#!/usr/bin/env python3
"""
One-time migration: drop topics whose stored position asserts the text says
nothing about them.

Prompt versions up to "8" described `topics` as a "list from" the vocabulary,
which the model sometimes read as a checklist to fill in rather than a selection
to make: it would list all six Weimar topics for a two-topic press release and
mark the misses in prose — "France does not explicitly address hybrid threats in
the provided text" stored as that topic's *position*.

A phantom topic propagates everywhere. It lands in `issue_areas`, so the event
joins a convergence cluster for a topic it never discusses; it feeds
`_grouping_relevance`; and `_stance_topics` asks the model to rate it on every
`--stances-only` run, which it declines every time, so the event stays pending
forever.

Prompt "9" states that `topics` is a selection and that a position must say what
the country did, never what the text lacks; `_extract()` drops any that slip
through anyway. This clears the ones already on disk, without a model call: the
model already told us in prose which topics don't belong.

The shared predicate is `enrich.asserts_absence()`, deliberately narrow — a real
position may well say a country "does not provide weapons" or "does not tolerate
breaches of international law", and deleting one of those would silently discard
a genuine negative stance. A topic whose stance already carries an evidence quote
is protected outright, even when its position asserts absence — see
`drop_absent_topics`. Relevance flags are left untouched: recomputing them needs
the actor set and is `pipeline.migrate_groupings`' job.

Only supported as a module (python -m pipeline.migrate_drop_absent_topics) so
pipeline.enrich resolves without a sys.path shim.

Usage:
    python -m pipeline.migrate_drop_absent_topics            # rewrite in place
    python -m pipeline.migrate_drop_absent_topics --dry-run  # report, write nothing
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from pipeline.enrich import asserts_absence, dump_yaml
from pipeline.schemas import EnrichedEventSchema

ROOT = Path(__file__).parent.parent
ENRICHED_DIR = ROOT / "data" / "enriched"


def _evidence_backed_topics(extracted: dict) -> set[str]:
    """Topics some grouping rated with a quote from the text."""
    rated = set()
    for topics in (extracted.get("stances") or {}).values():
        if not isinstance(topics, dict):
            continue
        for topic, entry in topics.items():
            if isinstance(entry, dict) and (entry.get("evidence") or "").strip():
                rated.add(topic)
    return rated


def drop_absent_topics(data: dict) -> tuple[dict, list[str]]:
    """Return (data without absence-asserting topics, [dropped topics]).

    Removes each such topic from `extracted.topics`, `extracted.positions`,
    every per-grouping block of `extracted.stances`, and the top-level
    `issue_areas` — all four are views of the same topic list, so leaving one
    behind would just move the inconsistency somewhere else.

    A topic whose stance carries an evidence quote is **kept** even if its
    position asserts absence. The two came from different calls and can
    disagree: the G7 trade-ministers item has a green_transition position
    reading "the text ... does not provide specific details or commitments"
    alongside a +1 stance quoting "securing mineral and critical-metal supply
    chains". A quote pinned to the source text is the stronger signal that the
    topic is really there, and deleting it would discard an auditable rating to
    satisfy a summary sentence. `_extract()` has no such conflict to weigh —
    stances are rated after topics are chosen — so it drops unconditionally.
    """
    extracted = data.get("extracted")
    if not isinstance(extracted, dict):
        return data, []
    positions = extracted.get("positions")
    if not isinstance(positions, dict):
        return data, []

    protected = _evidence_backed_topics(extracted)
    dropped = sorted(
        t for t, pos in positions.items() if isinstance(pos, str) and asserts_absence(pos) and t not in protected
    )
    if not dropped:
        return data, []

    new_extracted = dict(extracted)
    new_extracted["positions"] = {t: p for t, p in positions.items() if t not in dropped}
    if isinstance(extracted.get("topics"), list):
        new_extracted["topics"] = [t for t in extracted["topics"] if t not in dropped]
    stances = extracted.get("stances")
    if isinstance(stances, dict):
        kept_stances = {}
        for grouping, topics in stances.items():
            if not isinstance(topics, dict):
                kept_stances[grouping] = topics
                continue
            kept = {t: e for t, e in topics.items() if t not in dropped}
            if kept:
                kept_stances[grouping] = kept
        if kept_stances:
            new_extracted["stances"] = kept_stances
        else:
            new_extracted.pop("stances", None)

    updated = {**data, "extracted": new_extracted}
    if isinstance(data.get("issue_areas"), list):
        updated["issue_areas"] = [t for t in data["issue_areas"] if t not in dropped]
    return updated, dropped


def main() -> None:
    parser = argparse.ArgumentParser(description="Drop topics whose position asserts absence")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    changed = skipped = failed = total = 0
    for f in sorted(ENRICHED_DIR.glob("**/*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {f.relative_to(ROOT)}: {exc}")
            failed += 1
            continue
        if not data:
            continue

        updated, dropped = drop_absent_topics(data)
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
        total += len(dropped)

    print(f"\nDrop complete: {changed} files updated ({total} topics), {skipped} unchanged, {failed} failed")


if __name__ == "__main__":
    main()
