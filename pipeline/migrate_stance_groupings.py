#!/usr/bin/env python3
"""
One-off, LLM-free migration: re-key `extracted.stances` by grouping.

Stances used to be `{topic: {score, evidence}}` — one rating per topic, made
against a single sentence shared by every grouping that tracked it. Goals are
now per grouping (`defence` asks a different question of AUKUS than of the
Weimar Triangle), so stances are `{grouping: {topic: {score, evidence}}}`.

The old rating is copied to every relevant grouping that tracks the topic. That
is faithful to what it asserted: the sentence it was scored against really was
shared, so it answered all of those groupings' questions at once. It is not a
substitute for re-rating — the copies are identical where the new goals differ.
Re-rate with `pipeline.enrich --stances-only`, which now fills in each
(grouping, topic) pair separately.

Events whose stance topic is tracked by none of their relevant groupings lose
that rating: nothing would ever have scored it.

Usage:
    python -m pipeline.migrate_stance_groupings --dry-run
    python -m pipeline.migrate_stance_groupings
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import yaml

from pipeline.enrich import GOALS, GROUPINGS

ROOT = Path(__file__).parent.parent
ENRICHED_DIR = ROOT / "data" / "enriched"


def regroup(enriched: dict) -> dict | None:
    """Return the new stances block, or None if the file needs no change."""
    extracted = enriched.get("extracted") or {}
    stances = extracted.get("stances") or {}
    if not stances:
        return None
    # Already migrated: values are {topic: stance} rather than {score, evidence}.
    if all(isinstance(v, dict) and "score" not in v for v in stances.values()):
        return None

    relevant = [k for k in GROUPINGS if enriched.get(f"{k}_relevant")]
    out: dict[str, dict] = {}
    for topic, rating in stances.items():
        if not isinstance(rating, dict) or "score" not in rating:
            continue
        for key in relevant:
            if topic in GOALS.get(key, {}):
                out.setdefault(key, {})[topic] = rating
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-key stances by grouping")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    changed = 0
    dropped = 0
    per_grouping: Counter[str] = Counter()
    for path in sorted(ENRICHED_DIR.glob("**/*.yaml")):
        enriched = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(enriched, dict):
            continue
        new = regroup(enriched)
        if new is None:
            continue
        before = len(enriched["extracted"]["stances"])
        after = sum(len(v) for v in new.values())
        if after < before:
            dropped += before - after
        for key, topics in new.items():
            per_grouping[key] += len(topics)
        changed += 1
        if not args.dry_run:
            enriched["extracted"]["stances"] = new
            path.write_text(
                yaml.dump(enriched, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

    verb = "would rewrite" if args.dry_run else "rewrote"
    print(f"{verb} {changed} file(s)")
    print(f"  ratings by grouping: {dict(per_grouping.most_common())}")
    print(f"  ratings dropped (topic tracked by no relevant grouping): {dropped}")


if __name__ == "__main__":
    main()
