#!/usr/bin/env python3
"""
Weimar Triangle tracker — issue-area re-sync.

Overwrites each enriched event's issue_areas from its extracted.topics (the
LLM-assigned topics). pipeline.enrich already sets issue_areas this way when it
writes an event, so this is a repair/backfill tool: use it to re-apply topic
tags to older events after editing the enrichment prompt, without re-calling
the model.

Does NOT touch any other fields (title, text, extracted, stances, etc.).

Usage:
    python -m pipeline.reclassify
    python -m pipeline.reclassify --dry-run          # show changes without writing
    python -m pipeline.reclassify --source polish_mfa
"""

from __future__ import annotations

import argparse
import glob
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

EVENTS_DIR = ROOT / "data" / "events"
ENRICHED_DIR = ROOT / "data" / "enriched"


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
    parser = argparse.ArgumentParser(description="Re-sync issue_areas from LLM topics")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--source", help="Limit to a single source name")
    args = parser.parse_args()

    if args.source:
        pattern = str(EVENTS_DIR / args.source / "*" / "*.yaml")
    else:
        pattern = str(EVENTS_DIR / "*" / "*" / "*.yaml")
    files = sorted(glob.glob(pattern))

    print(f"{'DRY RUN — ' if args.dry_run else ''}Re-syncing issue_areas for {len(files)} event files …")

    counts: Counter = Counter()
    for f in files:
        result = _sync_from_extracted(Path(f), args.dry_run)
        if result:
            print(result)
            counts["changed"] += 1
        else:
            counts["unchanged"] += 1

    action = "Would update" if args.dry_run else "Updated"
    print(f"\n{action} {counts['changed']} files  ({counts['unchanged']} unchanged)")


if __name__ == "__main__":
    main()
