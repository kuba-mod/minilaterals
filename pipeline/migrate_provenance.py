#!/usr/bin/env python3
"""
One-time migration: backfill provenance metadata onto data written before it
was tracked (raw events collected / sidecars enriched prior to this change).

Nothing about provenance was recorded per-file at the time, so the values here
are *reconstructed from git history* — the only surviving record of how each
file was produced. The reconstruction rules, and the evidence for each:

RAW EVENTS  (data/events/) — adds `collection` + `collection_method`
  - `collection`: every pre-existing event is `source_lang: en`, and English is
    the fallback section for all three ministries (design principle #9), so all
    legacy events are `fallback`. Derived from source_lang via collection_tier().
  - `collection_method`:
      * france_diplomatie / polish_mfa — only ever scraped an HTML listing
        (both the initial backfill and daily runs), so always `html`.
      * german_mfa — daily runs read the live RSS feed (`rss`); the events
        seeded in the initial commit came from a mixed historical backfill
        (live feed + HTML pagination + Wayback replay) whose per-item mechanism
        was not recorded, so they are labelled `backfill`.

ENRICHED SIDECARS  (data/enriched/) — adds `enriched_by`
  - `model_id`: gemma4 was the sole enrichment model (the Anthropic provider is
    supported in code but has never been used — see CLAUDE.md), so `gemma4:latest`.
  - `prompt_version`: "1" — every current sidecar predates the multilingual
    prompt introduced in this branch (which is version "2"); enrich.py's prompts
    were unchanged from the last re-enrichment through the branch base.
  - `environment`: taken from the author of the commit that last wrote each
    sidecar — a human (kuba-mod) means it was enriched `local`ly, the
    github-actions bot means `github_actions`.

Usage:
    python -m pipeline.migrate_provenance --dry-run   # show what would change
    python -m pipeline.migrate_provenance             # apply
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import yaml

from pipeline.migrate_strip import RAW_FIELDS
from pipeline.sources.base import collection_tier

ROOT = Path(__file__).parent.parent
EVENTS_DIR = ROOT / "data" / "events"
ENRICHED_DIR = ROOT / "data" / "enriched"

# The initial commit that seeded the historical backfill (mixed mechanism).
SEED_COMMIT = "dfdbd4a"

# Legacy enrichment facts (see module docstring).
LEGACY_MODEL_ID = "gemma4:latest"
LEGACY_PROMPT_VERSION = "1"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()


def _adding_commit(path: Path) -> str:
    """Short hash of the commit that first added `path`."""
    return _git("log", "--diff-filter=A", "--format=%h", "-1", "--", str(path))


def _last_author_email(path: Path) -> str:
    """Email of the author of the commit that last modified `path`."""
    return _git("log", "--format=%ae", "-1", "--", str(path))


def _raw_method(source_name: str, adding_commit: str) -> str | None:
    if source_name in ("france_diplomatie", "polish_mfa"):
        return "html"
    if source_name == "german_mfa":
        return "backfill" if adding_commit.startswith(SEED_COMMIT) else "rss"
    return None


def _backfill_raw(path: Path, dry_run: bool) -> bool:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data or (data.get("collection") and data.get("collection_method")):
        return False
    source_name = data.get("source_name", "")
    method = data.get("collection_method") or _raw_method(source_name, _adding_commit(path))
    tier = data.get("collection") or collection_tier(source_name, data.get("source_lang", ""))
    # Rebuild in canonical field order (collection/method after source_lang).
    new: dict = {}
    for k in RAW_FIELDS:
        if k == "collection":
            new[k] = tier
        elif k == "collection_method":
            new[k] = method
        elif k in data:
            new[k] = data[k]
    if new == data:
        return False
    if not dry_run:
        path.write_text(yaml.dump(new, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return True


def _backfill_enriched(path: Path, dry_run: bool) -> bool:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data or data.get("enriched_by"):
        return False
    email = _last_author_email(path)
    environment = "github_actions" if "github-actions" in email else "local"
    data["enriched_by"] = {
        "model_id": LEGACY_MODEL_ID,
        "prompt_version": LEGACY_PROMPT_VERSION,
        "environment": environment,
    }
    if not dry_run:
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill provenance onto pre-existing YAMLs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    raw_files = sorted(EVENTS_DIR.glob("**/*.yaml"))
    enriched_files = sorted(ENRICHED_DIR.glob("**/*.yaml"))
    print(f"{'DRY RUN — ' if args.dry_run else ''}Raw events: {len(raw_files)}  Enriched: {len(enriched_files)}")

    raw_changed = sum(_backfill_raw(f, args.dry_run) for f in raw_files)
    enr_changed = sum(_backfill_enriched(f, args.dry_run) for f in enriched_files)

    action = "Would stamp" if args.dry_run else "Stamped"
    print(f"{action} {raw_changed} raw events and {enr_changed} enriched sidecars")


if __name__ == "__main__":
    main()
