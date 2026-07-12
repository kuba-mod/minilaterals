#!/usr/bin/env python3
"""
Weimar Triangle tracker — enrichment step.

Finds weimar_relevant YAML events without an 'extracted' block and calls
an LLM to extract a structured position summary. Writes the result back to
the YAML file in-place.

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

Usage:
    python -m pipeline.enrich               # process all pending items
    python -m pipeline.enrich --limit 10    # process at most 10 items
    python -m pipeline.enrich --dry-run     # print what would be extracted, no writes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import anthropic
import yaml
from openai import OpenAI
from tqdm import tqdm

from pipeline.sources.base import Event

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

EVENTS_DIR = ROOT / "data" / "events"
ENRICHED_DIR = ROOT / "data" / "enriched"
GOALS_PATH = ROOT / "data" / "weimar_goals.yaml"


def _load_goals() -> dict[str, str]:
    try:
        return yaml.safe_load(GOALS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


WEIMAR_GOALS = _load_goals()

SYSTEM_PROMPT = (
    "You extract structured diplomatic position summaries from government press releases. "
    "Return ONLY valid JSON — no markdown, no explanation, no wrapper text."
)

STANCE_RUBRIC = """\
Stance scale (integer, rating the country's stance against the Weimar goal for that topic):
 +2 = actively advances the goal: concrete commitments, resources, initiatives
      (e.g. "provides EUR 5bn in military aid", "will host a summit on X")
 +1 = supports the goal rhetorically, no new commitments
      (e.g. "reiterates continued support", "stresses the importance of X")
  0 = neutral: merely mentions the topic without taking a stance
 -1 = hedges or conditions the goal: partial support with significant caveats
      (e.g. "supports X but only if...", "questions the timeline")
 -2 = opposes or undermines the goal
      (e.g. "calls for a halt to weapons deliveries")

The "evidence" field MUST be a verbatim quote copied from the press release text
above — never from the goal statements. If the text contains no quote that supports
a stance on a topic, use stance 0 and evidence null."""

EXTRACTION_PROMPT = """\
Extract a structured summary from this press release.

Source country: {source}
Title: {title}
Text: {text}

Weimar Triangle goals — frame topic entries against these:
{goals_block}

{stance_rubric}

Return JSON with exactly these fields:
{{
  "event_type": "one of: joint_statement, speech, meeting, communique, statement",
  "participants": ["list of named officials or roles mentioned"],
  "topics": ["list from: ukraine, defence, hybrid, enlargement, green_transition, rule_of_law"],
  "location": "city and country if mentioned, else null",
  "position": "one sentence: overall position/action by {source}",
  "positions_by_topic": {{
    "<topic>": {{
      "position": "one sentence: how {source}'s stance advances, aligns with, or departs from the Weimar goal for this topic",
      "stance": <integer -2 to +2 from the stance scale>,
      "evidence": "shortest verbatim quote from the text that justifies the stance rating"
    }}
  }}
}}
Include in positions_by_topic ONLY the topics listed in "topics". Omit topics not mentioned."""

STANCE_BACKFILL_PROMPT = """\
Rate this government press release against shared Weimar Triangle policy goals.

Source country: {source}
Title: {title}
Text: {text}

Weimar Triangle goals:
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
}


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
    pending = []
    for f in sorted(EVENTS_DIR.glob("**/*.yaml")):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not d:
            continue

        event = Event(
            source_name=d.get("source_name", ""),
            title=d.get("title", ""),
            text=d.get("text", "") or "",
            source_url=d.get("source_url", ""),
            source_lang=d.get("source_lang", "en"),
            source_published_at=d.get("source_published_at", ""),
            date=d.get("date", ""),
        ).classify()

        if not event.weimar_relevant:
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
    """Coerce an LLM stance value to an int in [-2, 2], or None if unusable."""
    try:
        s = int(value)
    except (TypeError, ValueError):
        return None
    return max(-2, min(2, s))


def _clean_evidence(evidence, topic: str) -> str:
    """Drop evidence the model copied from the goal statement instead of the text."""
    ev = (evidence or "").strip()
    if not ev:
        return ""
    goal = WEIMAR_GOALS.get(topic, "")
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


