"""Tier 1 — convergence/stance cluster scoring in pipeline/render.py."""

from __future__ import annotations

import pytest

from pipeline.render import (
    CONVERGENCE_GOAL_CONVERGING,
    CONVERGENCE_GOAL_PARALLEL,
    _stance_agreement,
    score_cluster_convergence,
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


# --- score_cluster_convergence: peer-to-peer mode --------------------------


def _emb_cluster() -> dict:
    return cluster_from_events(
        "ukraine",
        {
            "DE": [event_dict(file_path="a.yaml")],
            "FR": [event_dict(file_path="b.yaml")],
        },
    )


def test_convergence_peer_to_peer_converging():
    # Identical unit vectors → cosine 1.0 → Converging (>= 0.72)
    emb = {"a.yaml": [1.0, 0.0], "b.yaml": [1.0, 0.0]}
    result = score_cluster_convergence(_emb_cluster(), emb)
    assert result["scoring_mode"] == "peer_to_peer"
    assert result["label"] == "Converging"
    assert result["overall"] == pytest.approx(1.0)


def test_convergence_peer_to_peer_diverging():
    # Orthogonal vectors → cosine 0.0 → Diverging (< 0.50)
    emb = {"a.yaml": [1.0, 0.0], "b.yaml": [0.0, 1.0]}
    result = score_cluster_convergence(_emb_cluster(), emb)
    assert result["label"] == "Diverging"
    assert result["overall"] == pytest.approx(0.0)


def test_convergence_needs_two_actors_with_embeddings():
    emb = {"a.yaml": [1.0, 0.0]}  # only DE has an embedding
    assert score_cluster_convergence(_emb_cluster(), emb) is None


# --- score_cluster_convergence: goal-anchored mode -------------------------


def test_convergence_goal_anchored_converging():
    # Both actors align perfectly with the goal vector → Converging.
    emb = {"a.yaml": [1.0, 0.0], "b.yaml": [1.0, 0.0]}
    goal = {"ukraine": [1.0, 0.0]}
    result = score_cluster_convergence(_emb_cluster(), emb, goal_emb_store=goal)
    assert result["scoring_mode"] == "goal_anchored"
    assert result["label"] == "Converging"
    assert result["overall"] >= CONVERGENCE_GOAL_CONVERGING


def test_convergence_goal_anchored_diverging():
    emb = {"a.yaml": [0.0, 1.0], "b.yaml": [0.0, 1.0]}
    goal = {"ukraine": [1.0, 0.0]}  # orthogonal to both actors
    result = score_cluster_convergence(_emb_cluster(), emb, goal_emb_store=goal)
    assert result["label"] == "Diverging"
    assert result["overall"] < CONVERGENCE_GOAL_PARALLEL


def test_position_embedding_takes_priority_over_fulltext():
    # pos_emb_store keyed "<path>#<area>" should win over the full-text emb_store.
    emb = {"a.yaml": [0.0, 1.0], "b.yaml": [0.0, 1.0]}  # would score Diverging alone
    pos = {"a.yaml#ukraine": [1.0, 0.0], "b.yaml#ukraine": [1.0, 0.0]}
    goal = {"ukraine": [1.0, 0.0]}
    result = score_cluster_convergence(_emb_cluster(), emb, goal_emb_store=goal, pos_emb_store=pos)
    assert result["label"] == "Converging"  # driven by pos vectors, not emb
