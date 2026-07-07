#!/usr/bin/env python3
"""
Weimar Triangle tracker — embedding step.

Computes sentence embeddings for extracted position summaries and stores them
in data/embeddings.json (keyed by event file path relative to repo root).

Runs after pipeline.enrich. Only processes events that have an extracted.position
but are not yet in embeddings.json. Use --recompute to re-embed everything.

Model: all-MiniLM-L6-v2 (22 MB, 384-dim, no API key required, runs in CI).

Usage:
    python -m pipeline.embed
    python -m pipeline.embed --recompute
    python -m pipeline.embed --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

EVENTS_DIR = ROOT / "data" / "events"
ENRICHED_DIR = ROOT / "data" / "enriched"
EMBEDDINGS_FILE = ROOT / "data" / "embeddings.json"
POSITION_EMBEDDINGS_FILE = ROOT / "data" / "position_embeddings.json"
GOAL_EMBEDDINGS_FILE = ROOT / "data" / "goal_embeddings.json"
GOALS_FILE = ROOT / "data" / "weimar_goals.yaml"
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def _load_embeddings() -> dict[str, list[float]]:
    if EMBEDDINGS_FILE.exists():
        return json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_embeddings(store: dict[str, list[float]]) -> None:
    EMBEDDINGS_FILE.write_text(
        json.dumps(store, separators=(",", ":")),
        encoding="utf-8",
    )


def _embed_text(d: dict) -> str:
    """Full text to embed: title + text (full announcement, not LLM extraction)."""
    title = d.get("title", "").strip()
    text = (d.get("text", "") or "").strip()
    return f"{title}\n\n{text}" if text else title


def embed_goals(recompute: bool = False) -> dict[str, list[float]]:
    """Embed official Weimar goal sentences; write data/goal_embeddings.json."""
    if not GOALS_FILE.exists():
        return {}
    goals: dict[str, str] = yaml.safe_load(GOALS_FILE.read_text(encoding="utf-8")) or {}
    existing: dict[str, list[float]] = {}
    if GOAL_EMBEDDINGS_FILE.exists() and not recompute:
        existing = json.loads(GOAL_EMBEDDINGS_FILE.read_text(encoding="utf-8"))
        if all(k in existing for k in goals):
            return existing
    pending = [k for k in goals if k not in existing or recompute]
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)
    vectors = model.encode([goals[k] for k in pending], show_progress_bar=False, normalize_embeddings=True)
    for key, vec in zip(pending, vectors, strict=True):
        existing[key] = vec.tolist()
    GOAL_EMBEDDINGS_FILE.write_text(json.dumps(existing, separators=(",", ":")), encoding="utf-8")
    print(f"Saved goal embeddings → {GOAL_EMBEDDINGS_FILE.relative_to(ROOT)}")
    return existing


def embed_positions(recompute: bool = False) -> dict[str, list[float]]:
    """Embed extracted.position summaries per topic; write data/position_embeddings.json.

    Keyed by composite keys: '{filepath}#{topic}' where filepath is the raw events path.
    Falls back to overall position (stored as '{filepath}#overall') for backward compat.
    Only events that have been through pipeline.enrich are included.
    """
    existing: dict[str, list[float]] = {}
    if POSITION_EMBEDDINGS_FILE.exists() and not recompute:
        existing = json.loads(POSITION_EMBEDDINGS_FILE.read_text(encoding="utf-8"))

    pending: list[tuple[str, str]] = []
    for f in sorted(ENRICHED_DIR.glob("**/*.yaml")):
        try:
            enriched = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        extracted = (enriched or {}).get("extracted", {})
        if not extracted:
            continue
        # Key uses the raw events path (not enriched), for embedding store consistency
        rel = str((EVENTS_DIR / f.relative_to(ENRICHED_DIR)).relative_to(ROOT))

        # Per-topic positions (new format)
        positions = extracted.get("positions", {})
        for topic, position_text in (positions or {}).items():
            if not position_text or not position_text.strip():
                continue
            key = f"{rel}#{topic}"
            if recompute or key not in existing:
                pending.append((key, position_text.strip()))

        # Fallback: overall position (for backward compat with old enrichments)
        if not positions:
            position = extracted.get("position")
            if position and position.strip():
                key = f"{rel}#overall"
                if recompute or key not in existing:
                    pending.append((key, position.strip()))

    if not pending:
        print(f"Position embeddings up to date  (stored: {len(existing)})")
        return existing

    print(f"Embedding {len(pending)} per-topic positions …")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)
    vectors = model.encode([t for _, t in pending], show_progress_bar=False, normalize_embeddings=True)
    for (key, _), vec in zip(pending, vectors, strict=True):
        existing[key] = vec.tolist()
    POSITION_EMBEDDINGS_FILE.write_text(json.dumps(existing, separators=(",", ":")), encoding="utf-8")
    print(f"Saved position embeddings → {POSITION_EMBEDDINGS_FILE.relative_to(ROOT)}  (total: {len(existing)})")
    return existing


def _find_pending(store: dict, recompute: bool) -> list[tuple[str, str]]:
    """Return list of (rel_path, text) for weimar_relevant events needing embedding."""
    pending = []
    for f in sorted(EVENTS_DIR.glob("**/*.yaml")):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not d:
            continue
        # weimar_relevant lives in the enriched sidecar
        rel_from_events = f.relative_to(EVENTS_DIR)
        enriched_path = ENRICHED_DIR / rel_from_events
        if not enriched_path.exists():
            continue
        try:
            enriched = yaml.safe_load(enriched_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not enriched or not enriched.get("weimar_relevant"):
            continue
        text = _embed_text(d)
        if not text:
            continue
        rel = str(f.relative_to(ROOT))
        if recompute or rel not in store:
            pending.append((rel, text))
    return pending


def main() -> None:
    parser = argparse.ArgumentParser(description="Weimar tracker embedding step")
    parser.add_argument("--recompute", action="store_true", help="Re-embed all events")
    parser.add_argument("--dry-run", action="store_true", help="Print pending without computing")
    args = parser.parse_args()

    store = _load_embeddings()
    pending = _find_pending(store, args.recompute)

    print(f"Pending embeddings: {len(pending)}  (stored: {len(store)})")
    if not pending:
        print("Nothing to embed.")
        if not args.dry_run:
            embed_positions(recompute=args.recompute)
            embed_goals(recompute=args.recompute)
        return

    if args.dry_run:
        for rel, _ in pending:
            print(f"  {rel}")
        return

    print(f"Loading model {MODEL_NAME!r} …")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)

    texts = [text for _, text in pending]
    print(f"Embedding {len(texts)} announcements …")
    vectors = model.encode(texts, show_progress_bar=True, normalize_embeddings=True)

    for (rel, _), vec in zip(pending, vectors, strict=True):
        store[rel] = vec.tolist()

    _save_embeddings(store)
    print(f"Saved → {EMBEDDINGS_FILE.relative_to(ROOT)}  (total: {len(store)} entries)")
    embed_positions(recompute=args.recompute)
    embed_goals(recompute=args.recompute)


if __name__ == "__main__":
    main()
