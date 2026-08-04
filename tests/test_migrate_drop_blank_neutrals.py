"""Tier 1 — the one-off migration that clears evidence-less neutral stances."""

from __future__ import annotations

from pipeline.migrate_drop_blank_neutrals import drop_blank_neutrals


def _sidecar(stances):
    return {"weimar_relevant": True, "extracted": {"topics": ["defence"], "stances": stances}}


def test_drops_blank_neutral_and_reports_it():
    data = _sidecar({"weimar": {"defence": {"score": 0, "evidence": ""}}})
    updated, dropped = drop_blank_neutrals(data)
    assert dropped == ["weimar/defence"]
    # The whole grouping block goes with its last rating, and so does an empty
    # `stances` — the event must look unrated to --stances-only, not empty-rated.
    assert "stances" not in updated["extracted"]


def test_keeps_neutral_with_evidence():
    data = _sidecar({"weimar": {"defence": {"score": 0, "evidence": "cytat"}}})
    updated, dropped = drop_blank_neutrals(data)
    assert dropped == []
    assert updated == data


def test_keeps_nonzero_without_evidence():
    data = _sidecar({"weimar": {"defence": {"score": 1, "evidence": ""}}})
    _, dropped = drop_blank_neutrals(data)
    assert dropped == []


def test_drops_per_grouping_independently():
    # The Kh-101 case: visegrad rated the same text, weimar found nothing.
    data = _sidecar(
        {
            "weimar": {"defence": {"score": 0, "evidence": ""}},
            "visegrad": {"defence": {"score": 1, "evidence": "stan gotowości"}},
        }
    )
    updated, dropped = drop_blank_neutrals(data)
    assert dropped == ["weimar/defence"]
    assert updated["extracted"]["stances"] == {"visegrad": {"defence": {"score": 1, "evidence": "stan gotowości"}}}


def test_keeps_siblings_in_the_same_grouping():
    data = _sidecar(
        {
            "weimar": {
                "ukraine": {"score": 1, "evidence": "będziemy wspierać"},
                "defence": {"score": 0, "evidence": ""},
            }
        }
    )
    updated, dropped = drop_blank_neutrals(data)
    assert dropped == ["weimar/defence"]
    assert set(updated["extracted"]["stances"]["weimar"]) == {"ukraine"}


def test_untouched_when_there_are_no_stances():
    for data in ({}, {"extracted": {}}, {"extracted": {"stances": {}}}):
        assert drop_blank_neutrals(data) == (data, [])


def test_does_not_mutate_input():
    data = _sidecar({"weimar": {"defence": {"score": 0, "evidence": ""}}})
    drop_blank_neutrals(data)
    assert data["extracted"]["stances"] == {"weimar": {"defence": {"score": 0, "evidence": ""}}}