def _extract(provider, raw_path: Path) -> bool:
    data = yaml.safe_load(raw_path.read_text(encoding="utf-8"))
    source_name = data.get("source_name", "unknown")
    source_label = SOURCE_LABELS.get(source_name, source_name)

    goals_block = "\n".join(f"- {topic}: {text.strip()}" for topic, text in WEIMAR_GOALS.items())
    prompt = EXTRACTION_PROMPT.format(
        source=source_label,
        title=data.get("title", "")[:300],
        text=(data.get("text", "") or "")[:3000],
        goals_block=goals_block,
        stance_rubric=STANCE_RUBRIC,
    )

    raw = ""
    try:
        extracted = None
        for attempt in range(2):
            raw = provider.call(prompt)
            try:
                extracted = _parse_json(raw)
                break
            except json.JSONDecodeError as exc:
                if attempt == 0:
                    print(f"  ~ retry {raw_path.name}: {exc}")
                else:
                    print(f"  ! JSON error for {raw_path.name}: {exc} — raw: {raw[:120]}")
                    return False
        assert extracted is not None

        # Build per-topic positions + stances: topic-specific text (fallback to
        # overall position sentence) and a -2..+2 stance rating with evidence quote.
        positions_by_topic = extracted.get("positions_by_topic") or {}
        overall_position = extracted.get("position", "")
        extracted["positions"] = {}
        extracted["stances"] = {}

        llm_topics = [t for t in (extracted.get("topics") or []) if t != "other"]
        for topic in llm_topics:
            entry = positions_by_topic.get(topic)
            if isinstance(entry, dict):
                pos_text = (entry.get("position") or "").strip()
                stance = _clean_stance(entry.get("stance"))
                if stance is not None:
                    extracted["stances"][topic] = {
                        "score": stance,
                        "evidence": _clean_evidence(entry.get("evidence"), topic),
                    }
            else:
                # Older prompt shape: plain string per topic, no stance
                pos_text = (entry or "").strip() if isinstance(entry, str) else ""
            extracted["positions"][topic] = pos_text if pos_text else overall_position

        if "positions_by_topic" in extracted:
            del extracted["positions_by_topic"]

        # Re-run classify() to get fresh classification fields, then let LLM topics override
        event = Event(
            source_name=source_name,
            title=data.get("title", ""),
            text=data.get("text", "") or "",
            source_url=data.get("source_url", ""),
            source_lang=data.get("source_lang", "en"),
            source_published_at=data.get("source_published_at", ""),
            date=data.get("date", ""),
        ).classify()

        enriched_data = {
            "actors": event.actors,
            "issue_areas": llm_topics if llm_topics else event.issue_areas,
            "weimar_relevant": event.weimar_relevant,
            "trilateral_signal": event.trilateral_signal,
            "weimar_score": event.weimar_score,
            "extracted": extracted,
        }

        rel = raw_path.relative_to(EVENTS_DIR)
        enriched_path = ENRICHED_DIR / rel
        enriched_path.parent.mkdir(parents=True, exist_ok=True)
        enriched_path.write_text(
            yaml.dump(enriched_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(
            f"  + [{source_name}] {data.get('date')} "
            f"topics={extracted.get('topics', [])} | "
            f"positions={list(extracted.get('positions', {}).keys())}"
        )
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
        topics = [t for t in (extracted.get("topics") or []) if t in WEIMAR_GOALS]
        if not topics:
            continue
        stances = extracted.get("stances") or {}
        if all(t in stances for t in topics):
            continue
        pending.append(f)
        if limit and len(pending) >= limit:
            break
    return pending


def _backfill_stances(provider, enriched_path: Path) -> bool:
    """Add stance ratings to an already-enriched event, reading the raw text."""
    enriched = yaml.safe_load(enriched_path.read_text(encoding="utf-8"))
    extracted = enriched.get("extracted") or {}
    topics = [t for t in (extracted.get("topics") or []) if t in WEIMAR_GOALS]

    raw_path = EVENTS_DIR / enriched_path.relative_to(ENRICHED_DIR)
    if not raw_path.exists():
        print(f"  ! No raw event for {enriched_path.name}")
        return False
    data = yaml.safe_load(raw_path.read_text(encoding="utf-8"))
    source_label = SOURCE_LABELS.get(data.get("source_name", ""), "unknown")

    goals_block = "\n".join(f"- {t}: {WEIMAR_GOALS[t].strip()}" for t in topics)
    prompt = STANCE_BACKFILL_PROMPT.format(
        source=source_label,
        title=data.get("title", "")[:300],
        text=(data.get("text", "") or "")[:3000],
        goals_block=goals_block,
        stance_rubric=STANCE_RUBRIC,
        topics=", ".join(topics),
    )

    try:
        ratings = None
        for attempt in range(2):
            raw = provider.call(prompt)
            try:
                ratings = _parse_json(raw)
                break
            except json.JSONDecodeError as exc:
                if attempt == 0:
                    print(f"  ~ retry {enriched_path.name}: {exc}")
                else:
                    print(f"  ! JSON error for {enriched_path.name}: {exc} — raw: {raw[:120]}")
                    return False
        assert ratings is not None

        stances = extracted.get("stances") or {}
        for topic in topics:
            entry = ratings.get(topic)
            if not isinstance(entry, dict):
                continue
            stance = _clean_stance(entry.get("stance"))
            if stance is None:
                continue
            stances[topic] = {
                "score": stance,
                "evidence": _clean_evidence(entry.get("evidence"), topic),
            }
        if not stances:
            print(f"  ! No usable ratings for {enriched_path.name}")
            return False

        extracted["stances"] = stances
        enriched["extracted"] = extracted
        enriched_path.write_text(
            yaml.dump(enriched, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        summary = "  ".join(f"{t}:{s['score']:+d}" for t, s in stances.items())
        print(f"  + [{data.get('source_name')}] {data.get('date')} {summary}")
        return True
    except Exception as exc:
        print(f"  ! Error for {enriched_path.name}: {exc}")
        return False


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
        ok = failed = 0
        for i, path in enumerate(tqdm(pending, desc="Stance backfill", unit="item")):
            if _backfill_stances(provider, path):
                ok += 1
            else:
                failed += 1
            if i < len(pending) - 1:
                time.sleep(0.2)
        print(f"\nStance backfill complete: {ok} ok, {failed} failed")
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


if __name__ == "__main__":
    main()
