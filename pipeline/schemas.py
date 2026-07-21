#!/usr/bin/env python3
"""
Weimar Triangle tracker — shared pydantic schemas for every YAML shape written
by the pipeline (raw events, enriched sidecars, meetings, annual, milestones,
edition, ingest run logs).

Deliberately has no dependency on pipeline.sources.base, so both base.py
(Event.save()/save_enriched()) and enrich.py (LLM output) can import these
models to validate at write time, and pipeline.validate can reuse the same
models to re-check the whole data/ tree in CI, without a circular import.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
    # Provenance. Optional/defaulted so events written before provenance
    # tracking (and any not-yet-backfilled file) still validate; new events
    # always carry both. collection: "native" | "fallback"; collection_method:
    # "rss" | "html" | "wayback" | "backfill" (legacy seed).
    collection: str | None = None
    collection_method: str | None = None
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


class EnrichedBySchema(BaseModel):
    """Enrichment provenance: which model, prompt revision, and environment
    produced the sidecar's current content."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    prompt_version: str
    environment: str  # "local" | "github_actions"


class EnrichedEventSchema(BaseModel):
    """data/enriched/{source}/{YYYY-MM}/{date}-{hash8}.yaml — written by Event.save_enriched().

    extra="allow" (not "forbid") because many existing files carry a legacy
    weimar_score field removed from the code but never migrated out of the data."""

    model_config = ConfigDict(extra="allow")

    actors: list[str] = Field(default_factory=list)
    issue_areas: list[str] = Field(default_factory=list)
    # Weimar relevance keeps its legacy field names for backward compatibility;
    # every other grouping (see data/groupings.yaml) emits a parallel
    # {key}_relevant / {key}_signal pair. All default False so sidecars written
    # before a grouping existed still validate.
    weimar_relevant: bool = False
    trilateral_signal: bool = False
    e3_relevant: bool = False
    e3_signal: bool = False
    visegrad_relevant: bool = False
    visegrad_signal: bool = False
    baltic_relevant: bool = False
    baltic_signal: bool = False
    aukus_relevant: bool = False
    aukus_signal: bool = False
    extracted: ExtractedSchema | None = None
    # Optional so sidecars written before provenance tracking still validate;
    # new/backfilled sidecars always carry it.
    enriched_by: EnrichedBySchema | None = None


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
