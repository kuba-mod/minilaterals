#!/usr/bin/env python3
"""
Weimar Triangle tracker — enrichment + classification step.

This is the sole categoriser: it processes EVERY raw event that lacks an
enriched sidecar and asks the LLM to decide, in one call, which Weimar
countries are involved, which issue areas the item touches, whether it is
relevant at all, and a structured position summary. The result is written to
data/enriched/. There is no keyword fallback — if the model is unreachable or
its output is unparseable, the event simply stays un-categorised and is retried
on the next run (or recovered by re-running this step against a local model).

Provider is selected via env var (or .env file):

  ENRICH_PROVIDER=ollama      # use local Ollama (default if no Anthropic key found)
  ENRICH_PROVIDER=anthropic   # use Claude Haiku via Anthropic API

Ollama settings (all optional):
  OLLAMA_HOST=http://localhost:11434   # default; https://ollama.com for Ollama Cloud
  OLLAMA_API_KEY=...                   # required for Ollama Cloud, unused locally
  OLLAMA_MODEL=gemma4:latest          # default; strong on French, German, Polish

Anthropic settings:
  ANTHROPIC_API_KEY=sk-ant-...
  ANTHROPIC_MODEL=claude-haiku-4-5-20251001   # default

Only supported as a module (python -m pipeline.enrich) — not as a direct
script (python pipeline/enrich.py) — so pipeline.sources.base resolves
without a sys.path shim.

Usage:
    python -m pipeline.enrich               # process all pending items
    python -m pipeline.enrich --limit 10    # process at most 10 items
    python -m pipeline.enrich --dry-run     # print what would be extracted, no writes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import anthropic
import yaml
from openai import OpenAI
from tqdm import tqdm

from pipeline.schemas import EnrichedEventSchema, ExtractedSchema
from pipeline.sources.base import KNOWN_ACTOR_SOURCES

ROOT = Path(__file__).parent.parent
EVENTS_DIR = ROOT / "data" / "events"
ENRICHED_DIR = ROOT / "data" / "enriched"
GROUPINGS_PATH = ROOT / "data" / "groupings.yaml"


def _load_config() -> dict:
    try:
        return yaml.safe_load(GROUPINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load_goals() -> dict[str, dict[str, str]]:
    """Reference goal sentences, keyed by grouping then topic.

    `defence` means something different to AUKUS than to the Weimar Triangle, so
    there is no single sentence per topic — which is why stances are keyed by
    (grouping, topic) rather than topic alone.
    """
    return {k: g["goals"] for k, g in _load_config().items() if isinstance(g, dict) and g.get("goals")}


GOALS = _load_goals()


class NoAliasDumper(yaml.SafeDumper):
    """Never emit YAML anchors/aliases.

    Two groupings can hold identical stance ratings for a topic — the same score
    and the same evidence quote — without those being the *same* rating. Left to
    itself PyYAML would collapse them into `&id001` / `*id001`, so re-rating one
    grouping would silently rewrite the other's. They are independent
    judgements against different goals, so they are written out independently.
    """

    def ignore_aliases(self, data):
        return True


def dump_yaml(data) -> str:
    return yaml.dump(data, Dumper=NoAliasDumper, allow_unicode=True, sort_keys=False)


class Grouping:
    """A minilateral: its member country codes and the issue areas it tracks."""

    def __init__(self, key: str, name: str, members: list[str], topics: list[str]):
        self.key = key
        self.name = name
        self.members = list(members)
        self.member_set = set(members)
        self.topics = set(topics)


def _load_groupings() -> dict[str, Grouping]:
    """The groupings the pipeline classifies against.

    The file also carries hub-page placeholders that nothing ingests yet;
    `topics` is what marks an entry as wired into the pipeline.
    Loading those too would widen the actor vocabulary and issue-area enum
    offered to the LLM, and emit a relevance flag per placeholder — see the
    file header.
    """
    raw = _load_config()
    return {
        key: Grouping(key, g.get("name", key), g.get("members", []), g["topics"])
        for key, g in raw.items()
        if isinstance(g, dict) and g.get("topics")
    }


GROUPINGS = _load_groupings()

# All country codes that appear in any grouping — the actor vocabulary.
ALL_MEMBERS = [c for g in GROUPINGS.values() for c in g.members]
# The global issue-area enum is the union of every grouping's tracked topics,
# ordered so that the six original Weimar areas come first.
_WEIMAR_TOPIC_ORDER = ["ukraine", "defence", "hybrid", "enlargement", "green_transition", "rule_of_law"]
ALL_TOPICS = _WEIMAR_TOPIC_ORDER + sorted({t for g in GROUPINGS.values() for t in g.topics} - set(_WEIMAR_TOPIC_ORDER))
# A legend of format key -> name (member codes), injected into the extraction
# prompt so the model has a concrete example per grouping to match against
# instead of just a bare list of keys.
FORMAT_HINTS_BLOCK = "\n".join(f"- {key}: {g.name} ({'/'.join(g.members)})" for key, g in GROUPINGS.items())

# Prompt revision stamped into each sidecar's `enriched_by.prompt_version`, so a
# score can be traced to the exact prompt that produced it. The lineage is keyed
# by the sha256[:8] of the prompt surface (the *_PROMPT / *_RUBRIC constants
# below), reconstructed from git history:
#   "1"  612a65fa  original — regex classification, LLM for positions/stances
#   "2"  da8777de  PR #35 — classification moved into the LLM prompt (actors)
#   "3"  c2cfff1e  actors/explicit_weimar shape hardening + retry
#   "4"  434962fe  native-language inputs (de/fr/pl); output English, evidence verbatim
#   "5"  c6353f81  multi-grouping: 12-country actors, topic union, explicit_formats
#   "6"  f5697563  explicit_formats legend + explicit-naming-vs-mere-involvement clarification
#   "7"  83172f45  per-grouping goals: extraction drops stances, one stance call
#                   per relevant grouping against that grouping's own goals
#   "8"  ee238d06  unrated ≠ neutral: omit a topic with no quotable stance
#                   instead of emitting stance 0 with null evidence
# BUMP PROMPT_VERSION and PROMPT_SURFACE_SHA together whenever the prompt surface
# changes — test_prompt_surface_in_sync fails until you do, so ratings never get
# mislabelled with a stale version. pipeline.migrate_provenance holds the full
# hash→version map for backfilling historical sidecars.
PROMPT_VERSION = "8"
PROMPT_SURFACE_SHA = "ee238d06"


def prompt_surface_sha() -> str:
    """sha256[:8] over the prompt strings sent to the model — the identity behind
    PROMPT_VERSION. Stable across runs; changes iff a prompt string changes."""
    parts = {
        "EXTRACTION_PROMPT": EXTRACTION_PROMPT,
        "STANCE_BACKFILL_PROMPT": STANCE_BACKFILL_PROMPT,
        "STANCE_RUBRIC": STANCE_RUBRIC,
        "SYSTEM_PROMPT": SYSTEM_PROMPT,
    }
    blob = "".join(f"{k}={parts[k]}" for k in sorted(parts))
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


def _environment() -> str:
    """Where this enrichment ran — GitHub Actions sets GITHUB_ACTIONS=true."""
    return "github_actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local"


SYSTEM_PROMPT = (
    "You extract structured diplomatic position summaries from government press releases. "
    "Return ONLY valid JSON — no markdown, no explanation, no wrapper text."
)

STANCE_RUBRIC = """\
Stance scale (integer, rating the country's stance against that grouping's goal for the topic):
 +2 = actively advances the goal: concrete commitments, resources, initiatives
      (e.g. "provides EUR 5bn in military aid", "will host a summit on X")
 +1 = supports the goal rhetorically, no new commitments
      (e.g. "reiterates continued support", "stresses the importance of X")
  0 = neutral: takes a position on the topic that neither advances nor
      undermines the goal (e.g. "notes the discussion took place")
 -1 = hedges or conditions the goal: partial support with significant caveats
      (e.g. "supports X but only if...", "questions the timeline")
 -2 = opposes or undermines the goal
      (e.g. "calls for a halt to weapons deliveries")

