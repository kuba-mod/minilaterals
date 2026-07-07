#!/usr/bin/env python3
"""
Weimar Triangle tracker — ingestion runner.

Usage:
    python -m pipeline.ingest                     # run all sources
    python -m pipeline.ingest --source german_mfa
    python -m pipeline.ingest --dry-run           # fetch and classify, don't write files
    python -m pipeline.ingest --weimar-only       # print only Weimar-relevant new items
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.sources import ALL_INGESTERS  # noqa: E402 (after sys.path shim above)

DATA_DIR = ROOT / "data"


def run_ingester(ingester, dry_run: bool = False) -> dict:
    source = ingester.source_name
    fetched = new = skipped = 0
    error = None

    try:
        for event in ingester.fetch():
            fetched += 1
            if dry_run:
                label = "WEIMAR" if event.weimar_relevant else "     ·"
                print(
                    f"  {label} {event.date} score={event.weimar_score:.2f} "
                    f"actors={event.actors} | {event.title[:80]}"
                )
                new += 1
            else:
                saved = event.save(str(DATA_DIR / "events"))
                if saved:
                    event.save_enriched(str(DATA_DIR / "enriched"))
                    new += 1
                    if event.weimar_relevant:
                        print(f"  + [{source}] {event.date} score={event.weimar_score:.2f} — {event.title[:70]}")
                else:
                    skipped += 1
    except Exception as exc:
        error = str(exc)
        print(f"  ERROR in {source}: {exc}", file=sys.stderr)

    return {"source": source, "fetched": fetched, "new": new, "skipped": skipped, "error": error}


def write_run_log(results: list[dict]) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log_path = DATA_DIR / "runs" / f"{today}.yaml"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_data = {
        "date": today,
        "run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": results,
        "totals": {
            "fetched": sum(r["fetched"] for r in results),
            "new": sum(r["new"] for r in results),
            "skipped": sum(r["skipped"] for r in results),
            "errors": sum(1 for r in results if r["error"]),
        },
    }
    log_path.write_text(yaml.dump(log_data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"\nRun log → {log_path.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Weimar tracker ingestion runner")
    parser.add_argument("--source", help="Run a single source by name")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and classify without writing files")
    parser.add_argument("--weimar-only", action="store_true",
                        help="Only print Weimar-relevant items (suppresses non-relevant output)")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Backfill: fetch events on or after this date (sources that support it)")
    args = parser.parse_args()

    ingesters = [cls(since=args.since) for cls in ALL_INGESTERS]
    if args.source:
        ingesters = [i for i in ingesters if i.source_name == args.source]
        if not ingesters:
            print(f"Unknown source '{args.source}'. Available: {[cls.source_name for cls in ALL_INGESTERS]}")
            sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "WRITE"
    print(f"Mode: {mode}  Sources: {[i.source_name for i in ingesters]}\n")

    results = []
    for ingester in ingesters:
        print(f"── {ingester.source_name}")
        r = run_ingester(ingester, dry_run=args.dry_run)
        print(f"   fetched={r['fetched']}  new={r['new']}  skipped={r['skipped']}"
              + (f"  ERROR: {r['error']}" if r["error"] else ""))
        results.append(r)

    totals_fetched = sum(r["fetched"] for r in results)
    totals_new = sum(r["new"] for r in results)
    print(f"\nTOTAL  fetched={totals_fetched}  new={totals_new}")

    if not args.dry_run:
        write_run_log(results)


if __name__ == "__main__":
    main()
