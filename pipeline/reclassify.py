#!/usr/bin/env python3
"""
Weimar Triangle tracker — re-classification step.

Two modes:

1. Default: re-runs Event.classify() on all event YAML files and writes back
   the classification fields (actors, issue_areas, weimar_relevant,
   trilateral_signal). Use after updating ISSUE_AREAS or
   COUNTRY_TERMS in pipeline/sources/base.py.

2. --from-extracted: overwrites issue_areas from extracted.topics for all
   already-enriched events. Use this to apply LLM-sourced topic tags to
   events that were enriched before this mode existed.

Does NOT touch any other fields (title, summary, extracted, etc.).

Only supported as a module (python -m pipeline.reclassify) — not as a direct
script (python pipeline/reclassify.py) — so pipeline.sources.base resolves
without a sys.path shim.

Usage:
    python -m pipeline.reclassify
    python -m pipeline.reclassify --dry-run          # show changes without writing
    python -m pipeline.reclassify --source polish_mfa
    python -m pipeline.reclassify --from-extracted   # sync issue_areas from LLM topics
    python -m pipeline.reclassify --from-extracted --dry-run
"""

from __future__ import annotations

import argparse
import glob
from collections import Counter
from pathlib import Path

import yaml

from pipeline.sources.base import Event

ROOT = Path(__file__).parent.parent
EVENTS_DIR = ROOT / "data" / "events"
ENRICHED_DIR = ROOT / "data" / "enriched"

CLASSIFICATION_FIELDS = {"actors", "issue_areas", "weimar_relevant", "trilateral_signal"}


def _reclassify_file(raw_path: Path, dry_run: bool) -> str | None:
    """Re-classify one event. Reads raw file, writes classification to enriched sidecar."""
    try:
        raw = yaml.safe_load(raw_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  SKIP {raw_path}: {exc}")
        return None

    if not raw:
        return None

    event = Event(
        source_name=raw.get("source_name", ""),
        title=raw.get("title", ""),
        text=raw.get("text", "") or "",
        source_url=raw.get("source_url", ""),
        source_lang=raw.get("source_lang", "en"),
        source_published_at=raw.get("source_published_at", ""),
        date=raw.get("date", ""),
    ).classify()

    rel = raw_path.relative_to(EVENTS_DIR)
    enriched_path = ENRICHED_DIR / rel

    # Load existing enriched data (or start fresh)
    enriched: dict = {}
    if enriched_path.exists():
        try:
            enriched = yaml.safe_load(enriched_path.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    changed = {}
    for field in CLASSIFICATION_FIELDS:
        old = enriched.get(field)
        new = getattr(event, field)
        if old != new:
            changed[field] = (old, new)

    if not changed:
        return None

    summary = "  " + raw_path.name
    for field, (old, new) in changed.items():
        summary += f"\n    {field}: {old!r} → {new!r}"

    if not dry_run:
        for field in CLASSIFICATION_FIELDS:
            enriched[field] = getattr(event, field)
        enriched_path.parent.mkdir(parents=True, exist_ok=True)
        enriched_path.write_text(yaml.dump(enriched, allow_unicode=True, sort_keys=False), encoding="utf-8")

    return summary


def _sync_from_extracted(raw_path: Path, dry_run: bool) -> str | None:
    """Overwrite issue_areas from extracted.topics in the enriched sidecar."""
    rel = raw_path.relative_to(EVENTS_DIR)
    enriched_path = ENRICHED_DIR / rel

    if not enriched_path.exists():
        return None

    try:
        enriched = yaml.safe_load(enriched_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  SKIP {enriched_path}: {exc}")
        return None

    if not enriched or not enriched.get("extracted"):
        return None

    llm_topics = list(enriched["extracted"].get("topics") or [])
    if not llm_topics:
        return None

    old = enriched.get("issue_areas")
    if old == llm_topics:
        return None

    summary = f"  {raw_path.name}\n    issue_areas: {old!r} → {llm_topics!r}"

    if not dry_run:
        enriched["issue_areas"] = llm_topics
        enriched_path.write_text(yaml.dump(enriched, allow_unicode=True, sort_keys=False), encoding="utf-8")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-classify all event YAML files")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--source", help="Limit to a single source name")
    parser.add_argument(
        "--from-extracted",
        action="store_true",
        help="Sync issue_areas from extracted.topics (LLM topics) instead of re-running classify()",
    )
    args = parser.parse_args()

    if args.source:
        pattern = str(EVENTS_DIR / args.source / "*" / "*.yaml")
        files = sorted(glob.glob(pattern))
    else:
        pattern = str(EVENTS_DIR / "*" / "*" / "*.yaml")
        files = sorted(glob.glob(pattern))

    mode = "from-extracted" if args.from_extracted else "regex classify()"
    print(f"{'DRY RUN — ' if args.dry_run else ''}Re-classifying {len(files)} event files (mode: {mode}) …")

    counts: Counter = Counter()
    for f in files:
        if args.from_extracted:
            result = _sync_from_extracted(Path(f), args.dry_run)
        else:
            result = _reclassify_file(Path(f), args.dry_run)
        if result:
            print(result)
            counts["changed"] += 1
        else:
            counts["unchanged"] += 1

    action = "Would update" if args.dry_run else "Updated"
    print(f"\n{action} {counts['changed']} files  ({counts['unchanged']} unchanged)")


if __name__ == "__main__":
    main()