The "evidence" field MUST be a verbatim quote copied from the press release text
above — never from the goal statements. Every rating needs one, including a 0.

If the text contains no quote that supports ANY rating on a topic — including
because the text touches the topic but says nothing bearing on THIS grouping's
goal for it — OMIT that topic from your JSON entirely. Do not emit stance 0 for
it: 0 means "this country took a neutral position", not "I found nothing"."""

EXTRACTION_PROMPT = """\
Extract a structured summary from this press release.

The press release may be written in German, French, Polish, or English. Write
every output field in English, EXCEPT "evidence" fields, which must stay
verbatim quotes in the original language of the text.

Source country: {source}
Title: {title}
Text: {text}

Minilateral format keys (for "explicit_formats" below):
{format_hints_block}

Return JSON with exactly these fields:
{{
  "event_type": "one of: joint_statement, speech, meeting, communique, statement",
  "participants": ["list of named officials or roles mentioned"],
  "actors": ["a flat array of the country codes ({actor_codes}) that this item represents or discusses; [] if none; never nest arrays or add other values"],
  "explicit_formats": ["a flat array of format keys from the list above, ONLY if the text itself names that format (e.g. says 'the E3', 'AUKUS', 'Weimar Triangle') — NOT just because the format's member countries happen to be discussed; [] if the text never names a format by its own name"],
  "topics": ["list from: {topic_list}"],
  "location": "city and country if mentioned, else null",
  "position": "one sentence: overall position/action by {source}",
  "positions_by_topic": {{
    "<topic>": {{
      "position": "one sentence: what position {source} takes, or what action it announces, on this topic"
    }}
  }}
}}
Include in positions_by_topic ONLY the topics listed in "topics". Omit topics not mentioned."""

STANCE_BACKFILL_PROMPT = """\
Rate this government press release against the {grouping}'s policy goals.

