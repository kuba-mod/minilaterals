#!/usr/bin/env python3
"""
One-time migration: drop stance ratings that are `score: 0` with no evidence.

Prompt versions up to "7" told the model to emit `stance 0, evidence null` when
it found nothing in a press release bearing on a grouping's goal for a topic.
That conflated two different things under one value: "this country took a
neutral position" (a rating) and "there is nothing here to rate" (an absence).
Stored as a real 0, the absence then fed the cluster mean — pulling convergence
toward Noncommittal on the strength of no evidence at all — and rendered as an
unauditable "+0 neutral" badge with no quote behind it.

Prompt "8" asks the model to omit such topics instead, and `_rate_stances`
drops any that slip through. This clears the ones already on disk so
`pipeline.enrich --stances-only` sees them as missing and re-rates them.

Nonzero scores with empty evidence are left alone: those are a real claim with
lost provenance (usually `_clean_evidence` dropping a quote the model copied
out of the goal statement), not an absence of stance.

Only supported as a module (python -m pipeline.migrate_drop_blank_neutrals) so
pipeline.schemas resolves without a sys.path shim.

Usage:
    python -m pipeline.migrate_drop_blank_neutrals            # rewrite in place
    python -m pipeline.migrate_drop_blank_neutrals --dry-run  # report, write nothing
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from pipeline.enrich import dump_yaml
from pipeline.schemas import EnrichedEventSchema

ROOT = Path(__file__).parent.parent
ENRICHED_DIR = ROOT / "data" / "enriched"


def _is_blank_neutral(entry) -> bool:
    return isinstance(entry, dict) and entry.get("score") == 0 and not (entry.get("evidence") or "").strip()


def drop_blank_neutrals(data: dict) -> tuple[dict, list[str]]:
    """Return (data with blank neutrals removed, ["grouping/topic", ...] dropped).

    Empty per-grouping blocks are removed too, and so is an `extracted.stances`
    left with nothing in it — an event with no ratings at all should look
    unrated to `--stances-only`, not like an empty rating set.
    """
    extracted = data.get("extracted")
    if not isinstance(extracted, dict):
        return data, []
    stances = extracted.get("stances")
    if not isinstance(stances, dict):
        return data, []

    dropped: list[str] = []
    kept_stances: dict = {}
    for grouping, topics in stances.items():
        if not isinstance(topics, dict):
            kept_stances[grouping] = topics
            continue
        kept = {t: e for t, e in topics.items() if not _is_blank_neutral(e)}
        dropped += [f"{grouping}/{t}" for t in topics if t not in kept]
        if kept:
            kept_stances[grouping] = kept

    if not dropped:
        return data, []

    updated_extracted = dict(extracted)
    if kept_stances:
        updated_extracted["stances"] = kept_stances
    else:
        updated_extracted.pop("stances", None)
    return {**data, "extracted": updated_extracted}, dropped


def main() -> None:
    parser = argparse.ArgumentParser(description="Drop evidence-less neutral stance ratings")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    changed = skipped = failed = total_dropped = 0
    for f in sorted(ENRICHED_DIR.glob("**/*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {f.relative_to(ROOT)}: {exc}")
            failed += 1
            continue
        if not data:
            continue

        updated, dropped = drop_blank_neutrals(data)
        if not dropped:
            skipped += 1
            continue

        try:
            EnrichedEventSchema.model_validate(updated)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {f.relative_to(ROOT)}: schema error after drop — {exc}")
            failed += 1
            continue

        print(f"  {'(dry) ' if args.dry_run else ''}- {f.relative_to(ROOT)} {dropped}")
        if not args.dry_run:
            f.write_text(dump_yaml(updated), encoding="utf-8")
        changed += 1
        total_dropped += len(dropped)

    print(f"\nDrop complete: {changed} files updated ({total_dropped} ratings), {skipped} unchanged, {failed} failed")
    if changed and not args.dry_run:
        print("Next: uv run python -m pipeline.enrich --stances-only  # re-rate the cleared topics")


if __name__ == "__main__":
    main()
