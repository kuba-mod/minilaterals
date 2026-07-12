"""Tier 2 — date-window computations in render.py, made deterministic by
injecting `today` so the Monday-snap and rolling-window logic is testable."""

from __future__ import annotations

from datetime import UTC, datetime

from pipeline.render import (
    compute_latest_heatmap,
    compute_topic_weekly_stances,
    compute_weekly_alignment,
)
from tests.conftest import event_dict

TODAY = datetime(2026, 6, 15, tzinfo=UTC)


# --- compute_weekly_alignment ----------------------------------------------


def test_weekly_alignment_scores_week_with_two_actors():
    events = [
        event_dict(source_name="german_mfa", date="2026-06-08", file_path="de.yaml"),
        event_dict(source_name="france_diplomatie", date="2026-06-10", file_path="fr.yaml"),
    ]
    emb = {"de.yaml": [1.0, 0.0], "fr.yaml": [1.0, 0.0]}
    series = compute_weekly_alignment(events, emb, today=TODAY)
    scored = [w for w in series if w is not None]
    assert scored, "expected at least one scored week"
    latest = scored[-1]
    assert latest["label"] == "Converging"  # identical vectors → cosine 1.0
    assert set(latest["actors_scored"]) == {"DE", "FR"}


def test_weekly_alignment_none_when_single_actor():
    events = [
        event_dict(source_name="german_mfa", date="2026-06-08", file_path="de.yaml"),
        event_dict(source_name="german_mfa", date="2026-06-10", file_path="de2.yaml"),
    ]
    emb = {"de.yaml": [1.0, 0.0], "de2.yaml": [1.0, 0.0]}
    series = compute_weekly_alignment(events, emb, today=TODAY)
    assert all(w is None for w in series)


def test_weekly_alignment_empty_without_embeddings():
    events = [event_dict(source_name="german_mfa", date="2026-06-08", file_path="de.yaml")]
    assert compute_weekly_alignment(events, {}, today=TODAY) == []


def test_weekly_alignment_ignores_non_mfa_sources():
    events = [
        event_dict(source_name="some_blog", date="2026-06-08", file_path="x.yaml"),
        event_dict(source_name="german_mfa", date="2026-06-09", file_path="de.yaml"),
    ]
    emb = {"x.yaml": [1.0, 0.0], "de.yaml": [1.0, 0.0]}
    # Only DE is a tracked MFA actor → never 2 actors → all None.
    series = compute_weekly_alignment(events, emb, today=TODAY)
    assert all(w is None for w in series)


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


# --- compute_latest_heatmap ------------------------------------------------


def test_latest_heatmap_picks_recent_scored_cluster():
    clusters = [
        {
            "area": "ukraine",
            "date_to": "2026-06-14",
            "convergence": {"label": "Aligned", "color": "#4d6b38", "overall": 0.9, "display": "+1.8"},
        }
    ]
    heatmap = compute_latest_heatmap(clusters, days=7, today=TODAY)
    assert heatmap["ukraine"]["label"] == "Aligned"
    assert heatmap["ukraine"]["display"] == "+1.8"


def test_latest_heatmap_ignores_stale_clusters():
    clusters = [
        {
            "area": "ukraine",
            "date_to": "2026-05-01",  # older than 7 days before TODAY
            "convergence": {"label": "Aligned", "color": "#4d6b38", "overall": 0.9},
        }
    ]
    heatmap = compute_latest_heatmap(clusters, days=7, today=TODAY)
    assert heatmap["ukraine"] is None


def test_latest_heatmap_all_areas_present():
    heatmap = compute_latest_heatmap([], days=7, today=TODAY)
    # Every tracked issue area is a key, defaulting to None.
    assert "ukraine" in heatmap and "defence" in heatmap
    assert all(v is None for v in heatmap.values())
