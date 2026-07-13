"""Tier 2 — pure math/geometry helpers in pipeline/render.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipeline.render import (
    _fmt_stance,
    _stance_norm,
    build_divergence_leaderboard,
    build_timeline_svg_data,
    build_topic_drilldown,
    cluster_key,
    compute_topic_weekly_stances,
)
from tests.conftest import cluster_from_events, event_dict


def _stance_event(source, date, score, area="enlargement", *, title="t", position="p", evidence="e"):
    """A loaded-event dict carrying one topic stance, in render.py's shape."""
    return {
        "source_name": source,
        "date": date,
        "title": title,
        "source_url": f"https://example.test/{date}",
        "extracted": {"position": position, "stances": {area: {"score": score, "evidence": evidence}}},
    }


def test_stance_norm_maps_range():
    assert _stance_norm(-2.0) == pytest.approx(0.0)
    assert _stance_norm(0.0) == pytest.approx(0.5)
    assert _stance_norm(2.0) == pytest.approx(1.0)


def test_fmt_stance_shows_sign():
    assert _fmt_stance(1.3) == "+1.3"
    assert _fmt_stance(-0.5) == "-0.5"
    assert _fmt_stance(0.0) == "+0.0"


def test_cluster_key_stable_and_order_independent():
    c1 = cluster_from_events(
        "ukraine",
        {"DE": [event_dict(file_path="a.yaml")], "FR": [event_dict(file_path="b.yaml")]},
    )
    c2 = cluster_from_events(
        "ukraine",
        {"FR": [event_dict(file_path="b.yaml")], "DE": [event_dict(file_path="a.yaml")]},
    )
    key = cluster_key(c1)
    assert len(key) == 12
    assert key == cluster_key(c2)  # sorted paths → stable regardless of actor order


# --- build_timeline_svg_data -----------------------------------------------


def test_timeline_none_with_fewer_than_two_scored_weeks():
    assert build_timeline_svg_data([None, None]) is None
    assert build_timeline_svg_data([{"overall": 0.5, "week": "2026-06-01", "label": "Mixed", "color": "#000"}]) is None


def test_timeline_builds_from_stance_series():
    weekly = [
        {
            "week": "2026-06-01",
            "overall": 0.75,
            "stance_avg": 1.0,
            "display": "+1.0",
            "band_lo": 0.5,
            "band_hi": 0.9,
            "label": "Aligned",
            "color": "#4d6b38",
            "per_actor": {"DE": 1.0, "FR": 1.0},
            "actors_scored": ["DE", "FR"],
            "n_events": 3,
        },
        {
            "week": "2026-06-08",
            "overall": 0.6,
            "stance_avg": 0.4,
            "display": "+0.4",
            "band_lo": 0.4,
            "band_hi": 0.8,
            "label": "Mixed",
            "color": "#8a6320",
            "per_actor": {"DE": 1.0, "FR": -0.2},
            "actors_scored": ["DE", "FR"],
            "n_events": 2,
        },
    ]
    svg = build_timeline_svg_data(weekly)
    assert svg is not None
    assert len(svg["points"]) == 2
    assert svg["band_points"]  # min–max band drawn from band_lo/band_hi
    assert svg["recent"]["week"] == "2026-06-08"


def test_timeline_flags_low_confidence_and_fits_domain():
    weekly = [
        {"week": "2026-06-01", "overall": 0.80, "stance_avg": 1.2, "display": "+1.2",
         "band_lo": 0.75, "band_hi": 0.85, "label": "Aligned", "color": "#4d6b38",
         "per_actor": {"DE": 1.2, "FR": 1.2}, "actors_scored": ["DE", "FR"], "n_events": 2},
        {"week": "2026-06-08", "overall": 0.78, "stance_avg": 1.1, "display": "+1.1",
         "band_lo": 0.70, "band_hi": 0.86, "label": "Aligned", "color": "#4d6b38",
         "per_actor": {"DE": 1.2, "FR": 1.0}, "actors_scored": ["DE", "FR"], "n_events": 12},
    ]
    svg = build_timeline_svg_data(weekly)
    # n_events below LOW_CONFIDENCE_N (4) flags the point as low-confidence.
    assert svg["points"][0]["low"] is True
    assert svg["points"][1]["low"] is False
    # Fit-to-data: the axis frames the ~+1 band, so it never labels the −2 floor.
    labels = {rl["label"] for rl in svg["ref_lines"]}
    assert "-2" not in labels and "-1" not in labels
    assert "+1" in labels  # the level the data sits on is drawn


# --- leaderboard + drill-down ----------------------------------------------


def test_leaderboard_ranks_by_current_week_spread():
    events = [
        _stance_event("german_mfa", "2026-06-29", 2, "enlargement"),
        _stance_event("polish_mfa", "2026-06-29", 0, "enlargement"),
        _stance_event("german_mfa", "2026-06-29", 1, "ukraine"),
        _stance_event("france_diplomatie", "2026-06-29", 1, "ukraine"),
    ]
    topic_weekly = compute_topic_weekly_stances(events, today=datetime(2026, 6, 29, tzinfo=UTC))
    board = build_divergence_leaderboard(topic_weekly)
    ranked = [r for r in board if not r["quiet"]]
    # enlargement (spread 2, Divergent) outranks ukraine (spread 0, Aligned).
    assert ranked[0]["area"] == "enlargement"
    assert ranked[0]["label"] == "Divergent"
    assert ranked[0]["spread"] == pytest.approx(2.0)
    assert ranked[-1]["area"] == "ukraine"


def test_drilldown_lists_statements_behind_each_week():
    events = [
        _stance_event("german_mfa", "2026-06-29", 2, "enlargement",
                      title="DE title", position="DE pos", evidence="DE ev"),
        _stance_event("polish_mfa", "2026-06-29", 0, "enlargement",
                      title="PL title", position="PL pos", evidence="PL ev"),
    ]
    topic_weekly = compute_topic_weekly_stances(events, today=datetime(2026, 6, 29, tzinfo=UTC))
    drill = build_topic_drilldown(topic_weekly, events)
    assert "overall" not in drill  # aggregate has no drill-down file
    items = [it for weeks in drill["enlargement"].values() for it in weeks]
    de = next(it for it in items if it["actor"] == "DE")
    assert de["position"] == "DE pos" and de["evidence"] == "DE ev" and de["score"] == 2
    assert de["url"] == "https://example.test/2026-06-29"
