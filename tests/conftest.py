"""Shared fixtures and helpers for the test suite.

Tests are hermetic: they build inputs by hand (via the real `Event` dataclass and
small dicts matching the shapes the pipeline passes around) rather than reading the
live `data/` tree, so a data refresh never breaks a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.sources.base import Event

FIXTURES = Path(__file__).parent / "fixtures"


def make_event(
    *,
    source_name: str = "german_mfa",
    title: str = "",
    text: str = "",
    source_url: str = "https://example.test/article",
    source_lang: str = "en",
    source_published_at: str = "2026-06-01T00:00:00Z",
    date: str = "2026-06-01",
) -> Event:
    """Build a real, raw Event (classification now happens in pipeline.enrich).

    Prefer this over hand-rolled dicts so tests exercise the same dataclass the
    pipeline uses.
    """
    return Event(
        source_name=source_name,
        title=title,
        text=text,
        source_url=source_url,
        source_lang=source_lang,
        source_published_at=source_published_at,
        date=date,
    )


def event_dict(
    *,
    source_name: str = "german_mfa",
    date: str = "2026-06-01",
    file_path: str = "data/events/german_mfa/2026-06/2026-06-01-aaaaaaaa.yaml",
    issue_areas: list[str] | None = None,
    source_url: str = "https://example.test/article",
    stances: dict | None = None,
) -> dict:
    """A loaded-event dict in the shape render.py works with (post `load_events`)."""
    d: dict = {
        "source_name": source_name,
        "date": date,
        "_file_path": file_path,
        "issue_areas": issue_areas if issue_areas is not None else ["ukraine"],
        "source_url": source_url,
    }
    if stances is not None:
        d["extracted"] = {"stances": stances}
    return d


def cluster_from_events(area: str, actor_events: dict[str, list[dict]]) -> dict:
    """Build a cluster dict matching build_convergence_clusters' output shape.

    `actor_events` maps actor code -> list of event dicts; each becomes a
    {"event": <dict>} item under by_actor, exactly as render's scorers expect.
    """
    by_actor = {actor: [{"event": e} for e in evs] for actor, evs in actor_events.items()}
    dates = [e.get("date", "") for evs in actor_events.values() for e in evs]
    return {
        "area": area,
        "actors": sorted(actor_events),
        "date_from": min(dates) if dates else "",
        "date_to": max(dates) if dates else "",
        "by_actor": by_actor,
    }


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def make_event_factory():
    return make_event
