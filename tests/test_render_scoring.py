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
    "spread,overall,label",
    [
        # low spread (consensus): label depends on overall goal-alignment
        (0.0, 2.0, "Aligned"),
        (0.5, 0.5, "Aligned"),
        (0.0, 0.49, "Noncommittal"),
        (0.0, -0.49, "Noncommittal"),
        (0.5, -0.5, "Aligned against goal"),
        (0.0, -2.0, "Aligned against goal"),
        # spread alone drives the label once it's above the consensus threshold
        (0.51, 2.0, "Mixed"),
        (0.51, -2.0, "Mixed"),
        (1.5, 0.0, "Mixed"),
        (1.51, 0.0, "Divergent"),
        (4.0, 0.0, "Divergent"),
    ],
)
def test_stance_agreement_thresholds(spread, overall, label):
    assert _stance_agreement(spread, overall)[0] == label


def test_stance_agreement_returns_color():
    label, color = _stance_agreement(0.0, 2.0)
    assert color.startswith("#")


def test_stance_agreement_aligned_against_goal_is_red():
    label, color = _stance_agreement(0.0, -2.0)
    assert label == "Aligned against goal"
    assert color == "#a14132"


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


def test_stance_scoring_aligned_against_goal():
    """Both actors at -2 agree with each other but oppose the Weimar goal —
    this must not read as a green 'Aligned'."""
    cluster = cluster_from_events(
        "ukraine",
        {
            "DE": [_stance_event("ukraine", -2, "a.yaml")],
            "FR": [_stance_event("ukraine", -2, "b.yaml")],
        },
    )
    result = score_cluster_stances(cluster)
    assert result is not None
    assert result["label"] == "Aligned against goal"
    assert result["overall"] == pytest.approx(-2.0)
    assert result["spread"] == pytest.approx(0.0)


def test_stance_scoring_noncommittal():
    cluster = cluster_from_events(
        "ukraine",
        {
            "DE": [_stance_event("ukraine", 0, "a.yaml")],
            "FR": [_stance_event("ukraine", 0, "b.yaml")],
        },
    )
    result = score_cluster_stances(cluster)
    assert result["label"] == "Noncommittal"


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