These goals are specific to the {grouping}. The same topic can mean something
different to another grouping, so rate ONLY against the goals given below.

The press release may be written in a European language or English. The
"evidence" fields must stay verbatim quotes in the original language of the text.

Source country: {source}
Title: {title}
Text: {text}

{grouping} goals:
{goals_block}

{stance_rubric}

Rate {source}'s stance for each of these topics only: {topics}

Return JSON mapping each topic to a rating:
{{
  "<topic>": {{
    "stance": <integer -2 to +2 from the stance scale>,
    "evidence": "shortest verbatim quote from the text that justifies the rating"
  }}
}}"""

SOURCE_LABELS = {
    "german_mfa": "Germany",
    "france_diplomatie": "France",
    "polish_mfa": "Poland",
    "german_chancellery": "Germany",
    "elysee": "France",
    "polish_pm": "Poland",
    "uk_fcdo": "United Kingdom",
    "us_state": "United States",
    "australia_dfat": "Australia",
    "czech_mfa": "Czechia",
    "slovak_mfa": "Slovakia",
    "hungary_government": "Hungary",
    "estonian_mfa": "Estonia",
    "latvian_mfa": "Latvia",
    "lithuanian_mfa": "Lithuania",
}

# Country code for each known-actor source. The source country is always folded
# into the actor list for these sources (see KNOWN_ACTOR_SOURCES), so a single-country
# MFA/chancellery press release still counts its own country as an actor.
SOURCE_ACTOR = {
    "german_mfa": "DE",
    "france_diplomatie": "FR",
    "polish_mfa": "PL",
    "german_chancellery": "DE",
    "elysee": "FR",
    "polish_pm": "PL",
    "uk_fcdo": "UK",
    "us_state": "US",
    "australia_dfat": "AU",
    "czech_mfa": "CZ",
    "slovak_mfa": "SK",
    "hungary_government": "HU",
    "estonian_mfa": "EE",
    "latvian_mfa": "LV",
    "lithuanian_mfa": "LT",
}

# Canonical order for actor codes (deduped from the grouping members), and the
# aliases the model might return for each country.
_ACTOR_ORDER = list(dict.fromkeys(ALL_MEMBERS))
_ACTOR_ALIASES = {
    "DE": "DE",
    "GERMANY": "DE",
    "GERMAN": "DE",
    "FR": "FR",
    "FRANCE": "FR",
    "FRENCH": "FR",
    "PL": "PL",
    "POLAND": "PL",
    "POLISH": "PL",
    "UK": "UK",
    "GB": "UK",
    "UNITED KINGDOM": "UK",
    "BRITAIN": "UK",
    "GREAT BRITAIN": "UK",
    "BRITISH": "UK",
    "ENGLAND": "UK",
    "US": "US",
    "USA": "US",
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "AMERICA": "US",
    "AMERICAN": "US",
    "AU": "AU",
    "AUSTRALIA": "AU",
    "AUSTRALIAN": "AU",
    "CZ": "CZ",
    "CZECHIA": "CZ",
    "CZECH REPUBLIC": "CZ",
    "CZECH": "CZ",
    "SK": "SK",
    "SLOVAKIA": "SK",
    "SLOVAK": "SK",
    "HU": "HU",
    "HUNGARY": "HU",
    "HUNGARIAN": "HU",
    "EE": "EE",
    "ESTONIA": "EE",
    "ESTONIAN": "EE",
    "LV": "LV",
    "LATVIA": "LV",
    "LATVIAN": "LV",
    "LT": "LT",
    "LITHUANIA": "LT",
    "LITHUANIAN": "LT",
}


def _normalize_actors(raw_actors, source_name: str) -> list[str]:
    """Map the model's actor list to canonical country codes, folding in the
    source country for known-actor sources, returned in canonical order."""
    codes = set()
    for a in raw_actors or []:
        code = _ACTOR_ALIASES.get(str(a).strip().upper())
        if code:
            codes.add(code)
    if source_name in KNOWN_ACTOR_SOURCES:
        codes.add(SOURCE_ACTOR[source_name])
    return [c for c in _ACTOR_ORDER if c in codes]


def _as_bool(value) -> bool:
    """Coerce a model-returned flag to bool. Guards against the string "false",
    which is truthy under a plain bool() cast."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _normalize_formats(raw_formats) -> set[str]:
    """Map the model's explicit_formats list to known grouping keys (lowercased)."""
    out = set()
    for f in raw_formats or []:
        key = str(f).strip().lower()
        if key in GROUPINGS:
            out.add(key)
    return out


