"""Tier 1 — stance cluster scoring in pipeline/render.py."""

from __future__ import annotations

import pytest

from pipeline.render import (
    _stance_agreement,
    score_cluster_stances,
)
from tests.conftest import cluster_from_events, event_dict

# --- _stance_agreement thresholds ------------------------------------------


@pytest.mark.parametrize(
    "spread,label",
    [
        (0.0, "Aligned"),
        (0.5, "Aligned"),
        (0.51, "Mixed"),
        (1.5, "Mixed"),
        (1.51, "Divergent"),
        (4.0, "Divergent"),
    ],
)
def test_stance_agreement_thresholds(spread, label):
    assert _stance_agreement(spread)[0] == label


def test_stance_agreement_returns_color():
    label, color = _stance_agreement(0.0)
    assert color.startswith("#")


# --- score_cluster_stances -------------------------------------------------


def _stance_event(area: str, score: int, fp: str) -> dict:
    return event_dict(file_path=fp, issue_areas=[area], stances={area: {"score": score, "evidence": "q"}})


def test_stance_scoring_aligned():
    cluster = cluster_from_events(
        "ukraine",
        {
            "DE": [_stance_event("ukraine", 2, "a.yaml")],
            "FR": [_stance_event("ukraine", 2, "b.yaml")],
        },
    )
    result = score_cluster_stances(cluster)
    assert result is not None
    assert result["label"] == "Aligned"
    assert result["overall"] == pytest.approx(2.0)
    assert result["spread"] == pytest.approx(0.0)
    assert result["scoring_mode"] == "stance"
    assert result["per_actor"]["DE"]["stance"] == pytest.approx(2.0)


def test_stance_scoring_divergent():
    cluster = cluster_from_events(
        "ukraine",
        {
            "DE": [_stance_event("ukraine", 2, "a.yaml")],
            "PL": [_stance_event("ukraine", -2, "b.yaml")],
        },
    )
    result = score_cluster_stances(cluster)
    assert result["label"] == "Divergent"
    assert result["spread"] == pytest.approx(4.0)


def test_stance_scoring_averages_multiple_events_per_actor():
    cluster = cluster_from_events(
        "ukraine",
        {
            "DE": [_stance_event("ukraine", 2, "a.yaml"), _stance_event("ukraine", 0, "b.yaml")],
            "FR": [_stance_event("ukraine", 1, "c.yaml")],
        },
    )
    result = score_cluster_stances(cluster)
    assert result["per_actor"]["DE"]["stance"] == pytest.approx(1.0)  # mean(2, 0)
    assert result["per_actor"]["DE"]["n"] == 2


def test_stance_scoring_needs_two_actors():
    cluster = cluster_from_events("ukraine", {"DE": [_stance_event("ukraine", 2, "a.yaml")]})
    assert score_cluster_stances(cluster) is None


def test_stance_scoring_none_when_no_stances():
    cluster = cluster_from_events(
        "ukraine",
        {"DE": [event_dict(file_path="a.yaml")], "FR": [event_dict(file_path="b.yaml")]},
    )
    assert score_cluster_stances(cluster) is None
