"""Tier 2 — pure math/geometry helpers in pipeline/render.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipeline.render import (
    _fmt_stance,
    _stance_norm,
    build_country_line_series,
    build_divergence_leaderboard,
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


# --- country line series ---------------------------------------------------


def test_country_lines_keep_each_capital_separate_and_show_lone_speakers():
    events = [
        # enlargement, same window: DE far above PL -> lines fan apart
        _stance_event("german_mfa", "2026-06-29", 2, "enlargement"),
        _stance_event("polish_mfa", "2026-06-29", 0, "enlargement"),
        # ukraine: only France spoke -> a single-capital line still appears
        _stance_event("france_diplomatie", "2026-06-29", 1, "ukraine"),
    ]
    series = build_country_line_series(events, today=datetime(2026, 6, 29, tzinfo=UTC))
    assert "overall" in series
    # Per-topic series keep each capital as its own mean (not averaged together).
    enl = [w for w in series["enlargement"] if w][-1]
    assert enl["pa"] == {"DE": 2.0, "PL": 0.0}
    # A topic where only one capital spoke still yields a line (unlike the
    # spread-based series, which drops <2-capital weeks).
    ukr = [w for w in series["ukraine"] if w][-1]
    assert ukr["pa"] == {"FR": 1.0}
    # Overall blends every topic per capital: France's only score is the ukraine +1.
    ov = [w for w in series["overall"] if w][-1]
    assert ov["pa"]["FR"] == pytest.approx(1.0)
    assert ov["pa"]["DE"] == pytest.approx(2.0)


# --- divergence leaderboard (orders pills + clusters) ----------------------


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
