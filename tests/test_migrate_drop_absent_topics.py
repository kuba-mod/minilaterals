"""Tier 1 — the one-off migration that strips absence-asserting topics."""

from __future__ import annotations

from pipeline.migrate_drop_absent_topics import drop_absent_topics

ABSENT = "France does not explicitly address hybrid threats in the provided text."


def _sidecar(**over):
    data = {
        "actors": ["FR"],
        "issue_areas": ["ukraine", "hybrid"],
        "weimar_relevant": True,
        "extracted": {
            "topics": ["ukraine", "hybrid"],
            "positions": {"ukraine": "France pledges continued support.", "hybrid": ABSENT},
            "stances": {"weimar": {"ukraine": {"score": 1, "evidence": "soutien à l'Ukraine"}}},
        },
    }
    data["extracted"].update(over.pop("extracted", {}))
    data.update(over)
    return data


def test_drops_topic_from_all_four_views():
    updated, dropped = drop_absent_topics(_sidecar())
    assert dropped == ["hybrid"]
    ex = updated["extracted"]
    assert ex["topics"] == ["ukraine"]
    assert set(ex["positions"]) == {"ukraine"}
    assert updated["issue_areas"] == ["ukraine"]
    # The surviving stance is untouched.
    assert ex["stances"]["weimar"]["ukraine"]["score"] == 1


def test_drops_the_topics_stance_too():
    data = _sidecar(
        extracted={"stances": {"weimar": {"hybrid": {"score": 0, "evidence": ""}}}},
    )
    updated, dropped = drop_absent_topics(data)
    assert dropped == ["hybrid"]
    # Nothing rated survives, so the whole stances map goes.
    assert "stances" not in updated["extracted"]


def test_protects_a_topic_whose_stance_has_evidence():
    # The G7 trade-ministers case: the position says the text gives no detail,
    # but the stance call quoted the text. The quote wins.
    data = _sidecar(
        extracted={
            "stances": {"weimar": {"hybrid": {"score": 1, "evidence": "securing critical-metal supply chains"}}},
        }
    )
    updated, dropped = drop_absent_topics(data)
    assert dropped == []
    assert updated == data


def test_protection_is_topic_wide_across_groupings():
    # Rated under visegrad with a quote → the topic is real, so weimar simply
    # has no quotable stance against *its* goal rather than a phantom topic.
    data = _sidecar(
        extracted={
            "stances": {"visegrad": {"hybrid": {"score": 0, "evidence": "cytat z tekstu"}}},
        }
    )
    _, dropped = drop_absent_topics(data)
    assert dropped == []


def test_evidence_less_stance_does_not_protect():
    data = _sidecar(extracted={"stances": {"weimar": {"hybrid": {"score": 1, "evidence": "   "}}}})
    _, dropped = drop_absent_topics(data)
    assert dropped == ["hybrid"]


def test_keeps_real_position_containing_does_not():
    data = _sidecar(
        extracted={
            "positions": {
                "ukraine": "France pledges continued support.",
                "hybrid": "Germany does not tolerate breaches of international law.",
            }
        }
    )
    _, dropped = drop_absent_topics(data)
    assert dropped == []


def test_untouched_without_positions():
    for data in ({}, {"extracted": {}}, {"extracted": {"positions": {}}}):
        assert drop_absent_topics(data) == (data, [])


def test_does_not_mutate_input():
    data = _sidecar()
    drop_absent_topics(data)
    assert data["extracted"]["topics"] == ["ukraine", "hybrid"]
    assert data["issue_areas"] == ["ukraine", "hybrid"]
