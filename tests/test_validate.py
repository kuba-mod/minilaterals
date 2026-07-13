"""Tests for pipeline/validate.py — hermetic, built from hand-written YAML
fixtures rather than the live data/ tree so a data refresh never breaks a test."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pipeline.validate import (
    AnnualSchema,
    EnrichedEventSchema,
    MeetingSchema,
    RawEventSchema,
    StanceSchema,
    validate_all,
)

VALID_RAW_EVENT = {
    "date": "2026-06-01",
    "type": "press_release",
    "source_name": "german_mfa",
    "title": "Statement on Ukraine",
    "text": "Germany reaffirms its support for Ukraine.",
    "source_url": "https://example.test/article",
    "source_lang": "en",
    "source_published_at": "2026-06-01T00:00:00Z",
    "ingested_at": "2026-06-01T01:00:00Z",
}

VALID_ENRICHED_EVENT = {
    "actors": ["DE"],
    "issue_areas": ["ukraine"],
    "weimar_relevant": True,
    "trilateral_signal": False,
    "extracted": {
        "event_type": "statement",
        "participants": ["Wadephul"],
        "topics": ["ukraine"],
        "location": None,
        "position": "Germany reaffirms its support for Ukraine.",
        "positions": {"ukraine": "Germany reaffirms its support for Ukraine."},
        "stances": {"ukraine": {"score": 2, "evidence": "reaffirms its support"}},
    },
}


def test_raw_event_schema_accepts_valid_event():
    RawEventSchema.model_validate(VALID_RAW_EVENT)


def test_raw_event_schema_rejects_missing_required_field():
    bad = {k: v for k, v in VALID_RAW_EVENT.items() if k != "source_url"}
    with pytest.raises(ValidationError):
        RawEventSchema.model_validate(bad)


def test_raw_event_schema_rejects_unknown_field():
    bad = {**VALID_RAW_EVENT, "weimar_score": 0.5}
    with pytest.raises(ValidationError):
        RawEventSchema.model_validate(bad)


def test_enriched_event_schema_accepts_valid_event():
    EnrichedEventSchema.model_validate(VALID_ENRICHED_EVENT)


def test_enriched_event_schema_allows_legacy_weimar_score():
    # weimar_score was removed from the code but still exists in older files —
    # the schema must tolerate it rather than reject hundreds of real files.
    legacy = {**VALID_ENRICHED_EVENT, "weimar_score": 0.1}
    EnrichedEventSchema.model_validate(legacy)


def test_stance_score_out_of_range_rejected():
    with pytest.raises(ValidationError):
        StanceSchema.model_validate({"score": 3, "evidence": ""})


def test_stance_score_in_range_accepted():
    StanceSchema.model_validate({"score": -2, "evidence": ""})
    StanceSchema.model_validate({"score": 2, "evidence": "quote"})


def test_meeting_schema_allows_empty_ministers():
    MeetingSchema.model_validate(
        {
            "date": "2024-01-01",
            "type": "Statement",
            "era": "renaissance",
            "location": "Warsaw, Poland",
            "topic": "Joint statement",
            "ministers": [],
        }
    )


def test_annual_schema_rejects_negative_meetings():
    with pytest.raises(ValidationError):
        AnnualSchema.model_validate({"year": 1991, "meetings": -1, "score": 0.5, "era": "founding", "note": "x"})


def test_validate_all_passes_on_well_formed_tree(tmp_path: Path):
    events_dir = tmp_path / "events"
    enriched_dir = tmp_path / "enriched"
    raw_path = events_dir / "german_mfa" / "2026-06" / "2026-06-01-aaaaaaaa.yaml"
    enriched_path = enriched_dir / "german_mfa" / "2026-06" / "2026-06-01-aaaaaaaa.yaml"
    raw_path.parent.mkdir(parents=True)
    enriched_path.parent.mkdir(parents=True)
    raw_path.write_text(yaml.dump(VALID_RAW_EVENT), encoding="utf-8")
    enriched_path.write_text(yaml.dump(VALID_ENRICHED_EVENT), encoding="utf-8")

    errors = validate_all(events_dir=events_dir, enriched_dir=enriched_dir, data_dir=tmp_path)

    assert errors == []


def test_validate_all_reports_broken_stance_score(tmp_path: Path):
    events_dir = tmp_path / "events"
    enriched_dir = tmp_path / "enriched"
    raw_path = events_dir / "german_mfa" / "2026-06" / "2026-06-01-aaaaaaaa.yaml"
    enriched_path = enriched_dir / "german_mfa" / "2026-06" / "2026-06-01-aaaaaaaa.yaml"
    raw_path.parent.mkdir(parents=True)
    enriched_path.parent.mkdir(parents=True)
    raw_path.write_text(yaml.dump(VALID_RAW_EVENT), encoding="utf-8")
    broken = {
        **VALID_ENRICHED_EVENT,
        "extracted": {**VALID_ENRICHED_EVENT["extracted"], "stances": {"ukraine": {"score": 9, "evidence": ""}}},
    }
    enriched_path.write_text(yaml.dump(broken), encoding="utf-8")

    errors = validate_all(events_dir=events_dir, enriched_dir=enriched_dir, data_dir=tmp_path)

    assert len(errors) == 1
    assert "2026-06-01-aaaaaaaa.yaml" in errors[0]
