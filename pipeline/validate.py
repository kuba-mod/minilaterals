#!/usr/bin/env python3
"""
Weimar Triangle tracker — YAML schema validator.

Validates every YAML file under data/ against a pydantic schema matching its
shape (raw event, enriched sidecar, meetings, annual, milestones, edition,
weimar goals, ingest run log). Run in CI to catch malformed ingest output,
broken LLM enrichment, or bad hand-edits before they reach the renderer.

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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.sources.base import COUNTRY_TERMS, ISSUE_AREAS  # noqa: E402 (after sys.path shim above)

DATA_DIR = ROOT / "data"

KNOWN_ACTORS = set(COUNTRY_TERMS)
KNOWN_ISSUE_AREAS = set(ISSUE_AREAS)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RawEventSchema(BaseModel):
    """data/events/{source}/{YYYY-MM}/{date}-{hash8}.yaml — written by Event.save()."""

    model_config = ConfigDict(extra="forbid")

    date: str
    type: str
    source_name: str
    title: str
    text: str
    source_url: str
    source_lang: str
    source_published_at: str
    ingested_at: str


class StanceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=-2, le=2)
    evidence: str


class ExtractedSchema(BaseModel):
    """The extracted: block written by pipeline.enrich. event_type is intentionally
    not a strict enum — observed LLM output includes values outside the documented
    prompt enum (e.g. "conference")."""

    model_config = ConfigDict(extra="forbid")

    event_type: str | None = None
    participants: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    location: str | None = None
    position: str
    positions: dict[str, str] = Field(default_factory=dict)
    stances: dict[str, StanceSchema] = Field(default_factory=dict)


class EnrichedEventSchema(BaseModel):
    """data/enriched/{source}/{YYYY-MM}/{date}-{hash8}.yaml — written by Event.save_enriched().

    extra="allow" (not "forbid") because many existing files carry a legacy
    weimar_score field removed from the code but never migrated out of the data."""

    model_config = ConfigDict(extra="allow")

    actors: list[str] = Field(default_factory=list)
    issue_areas: list[str] = Field(default_factory=list)
    weimar_relevant: bool = False
    trilateral_signal: bool = False
    extracted: ExtractedSchema | None = None


class EditionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cutoff: str


class MeetingSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    type: str
    era: str
    location: str
    topic: str
    ministers: list[str] = Field(default_factory=list)


class AnnualSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int
    meetings: int = Field(ge=0)
    score: float
    era: str
    note: str


class MilestoneSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int
    era: str
    event: str


class SourceRunSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    fetched: int
    new: int
    skipped: int
    error: str | None = None


class RunTotalsSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fetched: int
    new: int
    skipped: int
    errors: int


class RunLogSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    run_at: str
    sources: list[SourceRunSchema]
    totals: RunTotalsSchema


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
    if not path.exists():
        return []
    try:
        data = _load_yaml(path)
    except yaml.YAMLError as exc:
        return [f"{_rel(path)}: invalid YAML — {exc}"]
    if not isinstance(data, dict):
        return [f"{_rel(path)}: expected a mapping, got {type(data).__name__}"]
    errors = []
    extra_keys = set(data) - KNOWN_ISSUE_AREAS
    missing_keys = KNOWN_ISSUE_AREAS - set(data)
    if extra_keys:
        errors.append(f"{_rel(path)}: unknown issue-area keys {sorted(extra_keys)}")
    if missing_keys:
        errors.append(f"{_rel(path)}: missing issue-area keys {sorted(missing_keys)}")
    for key, value in data.items():
        if not isinstance(value, str):
            errors.append(f"{_rel(path)}[{key}]: expected a string, got {type(value).__name__}")
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

    errors.extend(_validate_goals(data_dir / "weimar_goals.yaml"))

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