def _grouping_relevance(actors: list[str], explicit_formats: set[str], topics: list[str], source_name: str) -> dict:
    """Compute the per-grouping {key}_relevant flags. For each grouping, relevance is
    a fixed rule over the event's actors that are members of that grouping and the
    topics that grouping tracks (mirroring the original Weimar policy, but scoped to
    each grouping's membership so a widened actor vocabulary can't leak across
    formats): an explicit-format mention (or all members present), OR 2+ member
    actors on a tracked topic, OR a known-actor source that belongs to the grouping
    on a tracked topic. Every grouping — including weimar — uses the same flat
    {key}_relevant naming; there is no separate "strong signal" tier.
    """
    from_known_actor = source_name in KNOWN_ACTOR_SOURCES
    source_code = SOURCE_ACTOR.get(source_name)
    flags: dict[str, bool] = {}
    for key, g in GROUPINGS.items():
        present = [a for a in actors if a in g.member_set]
        gtopics = [t for t in topics if t in g.topics]
        explicit = key in explicit_formats or (len(present) == len(g.members) and len(g.members) > 0)
        flags[f"{key}_relevant"] = (
            explicit
            or (len(present) >= 2 and bool(gtopics))
            or (from_known_actor and source_code in g.member_set and bool(gtopics))
        )
    return flags


# ---------------------------------------------------------------------------
# Provider implementations — both expose the same call(prompt) -> str interface
# ---------------------------------------------------------------------------


