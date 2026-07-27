#!/usr/bin/env python3
"""
Weimar Triangle tracker — YAML schema validator.

Validates every YAML file under data/ against a pydantic schema matching its
shape (raw event, enriched sidecar, meetings, annual, milestones, edition,
weimar goals, ingest run log). Run in CI to catch malformed ingest output,
broken LLM enrichment, or bad hand-edits before they reach the renderer.

Only supported as a module (python -m pipeline.validate) — not as a direct
script (python pipeline/validate.py) — so pipeline.schemas and
pipeline.sources.base resolve without a sys.path shim.

Usage:
    python -m pipeline.validate
    python -m pipeline.validate --path data/enriched
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from pipeline.schemas import (
    AnnualSchema,
    EditionSchema,
    EnrichedEventSchema,
    MeetingSchema,
    MilestoneSchema,
    RawEventSchema,
    RunLogSchema,
)

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

# The canonical actor codes and issue-area enum, derived from the grouping
# config (data/groupings.yaml) so they can't drift from what enrich.py offers
# the LLM. Both are the union across every tracked minilateral.
GROUPINGS_PATH = DATA_DIR / "groupings.yaml"


def _load_grouping_vocab() -> tuple[set[str], set[str]]:
    try:
        raw = yaml.safe_load(GROUPINGS_PATH.read_text(encoding="utf-8")) or {}
    except OSError:
        return set(), set()
    # `topics` marks a grouping as tracked by the pipeline; the file also holds
    # hub-page placeholders, whose members must not widen the actor enum.
    tracked = [g for g in raw.values() if isinstance(g, dict) and g.get("topics")]
    actors = {c for g in tracked for c in (g.get("members") or [])}
    issues = {t for g in tracked for t in g["topics"]}
    return actors, issues


KNOWN_ACTORS, KNOWN_ISSUE_AREAS = _load_grouping_vocab()

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _rel(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _validate_file(path: Path, schema: type[BaseModel]) -> str | None:
    try:
        data = _load_yaml(path)
    except yaml.YAMLError as exc:
        return f"{_rel(path)}: invalid YAML — {exc}"
    if data is None:
        return f"{_rel(path)}: empty file"
    try:
        schema.model_validate(data)
    except ValidationError as exc:
        return f"{_rel(path)}: {exc}"
    return None


def _validate_list_file(path: Path, schema: type[BaseModel]) -> list[str]:
    errors: list[str] = []
    try:
        data = _load_yaml(path)
    except yaml.YAMLError as exc:
        return [f"{_rel(path)}: invalid YAML — {exc}"]
    if not isinstance(data, list):
        return [f"{_rel(path)}: expected a list, got {type(data).__name__}"]
    for i, item in enumerate(data):
        try:
            schema.model_validate(item)
        except ValidationError as exc:
            errors.append(f"{_rel(path)}[{i}]: {exc}")
    return errors


def _validate_goals(path: Path) -> list[str]:
    """Every tracked grouping's `goals` must match its `topics` exactly.

    Goals are per grouping, not per topic — `defence` means something different
    to AUKUS than to the Weimar Triangle. A topic added to a grouping without a
    goal sentence (or a leftover sentence for a dropped topic) fails here.
    """
    if not path.exists():
        return []
    try:
        raw = _load_yaml(path)
    except yaml.YAMLError as exc:
        return [f"{_rel(path)}: invalid YAML — {exc}"]
    if not isinstance(raw, dict):
        return [f"{_rel(path)}: expected a mapping, got {type(raw).__name__}"]
    errors = []
    for key, g in raw.items():
        if not isinstance(g, dict):
            continue
        topics = set(g.get("topics") or [])
        if not topics:
            if g.get("goals"):
                errors.append(f"{_rel(path)}: {key} has goals but no topics — it is not tracked")
            continue
        goals = g.get("goals")
        if not isinstance(goals, dict):
            errors.append(f"{_rel(path)}: {key} is tracked but has no `goals` mapping")
            continue
        for extra in sorted(set(goals) - topics):
            errors.append(f"{_rel(path)}: {key}.goals has {extra!r}, which is not one of its topics")
        for missing in sorted(topics - set(goals)):
            errors.append(f"{_rel(path)}: {key}.goals is missing a sentence for topic {missing!r}")
        for topic, value in goals.items():
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{_rel(path)}: {key}.goals[{topic}] expected a non-empty string")
    return errors


def _validate_actors_and_issues(path: Path, data: dict) -> list[str]:
    errors = []
    unknown_actors = set(data.get("actors") or []) - KNOWN_ACTORS
    if unknown_actors:
        errors.append(f"{_rel(path)}: unknown actors {sorted(unknown_actors)}")
    unknown_areas = set(data.get("issue_areas") or []) - KNOWN_ISSUE_AREAS
    if unknown_areas:
        errors.append(f"{_rel(path)}: unknown issue_areas {sorted(unknown_areas)}")
    return errors


def validate_all(
    events_dir: Path | None = None,
    enriched_dir: Path | None = None,
    data_dir: Path | None = None,
) -> list[str]:
    errors: list[str] = []

    data_dir = data_dir or DATA_DIR
    events_dir = events_dir or data_dir / "events"
    enriched_dir = enriched_dir or data_dir / "enriched"

    for f in sorted(glob.glob(str(events_dir / "**" / "*.yaml"), recursive=True)):
        err = _validate_file(Path(f), RawEventSchema)
        if err:
            errors.append(err)

    for f in sorted(glob.glob(str(enriched_dir / "**" / "*.yaml"), recursive=True)):
        path = Path(f)
        try:
            data = _load_yaml(path)
        except yaml.YAMLError as exc:
            errors.append(f"{_rel(path)}: invalid YAML — {exc}")
            continue
        if data is None:
            errors.append(f"{_rel(path)}: empty file")
            continue
        try:
            EnrichedEventSchema.model_validate(data)
        except ValidationError as exc:
            errors.append(f"{_rel(path)}: {exc}")
            continue
        errors.extend(_validate_actors_and_issues(path, data))

    edition_path = data_dir / "edition.yaml"
    if edition_path.exists():
        err = _validate_file(edition_path, EditionSchema)
        if err:
            errors.append(err)

    meetings_path = data_dir / "meetings.yaml"
    if meetings_path.exists():
        errors.extend(_validate_list_file(meetings_path, MeetingSchema))

    annual_path = data_dir / "annual.yaml"
    if annual_path.exists():
        errors.extend(_validate_list_file(annual_path, AnnualSchema))

    milestones_path = data_dir / "milestones.yaml"
    if milestones_path.exists():
        errors.extend(_validate_list_file(milestones_path, MilestoneSchema))

    errors.extend(_validate_goals(GROUPINGS_PATH))

    for f in sorted(glob.glob(str(data_dir / "runs" / "*.yaml"))):
        err = _validate_file(Path(f), RunLogSchema)
        if err:
            errors.append(err)

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Weimar tracker YAML schema validator")
    parser.add_argument("--path", help="Only validate files under this path prefix")
    args = parser.parse_args()

    errors = validate_all()

    if args.path:
        errors = [e for e in errors if e.startswith(args.path) or f"/{args.path}" in e]

    if errors:
        print(f"{len(errors)} schema violation(s):\n", file=sys.stderr)
        for err in errors:
            print(f"  {err}\n", file=sys.stderr)
        sys.exit(1)

    print("All YAML files valid.")


if __name__ == "__main__":
    main()
