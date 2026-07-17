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
  - `prompt_version`: reconstructed per-file. A sidecar's ratings were produced
    by whatever prompt enrich.py carried at the commit that wrote them, so we
    hash the prompt surface (*_PROMPT / *_RUBRIC constants) at that commit and
    map it through PROMPT_LINEAGE. The prompt genuinely evolved (regex → LLM
    classification at PR #35, then shape hardening), so this is NOT a single
    value: most current sidecars are "3", but a handful of pre-#35 sidecars that
    survived the clear are still "1".
  - `environment`: taken from the author of the commit that wrote each sidecar —
    a human (kuba-mod) means it was enriched `local`ly, the github-actions bot
    means `github_actions`.

Both enriched facts are read at BASE_COMMIT (the branch base, before any
provenance commit) so this migration is reproducible and self-correcting: it
recomputes `enriched_by` from history rather than trusting whatever it wrote on
a previous run.

Usage:
    python -m pipeline.migrate_provenance --dry-run   # show what would change
    python -m pipeline.migrate_provenance             # apply
"""

from __future__ import annotations

import argparse
import ast
import hashlib
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
# Last commit before any provenance work: reading blame "as of" here ignores the
# provenance commits themselves, so each sidecar resolves to its true writer.
BASE_COMMIT = "4c33a6a"

LEGACY_MODEL_ID = "gemma4:latest"

# Prompt-surface sha256[:8] → prompt_version, the enrich.py prompt lineage (see
# that file's PROMPT_VERSION block). Unknown hashes fall back to the raw hash.
PROMPT_LINEAGE = {
    "612a65fa": "1",  # original — regex classification, LLM positions/stances
    "da8777de": "2",  # PR #35 — classification moved into the LLM prompt
    "c2cfff1e": "3",  # actors/explicit_weimar shape hardening + retry
    "434962fe": "4",  # native-language inputs (this branch)
}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def _adding_commit(path: Path) -> str:
    """Short hash of the commit that first added `path`."""
    return _git("log", "--diff-filter=A", "--format=%h", "-1", "--", str(path))


def _writer(path: Path) -> tuple[str, str]:
    """(short hash, author email) of the last commit up to BASE_COMMIT that
    touched `path` — its original writer, ignoring later provenance commits."""
    out = _git("log", BASE_COMMIT, "--format=%h\t%ae", "-1", "--", str(path))
    h, _, email = out.partition("\t")
    return h, email


_PROMPT_VERSION_CACHE: dict[str, str] = {}


def _prompt_version_at(commit: str) -> str:
    """prompt_version of enrich.py as it stood at `commit` (hashed prompt surface
    mapped through PROMPT_LINEAGE)."""
    if commit in _PROMPT_VERSION_CACHE:
        return _PROMPT_VERSION_CACHE[commit]
    src = _git("show", f"{commit}:pipeline/enrich.py")
    consts: dict[str, str] = {}
    for node in ast.parse(src).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and (node.targets[0].id.endswith("_PROMPT") or node.targets[0].id.endswith("_RUBRIC"))
        ):
            try:
                consts[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    blob = "".join(f"{k}={consts[k]}" for k in sorted(consts))
    sha = hashlib.sha256(blob.encode()).hexdigest()[:8]
    version = _PROMPT_VERSION_CACHE[commit] = PROMPT_LINEAGE.get(sha, sha)
    return version


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
    if not data:
        return False
    writer, email = _writer(path)
    enriched_by = {
        "model_id": LEGACY_MODEL_ID,
        "prompt_version": _prompt_version_at(writer),
        "environment": "github_actions" if "github-actions" in email else "local",
    }
    # Recompute rather than skip-if-present, so a prior run's values are corrected.
    if data.get("enriched_by") == enriched_by:
        return False
    data["enriched_by"] = enriched_by
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
