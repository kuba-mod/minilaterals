#!/usr/bin/env python3
"""
Weimar Triangle tracker — cluster commentary step.

For each convergence cluster that has extracted positions and a computed
similarity score, calls an LLM to write 1–2 sentences of plain-English
diplomatic commentary. Results are cached in data/commentary.json and
injected into the rendered site by pipeline/render.py.

Provider selection and env vars are identical to pipeline/enrich.py:
  ENRICH_PROVIDER=anthropic | ollama
  ANTHROPIC_API_KEY=sk-ant-...
  ANTHROPIC_MODEL=claude-haiku-4-5-20251001
  OLLAMA_HOST=http://localhost:11434
  OLLAMA_MODEL=gemma4:latest

Usage:
    python -m pipeline.comment               # generate for all pending clusters
    python -m pipeline.comment --dry-run     # print without writing
    python -m pipeline.comment --limit 5     # process at most 5 clusters
    python -m pipeline.comment --force       # regenerate even if cached
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

COMMENTARY_FILE = ROOT / "data" / "commentary.json"

SYSTEM_PROMPT = (
    "You are a concise diplomatic analyst. Write plain English — no bullet points, "
    "no headers, no jargon that needs explaining. Two sentences maximum."
)

COMMENTARY_PROMPT = """\
The following diplomatic positions were published by {countries} on the topic of \
{area} within a 14-day period:

{positions}

Their semantic alignment score is {score}% ({label}).

Write 1–2 sentences explaining what this means in concrete diplomatic terms. \
What specifically are they {alignment_verb}? Reference the actual policy content — \
do not just rephrase the score number."""

ACTOR_LABELS = {
    "DE": "Germany",
    "FR": "France",
    "PL": "Poland",
}


# ---------------------------------------------------------------------------
# Provider implementations — same interface as enrich.py
# ---------------------------------------------------------------------------

class OllamaProvider:
    def __init__(self, host: str, model: str, api_key: str = "ollama"):
        self.host = host.rstrip("/")
        self.model = model
        from openai import OpenAI
        # Set OLLAMA_API_KEY for Ollama Cloud (OLLAMA_HOST=https://ollama.com)
        self.client = OpenAI(base_url=f"{self.host}/v1", api_key=api_key)
        print(f"Provider: Ollama  host={self.host}  model={self.model}")

    def call(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            # Generous budget: thinking models like gemma4 burn tokens on reasoning
            # before writing the actual response; 4096 lets them finish both.
            max_tokens=4096,
            temperature=0.2,
        )
        msg = response.choices[0].message
        content = (msg.content or "").strip()
        if not content and getattr(msg, "reasoning", None):
            # Some thinking models put the final answer in reasoning when content
            # is suppressed; extract the last non-empty paragraph as the response.
            paragraphs = [p.strip() for p in msg.reasoning.split("\n\n") if p.strip()]
            content = paragraphs[-1] if paragraphs else ""
        return content


class AnthropicProvider:
    def __init__(self, api_key: str, model: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        print(f"Provider: Anthropic  model={self.model}")

    def call(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _load_env() -> None:
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


def cluster_key(cluster: dict) -> str:
    """Stable cache key: SHA256 of sorted event file paths in the cluster."""
    paths = sorted(
        item["event"]["_file_path"]
        for items in cluster["by_actor"].values()
        for item in items
        if item["event"].get("_file_path")
    )
    return hashlib.sha256(json.dumps(paths).encode()).hexdigest()[:12]


def _build_prompt(cluster: dict) -> str | None:
    """Build the commentary prompt. Returns None if positions or convergence are missing."""
    positions = []
    for actor in sorted(cluster["by_actor"]):
        label = ACTOR_LABELS.get(actor, actor)
        for item in cluster["by_actor"][actor]:
            pos = (item["event"].get("extracted") or {}).get("position")
            if pos:
                positions.append(f"{label}: {pos}")
                break  # one representative position per actor

    if len(positions) < 2:
        return None

    conv = cluster.get("convergence")
    if not conv:
        return None

    score = int(conv["overall"] * 100)
    label = conv["label"]
    alignment_verb = {
        "Converging": "converging",
        "Parallel": "pursuing parallel but distinct tracks",
        "Diverging": "diverging",
    }.get(label, "positioned differently")

    scoring_note = (
        "This score reflects how well each country aligns with the officially agreed "
        "Weimar Triangle goal on this topic, not just similarity to each other."
        if conv.get("scoring_mode") == "goal_anchored"
        else "This score reflects the semantic similarity between the countries' positions."
    )

    countries = " and ".join(ACTOR_LABELS.get(a, a) for a in sorted(cluster["actors"]))
    positions_text = "\n".join(positions)

    return COMMENTARY_PROMPT.format(
        countries=countries,
        area=cluster["area_label"],
        positions=positions_text,
        score=score,
        label=label,
        alignment_verb=alignment_verb,
    ) + f"\n\nNote: {scoring_note}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate cluster commentary")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    parser.add_argument("--limit", type=int, default=None, help="Max clusters to process")
    parser.add_argument("--force", action="store_true", help="Regenerate even if cached")
    args = parser.parse_args()

    from pipeline.render import (
        load_events,
        build_convergence_clusters,
        score_cluster_convergence,
        load_embeddings,
        load_goal_embeddings,
        load_position_embeddings,
    )

    events = load_events(weimar_only=True)
    emb_store = load_embeddings()
    goal_emb_store = load_goal_embeddings()
    pos_emb_store = load_position_embeddings()
    clusters = build_convergence_clusters(events)
    for c in clusters:
        c["convergence"] = score_cluster_convergence(c, emb_store, goal_emb_store, pos_emb_store)

    cache: dict[str, str] = {}
    if COMMENTARY_FILE.exists() and not args.force:
        cache = json.loads(COMMENTARY_FILE.read_text(encoding="utf-8"))

    provider = None
    processed = 0
    updated = False

    for cluster in clusters:
        if args.limit and processed >= args.limit:
            break

        key = cluster_key(cluster)
        if key in cache and not args.force:
            continue

        prompt = _build_prompt(cluster)
        if not prompt:
            continue

        if provider is None:
            provider = _build_provider()

        area = cluster["area_label"]
        actors = ", ".join(sorted(cluster["actors"]))
        print(f"\nCluster: {area} [{actors}]  key={key}")

        try:
            text = provider.call(prompt)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        print(f"  → {text}")

        if not text:
            print("  (empty response — skipping)")
            continue

        if not args.dry_run:
            cache[key] = text
            updated = True
        processed += 1

    if updated and not args.dry_run:
        COMMENTARY_FILE.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nWrote {len(cache)} entries to {COMMENTARY_FILE}")
    elif processed == 0:
        print("No pending clusters (all cached or no positions/embeddings available).")


if __name__ == "__main__":
    main()
