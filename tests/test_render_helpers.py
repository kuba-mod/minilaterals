"""Tier 2 — pure math/geometry helpers in pipeline/render.py."""

from __future__ import annotations

import math

import pytest

from pipeline import render
from pipeline.render import (
    _allpairs_median_score,
    _dot,
    _fmt_stance,
    _mean_vec,
    _stance_norm,
    build_timeline_svg_data,
    cluster_key,
)


def test_dot_product():
    assert _dot([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _dot([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _dot([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == pytest.approx(32.0)


def test_mean_vec_normalises():
    result = _mean_vec([[3.0, 0.0], [0.0, 0.0]])
    # mean is (1.5, 0), normalised → (1, 0)
    assert result == pytest.approx([1.0, 0.0])
    assert math.isclose(math.sqrt(sum(x * x for x in result)), 1.0)


def test_mean_vec_empty_returns_none():
    assert _mean_vec([]) is None


def test_mean_vec_zero_magnitude_returns_none():
    assert _mean_vec([[0.0, 0.0], [0.0, 0.0]]) is None


def test_stance_norm_maps_range():
    assert _stance_norm(-2.0) == pytest.approx(0.0)
    assert _stance_norm(0.0) == pytest.approx(0.5)
    assert _stance_norm(2.0) == pytest.approx(1.0)


def test_fmt_stance_shows_sign():
    assert _fmt_stance(1.3) == "+1.3"
    assert _fmt_stance(-0.5) == "-0.5"
    assert _fmt_stance(0.0) == "+0.0"


def test_allpairs_median_pure_python_path():
    # numpy is not installed in this environment, so this exercises the
    # hand-rolled median/quartile fallback (_HAS_NUMPY is False).
    assert render._HAS_NUMPY is False
    vecs_a = [[1.0, 0.0], [0.0, 1.0]]
    vecs_b = [[1.0, 0.0]]
    med, q25, q75 = _allpairs_median_score(vecs_a, vecs_b)
    # sims are [1.0, 0.0]; median of two = 0.5
    assert med == pytest.approx(0.5)
    assert q25 <= med <= q75


def test_cluster_key_stable_and_order_independent():
    from tests.conftest import cluster_from_events, event_dict

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


def test_timeline_stance_mode_detected():
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
    assert svg["stance_mode"] is True
    assert len(svg["points"]) == 2
    assert svg["recent"]["week"] == "2026-06-08"
