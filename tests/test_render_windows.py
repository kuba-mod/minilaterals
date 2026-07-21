"""Tier 2 — date-window computations in render.py, made deterministic by
injecting `today` so the Monday-snap and rolling-window logic is testable."""

from __future__ import annotations

from datetime import UTC, datetime

from pipeline.render import (
    _stance_rows,
    compute_latest_topic_pills,
    compute_topic_weekly_stances,
)
from tests.conftest import event_dict

TODAY = datetime(2026, 6, 15, tzinfo=UTC)


# --- compute_topic_weekly_stances ------------------------------------------


def _stance_event(source, date, fp, topic, score):
    return event_dict(
        source_name=source,
        date=date,
        file_path=fp,
        issue_areas=[topic],
        stances={topic: {"score": score, "evidence": "q"}},
    )


def test_topic_weekly_stances_builds_series():
    events = [
        _stance_event("german_mfa", "2026-06-08", "de.yaml", "ukraine", 2),
        _stance_event("france_diplomatie", "2026-06-10", "fr.yaml", "ukraine", 2),
    ]
    result = compute_topic_weekly_stances(events, today=TODAY)
    assert "ukraine" in result
    assert "overall" in result
    ukraine_scored = [w for w in result["ukraine"] if w is not None]
    assert ukraine_scored
    latest = ukraine_scored[-1]
    assert latest["label"] == "Aligned"  # both +2
    assert latest["stance_avg"] == 2.0


def test_topic_weekly_stances_empty_without_ratings():
    events = [event_dict(source_name="german_mfa", date="2026-06-08", file_path="de.yaml")]
    assert compute_topic_weekly_stances(events, today=TODAY) == {}


def test_topic_weekly_stances_single_actor_weeks_are_none():
    events = [
        _stance_event("german_mfa", "2026-06-08", "de.yaml", "ukraine", 2),
        _stance_event("german_mfa", "2026-06-10", "de2.yaml", "ukraine", 1),
    ]
    result = compute_topic_weekly_stances(events, today=TODAY)
    assert all(w is None for w in result.get("ukraine", []))


def test_topic_weekly_stances_counts_executive_office_sources():
    # A country's executive office (chancellery/Élysée/KPRM) is as much its
    # position as its foreign ministry (design principle #3) — a statement
    # from only that source, paired with another country's MFA, must still
    # form a scored 2-actor week rather than being silently dropped.
    events = [
        _stance_event("polish_pm", "2026-06-08", "pl_pm.yaml", "enlargement", -1),
        _stance_event("german_mfa", "2026-06-09", "de.yaml", "enlargement", 1),
    ]
    result = compute_topic_weekly_stances(events, today=TODAY)
    scored = [w for w in result.get("enlargement", []) if w is not None]
    assert scored
    assert scored[-1]["per_actor"] == {"PL": -1.0, "DE": 1.0}


# --- _stance_rows ------------------------------------------------------------


def test_stance_rows_include_all_six_sources():
    events = [
        _stance_event("polish_pm", "2026-06-08", "pl_pm.yaml", "enlargement", -1),
        _stance_event("elysee", "2026-06-08", "fr_exec.yaml", "ukraine", 1),
        _stance_event("german_chancellery", "2026-06-08", "de_exec.yaml", "defence", 2),
        _stance_event("unknown_source", "2026-06-08", "x.yaml", "ukraine", 1),
    ]
    rows = _stance_rows(events)
    actors = {(a, t) for _, a, t, _ in rows}
    assert ("PL", "enlargement") in actors
    assert ("FR", "ukraine") in actors
    assert ("DE", "defence") in actors
    assert len(rows) == 3  # the unrecognised source contributes nothing


# --- compute_latest_topic_pills ---------------------------------------------


def test_latest_topic_pills_picks_recent_scored_cluster():
    clusters = [
        {
            "area": "ukraine",
            "date_to": "2026-06-14",
            "convergence": {"label": "Aligned", "color": "#4d6b38", "overall": 0.9, "display": "+1.8"},
        }
    ]
    pills = compute_latest_topic_pills(clusters, days=7, today=TODAY)
    assert pills["ukraine"]["label"] == "Aligned"
    assert pills["ukraine"]["display"] == "+1.8"


def test_latest_topic_pills_ignores_stale_clusters():
    clusters = [
        {
            "area": "ukraine",
            "date_to": "2026-05-01",  # older than 7 days before TODAY
            "convergence": {"label": "Aligned", "color": "#4d6b38", "overall": 0.9},
        }
    ]
    pills = compute_latest_topic_pills(clusters, days=7, today=TODAY)
    assert pills["ukraine"] is None


def test_latest_topic_pills_all_areas_present():
    pills = compute_latest_topic_pills([], days=7, today=TODAY)
    # Every tracked issue area is a key, defaulting to None.
    assert "ukraine" in pills and "defence" in pills
    assert all(v is None for v in pills.values())
