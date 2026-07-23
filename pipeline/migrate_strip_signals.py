#!/usr/bin/env python3
"""
One-time migration: strip the obsolete per-grouping "signal" fields from
existing enriched sidecars.

The two-tier relevant/signal design (a broad {key}_relevant plus a stronger
{key}_signal) was simplified to a single {key}_relevant flag per grouping — see
EnrichedEventSchema in schemas.py. This removes the now-unused
trilateral_signal, e3_signal, visegrad_signal, baltic_signal, and aukus_signal
fields from every data/enriched/ sidecar; the {key}_relevant fields are
untouched.

Only supported as a module (python -m pipeline.migrate_strip_signals) so
pipeline.schemas resolves without a sys.path shim.

Usage:
    python -m pipeline.migrate_strip_signals            # rewrite in place
    python -m pipeline.migrate_strip_signals --dry-run  # report, write nothing
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from pipeline.schemas import EnrichedEventSchema

ROOT = Path(__file__).parent.parent
ENRICHED_DIR = ROOT / "data" / "enriched"

OBSOLETE_FIELDS = [
    "trilateral_signal",
    "e3_signal",
    "visegrad_signal",
    "baltic_signal",
    "aukus_signal",
]


def _without_signals(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in OBSOLETE_FIELDS}


def main() -> None:
    parser = argparse.ArgumentParser(description="Strip obsolete per-grouping signal fields")
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

        updated = _without_signals(data)
        if updated == data:
            skipped += 1
            continue

        try:
            EnrichedEventSchema.model_validate(updated)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {f.relative_to(ROOT)}: schema error after strip — {exc}")
            failed += 1
            continue

        removed = [k for k in OBSOLETE_FIELDS if k in data]
        print(f"  {'(dry) ' if args.dry_run else ''}- {f.relative_to(ROOT)} {removed}")
        if not args.dry_run:
            f.write_text(yaml.dump(updated, allow_unicode=True, sort_keys=False), encoding="utf-8")
        changed += 1

    print(f"\nStrip complete: {changed} updated, {skipped} unchanged, {failed} failed")


if __name__ == "__main__":
    main()
