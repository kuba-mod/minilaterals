#!/usr/bin/env python3
"""
Backfill the per-grouping relevance flags onto existing enriched sidecars.

When the pipeline gained the additional minilaterals (E3, Visegrád Group,
Baltic Three, AUKUS), every event enriched before that change lacked the new
{key}_relevant / {key}_signal fields. Relevance is a pure function of the
already-stored `actors` + `issue_areas` + the source's country, so those flags
can be recomputed here **without re-running the LLM** — this is a cheap,
deterministic pass over data/enriched/, not a re-enrichment.

The legacy Weimar fields (weimar_relevant / trilateral_signal) are left exactly
as they were: old sidecars only ever carried DE/FR/PL actors, so the original
Weimar computation is still correct for them. The `explicit_formats` signal
isn't recoverable from an old sidecar (it wasn't captured), so it defaults to
empty here — a non-Weimar grouping only gains a `_signal` from an all-members-
present event, which historical DE/FR/PL-only data never satisfies anyway.

Only supported as a module (python -m pipeline.migrate_groupings) so
pipeline.enrich / pipeline.sources resolve without a sys.path shim.

Usage:
    python -m pipeline.migrate_groupings            # rewrite in place
    python -m pipeline.migrate_groupings --dry-run  # report, write nothing
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from pipeline.enrich import GROUPINGS, _grouping_relevance
from pipeline.schemas import EnrichedEventSchema

ROOT = Path(__file__).parent.parent
ENRICHED_DIR = ROOT / "data" / "enriched"
EVENTS_DIR = ROOT / "data" / "events"

# The new (non-Weimar) flag names this migration is responsible for.
NEW_FLAGS = [f"{k}_{suffix}" for k in GROUPINGS if k != "weimar" for suffix in ("relevant", "signal")]


def _source_name(enriched_path: Path, data: dict) -> str:
    """The enriched tree mirrors the events tree, so the source is the first
    path segment under data/enriched/ (also recoverable from the raw event)."""
    rel = enriched_path.relative_to(ENRICHED_DIR)
    return rel.parts[0] if rel.parts else data.get("source_name", "unknown")


def _with_new_flags(data: dict, source_name: str) -> dict:
    """Return a copy of the sidecar dict with the non-Weimar grouping flags
    (re)computed and inserted right after trilateral_signal, preserving order."""
    actors = list(data.get("actors") or [])
    topics = list(data.get("issue_areas") or [])
    flags = _grouping_relevance(actors, set(), topics, source_name)
    new = {k: flags[k] for k in NEW_FLAGS}

    out: dict = {}
    for key, value in data.items():
        if key in NEW_FLAGS:
            continue  # drop any stale copy; re-inserted in canonical position
        out[key] = value
        if key == "trilateral_signal":
            out.update(new)
    # If the sidecar predates trilateral_signal entirely, append at the end.
    if "trilateral_signal" not in data:
        for k, v in new.items():
            out.setdefault(k, v)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill per-grouping relevance flags")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    files = sorted(ENRICHED_DIR.glob("**/*.yaml"))
    changed = skipped = failed = 0
    for f in files:
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {f.relative_to(ROOT)}: {exc}")
            failed += 1
            continue
        if not data:
            continue

        source_name = _source_name(f, data)
        updated = _with_new_flags(data, source_name)
        if updated == data:
            skipped += 1
            continue

        try:
            EnrichedEventSchema.model_validate(updated)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {f.relative_to(ROOT)}: schema error after backfill — {exc}")
            failed += 1
            continue

        matched = [k for k in NEW_FLAGS if updated.get(k)]
        print(f"  {'(dry) ' if args.dry_run else ''}+ {f.relative_to(ROOT)} {matched or '—'}")
        if not args.dry_run:
            f.write_text(yaml.dump(updated, allow_unicode=True, sort_keys=False), encoding="utf-8")
        changed += 1

    print(f"\nBackfill complete: {changed} updated, {skipped} unchanged, {failed} failed")


if __name__ == "__main__":
    main()