class OllamaProvider:
    def __init__(self, host: str, model: str, api_key: str = "ollama"):
        self.host = host.rstrip("/")
        self.model = model

        # api_key is a placeholder for local Ollama; set OLLAMA_API_KEY for
        # Ollama Cloud (OLLAMA_HOST=https://ollama.com) — same model, same
        # ratings as the local setup.
        self.client = OpenAI(base_url=f"{self.host}/v1", api_key=api_key)
        print(f"Provider: Ollama  host={self.host}  model={self.model}")

    def call(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2048,
            temperature=0,
            response_format={"type": "json_object"},
            # Disable extended thinking mode — it's slow and wasteful for
            # structured extraction tasks.
            extra_body={"think": False},
        )
        return response.choices[0].message.content.strip()


class AnthropicProvider:
    def __init__(self, api_key: str, model: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        print(f"Provider: Anthropic  model={self.model}")

    def call(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _load_env() -> None:
    """Load .env file into os.environ (simple key=value only)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _build_provider():
    _load_env()
    provider = os.environ.get("ENRICH_PROVIDER", "").strip().lower()

    # Auto-detect: if Anthropic key present use it, else try Ollama
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not provider:
        provider = "anthropic" if anthropic_key else "ollama"

    if provider == "anthropic":
        if not anthropic_key:
            print("ERROR: ENRICH_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
            print("  Add ANTHROPIC_API_KEY=sk-ant-... to your .env file")
            sys.exit(1)
        model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        return AnthropicProvider(api_key=anthropic_key, model=model)

    if provider == "ollama":
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        model = os.environ.get("OLLAMA_MODEL", "gemma4:latest")
        api_key = os.environ.get("OLLAMA_API_KEY", "ollama")
        return OllamaProvider(host=host, model=model, api_key=api_key)

    print(f"ERROR: Unknown ENRICH_PROVIDER={provider!r}. Use 'ollama' or 'anthropic'.")
    sys.exit(1)


def _find_pending(limit: int | None = None) -> list[Path]:
    """Every raw event without an enriched sidecar is pending — the LLM sees
    everything and decides relevance itself. Newest first, so the events that
    drive the current edition's clusters and timeline are categorised soonest."""
    pending = []
    for f in sorted(EVENTS_DIR.glob("**/*.yaml"), key=lambda p: p.name, reverse=True):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not d:
            continue

        rel = f.relative_to(EVENTS_DIR)
        enriched_path = ENRICHED_DIR / rel
        if enriched_path.exists():
            try:
                enriched = yaml.safe_load(enriched_path.read_text(encoding="utf-8"))
                if enriched and enriched.get("extracted") is not None:
                    continue  # already enriched
            except Exception:
                pass

        pending.append(f)
        if limit and len(pending) >= limit:
            break
    return pending


def _clean_stance(value) -> int | None:
    """Coerce an LLM stance value to an int in [-2, 2], or None if not provided.

    A non-numeric/missing value is a normal "no stance given" case. A numeric
    value outside [-2, 2] means the model ignored the rubric, which is worth
    surfacing rather than silently clamping into range.
    """
    try:
        s = int(value)
    except (TypeError, ValueError):
        return None
    if not -2 <= s <= 2:
        raise ValueError(f"stance {s} out of range [-2, 2]")
    return s


def _clean_evidence(evidence, topic: str, grouping: str) -> str:
    """Drop evidence the model copied from the goal statement instead of the text."""
    ev = (evidence or "").strip()
    if not ev:
        return ""
    goal = GOALS.get(grouping, {}).get(topic, "")
    if ev and (ev in goal or goal.strip() in ev):
        return ""
    return ev


def _parse_json(raw: str) -> dict:
    # Strip markdown code fences some models add despite instructions
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def _validate_llm_shape(extracted: dict) -> None:
    """Reject actors/explicit_formats shapes the prompt didn't ask for, instead of
    letting _normalize_actors/_normalize_formats silently mis-parse them (observed:
    gemma4 occasionally nests "actors", e.g. [["FR"]] or ["FR", []], which a naive
    str()-based parse just drops with no error)."""
    actors = extracted.get("actors")
    if actors is not None and (not isinstance(actors, list) or any(not isinstance(a, str) for a in actors)):
        raise ValueError(f"actors must be a flat array of strings, got {actors!r}")
    formats = extracted.get("explicit_formats")
    if formats is not None and (not isinstance(formats, list) or any(not isinstance(f, str) for f in formats)):
        raise ValueError(f"explicit_formats must be a flat array of strings, got {formats!r}")


def _extract(provider, raw_path: Path) -> bool:
    data = yaml.safe_load(raw_path.read_text(encoding="utf-8"))
    source_name = data.get("source_name", "unknown")
    source_label = SOURCE_LABELS.get(source_name, source_name)

    # No goals or stance rubric here: extraction no longer rates stances, since
    # the goal a rating is measured against depends on the grouping (see
    # `_rate_stances`), which isn't known until relevance is computed.
    prompt = EXTRACTION_PROMPT.format(
        source=source_label,
        title=data.get("title", ""),
        text=data.get("text", "") or "",
        format_hints_block=FORMAT_HINTS_BLOCK,
        actor_codes=", ".join(_ACTOR_ORDER),
        topic_list=", ".join(ALL_TOPICS),
    )

    raw = ""
    try:
        extracted = None
        for attempt in range(2):
            raw = provider.call(prompt)
            try:
                extracted = _parse_json(raw)
                _validate_llm_shape(extracted)
                break
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt == 0:
                    print(f"  ~ retry {raw_path.name}: {exc}")
                else:
                    print(f"  ! invalid LLM output for {raw_path.name}: {exc} — raw: {raw[:120]}")
                    return False
        assert extracted is not None

        # Build per-topic positions. A topic listed in "topics" without a usable
        # position entry is treated as an extraction failure (see below). Stances
        # are NOT produced here — they need a grouping's own goals, and which
        # groupings apply isn't known until relevance is computed below.
        positions_by_topic = extracted.get("positions_by_topic") or {}
        extracted["positions"] = {}
        extracted["stances"] = {}

        llm_topics = [t for t in (extracted.get("topics") or []) if t != "other"]
        for topic in llm_topics:
            entry = positions_by_topic.get(topic)
            if not isinstance(entry, dict):
                raise ValueError(f"missing positions_by_topic entry for topic {topic!r}")
            pos_text = (entry.get("position") or "").strip()
            if not pos_text:
                raise ValueError(f"empty position for topic {topic!r}")
            extracted["positions"][topic] = pos_text

        if "positions_by_topic" in extracted:
            del extracted["positions_by_topic"]

        # Classify from the model's own reading of the item: which member
        # countries are involved, which minilateral formats it explicitly names,
        # and which issue areas it touches. Relevance is then a fixed rule over
        # those signals, computed per grouping (see _grouping_relevance) so a
        # widened actor vocabulary can't leak relevance across formats. Pulled out
        # of `extracted` (not part of ExtractedSchema) since they're promoted to
        # top-level enriched fields instead.
        actors = _normalize_actors(extracted.pop("actors", None), source_name)
        explicit_formats = _normalize_formats(extracted.pop("explicit_formats", None))
        ExtractedSchema.model_validate(extracted)
        relevance = _grouping_relevance(actors, explicit_formats, llm_topics, source_name)

        enriched_data = {
            "actors": actors,
            "issue_areas": llm_topics,
            **relevance,
            "extracted": extracted,
            "enriched_by": {
                "model_id": provider.model,
                "prompt_version": PROMPT_VERSION,
                "environment": _environment(),
            },
        }
        # One stance call per relevant grouping, each against that grouping's own
        # goals. Usually one; ~a quarter of events are relevant to two.
        for key in _relevant_groupings(relevance):
            topics = _stance_topics(key, llm_topics)
            if topics:
                rated = _rate_stances(provider, source_label, data.get("title", ""), data.get("text", ""), key, topics)
                if rated:
                    extracted["stances"][key] = rated

        EnrichedEventSchema.model_validate(enriched_data)

        rel = raw_path.relative_to(EVENTS_DIR)
        enriched_path = ENRICHED_DIR / rel
        enriched_path.parent.mkdir(parents=True, exist_ok=True)
        enriched_path.write_text(
            dump_yaml(enriched_data),
            encoding="utf-8",
        )
        matched = [k for k in GROUPINGS if relevance.get(f"{k}_relevant")]
        flag = ("+" + ",".join(matched)) if matched else "·"
        print(f"  + [{flag}] [{source_name}] {data.get('date')} actors={actors} topics={llm_topics}")
        return True
    except Exception as exc:
        print(f"  ! Error for {raw_path.name}: {exc}")
        return False


def _find_stance_pending(limit: int | None = None) -> list[Path]:
    """Enriched files that have positions but no stance ratings yet, newest first
    (recent events drive the visible clusters and timeline)."""
    pending = []
    for f in sorted(ENRICHED_DIR.glob("**/*.yaml"), key=lambda p: p.name, reverse=True):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        extracted = (d or {}).get("extracted") or {}
        topics = [t for t in (extracted.get("topics") or []) if t != "other"]
        stances = extracted.get("stances") or {}
        wanted = {key: _stance_topics(key, topics) for key in _relevant_groupings(d or {})}
        wanted = {k: v for k, v in wanted.items() if v}
        if not wanted:
            continue
        if all(all(t in (stances.get(k) or {}) for t in ts) for k, ts in wanted.items()):
            continue
        pending.append(f)
        if limit and len(pending) >= limit:
            break
    return pending


def _rate_stances(provider, source_label: str, title: str, text: str, grouping: str, topics: list[str]) -> dict:
    """Rate one grouping's topics against that grouping's own goals.

    Returns {topic: {score, evidence}}. Separate from extraction because the goal
    a stance is measured against only becomes unambiguous once relevance is
    known: an event relevant to both weimar and e3 gets one call per grouping,
    since "defence" asks a different question of each.
    """
    goals_block = "\n".join(f"- {t}: {GOALS[grouping][t].strip()}" for t in topics)
    prompt = STANCE_BACKFILL_PROMPT.format(
        source=source_label,
        title=title,
        text=text or "",
        grouping=GROUPINGS[grouping].name,
        goals_block=goals_block,
        stance_rubric=STANCE_RUBRIC,
        topics=", ".join(topics),
    )
    ratings = None
    for attempt in range(2):
        raw = provider.call(prompt)
        try:
            ratings = _parse_json(raw)
            break
        except json.JSONDecodeError as exc:
            if attempt == 0:
                print(f"  ~ retry stances [{grouping}]: {exc}")
            else:
                print(f"  ! JSON error rating [{grouping}]: {exc} — raw: {raw[:120]}")
                return {}
    out = {}
    for topic in topics:
        entry = ratings.get(topic) if isinstance(ratings, dict) else None
        if not isinstance(entry, dict):
            continue
        score = _clean_stance(entry.get("stance"))
        if score is None:
            continue
        evidence = _clean_evidence(entry.get("evidence"), topic, grouping)
        # A 0 with no quote behind it is the model saying "I found nothing to
        # rate", not "this country is neutral" — the rubric asks it to omit the
        # topic instead, and this is the net for when it doesn't. Storing it
        # would drag the cluster mean toward Noncommittal on the strength of an
        # absence, and render an unauditable "+0 neutral" badge. Dropping the
        # topic leaves it unrated, which `--stances-only` will retry.
        # A *nonzero* score with empty evidence is a different case — a real
        # claim with lost provenance — so it is kept.
        if score == 0 and not evidence:
            continue
        out[topic] = {"score": score, "evidence": evidence}
    return out


def _stance_topics(grouping: str, topics: list[str]) -> list[str]:
    """The subset of an event's topics that `grouping` actually tracks."""
    return [t for t in topics if t in GOALS.get(grouping, {})]


def _relevant_groupings(relevance: dict) -> list[str]:
    return [k for k in GROUPINGS if relevance.get(f"{k}_relevant")]


def _backfill_stances(provider, enriched_path: Path) -> str:
    """Add stance ratings to an already-enriched event, reading the raw text.

    Returns one of three outcomes, deliberately distinct: `"ok"` (ratings
    written), `"empty"` (the model found nothing quotable to rate — the normal
    outcome since prompt version "8", not a failure), or `"error"` (the raw
    event is missing, or the call/validation/write blew up). Only `"error"`
    should reach the exit code: a run that trips the healthcheck every day
    because some events genuinely carry no stance is an alert that means
    nothing."""
    enriched = yaml.safe_load(enriched_path.read_text(encoding="utf-8"))
    extracted = enriched.get("extracted") or {}
    topics = [t for t in (extracted.get("topics") or []) if t != "other"]

    raw_path = EVENTS_DIR / enriched_path.relative_to(ENRICHED_DIR)
    if not raw_path.exists():
        print(f"  ! No raw event for {enriched_path.name}")
        return "error"
    data = yaml.safe_load(raw_path.read_text(encoding="utf-8"))
    source_label = SOURCE_LABELS.get(data.get("source_name", ""), "unknown")

    stances = extracted.get("stances") or {}
    try:
        for key in _relevant_groupings(enriched):
            wanted = _stance_topics(key, topics)
            if not wanted or all(t in (stances.get(key) or {}) for t in wanted):
                continue
            rated = _rate_stances(provider, source_label, data.get("title", ""), data.get("text", ""), key, wanted)
            if rated:
                stances[key] = {**(stances.get(key) or {}), **rated}
        if not stances:
            print(f"  - No quotable stance in {enriched_path.name}")
            return "empty"

        extracted["stances"] = stances
        enriched["extracted"] = extracted
        # This run re-produced the stance ratings, so it owns the provenance.
        enriched["enriched_by"] = {
            "model_id": provider.model,
            "prompt_version": PROMPT_VERSION,
            "environment": _environment(),
        }
        EnrichedEventSchema.model_validate(enriched)
        enriched_path.write_text(
            dump_yaml(enriched),
            encoding="utf-8",
        )
        summary = "  ".join(f"{k}/{t}:{v['score']:+d}" for k, topics_ in stances.items() for t, v in topics_.items())
        print(f"  + [{data.get('source_name')}] {data.get('date')} {summary}")
        return "ok"
    except Exception as exc:
        print(f"  ! Error for {enriched_path.name}: {exc}")
        return "error"


def main() -> None:
    parser = argparse.ArgumentParser(description="Weimar tracker enrichment")
    parser.add_argument("--limit", type=int, default=None, help="Max items to process")
    parser.add_argument("--dry-run", action="store_true", help="Print without calling API")
    parser.add_argument(
        "--stances-only",
        action="store_true",
        help="Backfill stance ratings for already-enriched events that lack them",
    )
    args = parser.parse_args()

    if args.stances_only:
        pending = _find_stance_pending(limit=args.limit)
        print(f"Pending stance backfill: {len(pending)} items")
        if not pending:
            print("Nothing to do.")
            return
        if args.dry_run:
            for path in pending:
                print(f"  {path.relative_to(ENRICHED_DIR)}")
            return
        provider = _build_provider()
        counts = {"ok": 0, "empty": 0, "error": 0}
        for i, path in enumerate(tqdm(pending, desc="Stance backfill", unit="item")):
            counts[_backfill_stances(provider, path)] += 1
            if i < len(pending) - 1:
                time.sleep(0.2)
        print(
            f"\nStance backfill complete: {counts['ok']} ok, {counts['empty']} nothing to rate, {counts['error']} failed"
        )
        # Only real failures are failures. An event the model finds no quotable
        # stance in is the documented outcome of prompt version "8", and it stays
        # pending forever, so counting it here would trip the daily healthcheck
        # every run from now on.
        if counts["error"]:
            sys.exit(1)
        return

    pending = _find_pending(limit=args.limit)
    print(f"Pending enrichment: {len(pending)} items")

    if not pending:
        print("Nothing to do.")
        return

    if args.dry_run:
        for path in pending:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            source = SOURCE_LABELS.get(data.get("source_name", ""), "?")
            print(f"  [{source}] {data.get('date')} — {data.get('title', '')[:70]}")
        return

    provider = _build_provider()

    ok = failed = 0
    for i, path in enumerate(tqdm(pending, desc="Enriching", unit="item")):
        if _extract(provider, path):
            ok += 1
        else:
            failed += 1
        if i < len(pending) - 1:
            time.sleep(0.2)

    print(f"\nEnrichment complete: {ok} ok, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
