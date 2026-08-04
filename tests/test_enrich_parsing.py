"""Tier 1 — LLM-response parsing/cleaning in pipeline/enrich.py."""

from __future__ import annotations

import json

import pytest

from pipeline import enrich
from pipeline.enrich import _clean_evidence, _clean_stance, _parse_json, _validate_llm_shape

# --- _parse_json -----------------------------------------------------------


def test_parse_json_plain():
    assert _parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_strips_bare_fence():
    raw = '```\n{"a": 1}\n```'
    assert _parse_json(raw) == {"a": 1}


def test_parse_json_strips_json_tagged_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _parse_json(raw) == {"a": 1}


def test_parse_json_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        _parse_json("not json at all")


# --- _validate_llm_shape -----------------------------------------------------


def test_validate_llm_shape_accepts_flat_actors_and_formats():
    _validate_llm_shape({"actors": ["FR", "DE"], "explicit_formats": ["weimar"]})
    _validate_llm_shape({"actors": [], "explicit_formats": []})


def test_validate_llm_shape_accepts_missing_fields():
    _validate_llm_shape({})


@pytest.mark.parametrize(
    "actors",
    [
        [["FR"]],
        ["FR", []],
        ["FR", 1],
        "FR",
    ],
)
def test_validate_llm_shape_rejects_nested_or_non_string_actors(actors):
    with pytest.raises(ValueError, match="actors"):
        _validate_llm_shape({"actors": actors})


@pytest.mark.parametrize(
    "formats",
    [
        "weimar",
        [["weimar"]],
        ["weimar", 1],
    ],
)
def test_validate_llm_shape_rejects_bad_explicit_formats(formats):
    with pytest.raises(ValueError, match="explicit_formats"):
        _validate_llm_shape({"explicit_formats": formats})


# --- _clean_stance ---------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (2, 2),
        (-2, -2),
        (0, 0),
        ("1", 1),
        (1.9, 1),  # truncated toward zero by int()
        ("abc", None),
        (None, None),
    ],
)
def test_clean_stance(value, expected):
    assert _clean_stance(value) == expected


@pytest.mark.parametrize("value", [3, -5, 4, -3])
def test_clean_stance_raises_out_of_range(value):
    # A numeric stance outside [-2, 2] means the model ignored the rubric —
    # that's worth surfacing, not silently clamping into range.
    with pytest.raises(ValueError):
        _clean_stance(value)


# --- _clean_evidence -------------------------------------------------------


def test_clean_evidence_keeps_genuine_quote(monkeypatch):
    monkeypatch.setattr(enrich, "GOALS", {"weimar": {"ukraine": "long-term support for Ukraine"}})
    kept = _clean_evidence("Germany will provide EUR 5bn in aid", "ukraine", "weimar")
    assert kept == "Germany will provide EUR 5bn in aid"


def test_clean_evidence_drops_goal_copy(monkeypatch):
    goal = "The Weimar Triangle commits to long-term support for Ukraine"
    monkeypatch.setattr(enrich, "GOALS", {"weimar": {"ukraine": goal}})
    # Evidence copied verbatim from the goal statement must be dropped.
    assert _clean_evidence(goal, "ukraine", "weimar") == ""


def test_clean_evidence_drops_substring_of_goal(monkeypatch):
    goal = "The Weimar Triangle commits to long-term support for Ukraine"
    monkeypatch.setattr(enrich, "GOALS", {"weimar": {"ukraine": goal}})
    assert _clean_evidence("long-term support for Ukraine", "ukraine", "weimar") == ""


def test_clean_evidence_empty_input():
    assert _clean_evidence(None, "ukraine", "weimar") == ""
    assert _clean_evidence("   ", "ukraine", "weimar") == ""


# --- _rate_stances: unrated is not neutral ----------------------------------


class _OneShotProvider:
    def __init__(self, payload):
        self.payload = payload

    def call(self, prompt):  # noqa: ARG002
        return json.dumps(self.payload)


def _rate(payload, topics, monkeypatch, goals=None):
    monkeypatch.setattr(enrich, "GOALS", goals or {"weimar": {t: f"goal for {t}" for t in topics}})
    monkeypatch.setattr(
        enrich, "GROUPINGS", {"weimar": enrich.Grouping("weimar", "Weimar Triangle", ["DE", "FR", "PL"], topics)}
    )
    return enrich._rate_stances(_OneShotProvider(payload), "Poland", "t", "body", "weimar", topics)


def test_rate_stances_drops_neutral_without_evidence(monkeypatch):
    # score 0 + no quote is the model saying "found nothing", not "neutral" —
    # storing it would drag the cluster mean on the strength of an absence.
    out = _rate({"defence": {"stance": 0, "evidence": None}}, ["defence"], monkeypatch)
    assert out == {}


def test_rate_stances_keeps_neutral_with_evidence(monkeypatch):
    out = _rate({"defence": {"stance": 0, "evidence": "rozmowy się odbyły"}}, ["defence"], monkeypatch)
    assert out == {"defence": {"score": 0, "evidence": "rozmowy się odbyły"}}


def test_rate_stances_keeps_nonzero_without_evidence(monkeypatch):
    # A nonzero score is a real claim with lost provenance, not an absence.
    out = _rate({"defence": {"stance": 1, "evidence": ""}}, ["defence"], monkeypatch)
    assert out == {"defence": {"score": 1, "evidence": ""}}


def test_rate_stances_drops_neutral_whose_evidence_was_goal_copy(monkeypatch):
    goals = {"weimar": {"defence": "A European defence pillar complementary to NATO"}}
    out = _rate(
        {"defence": {"stance": 0, "evidence": "A European defence pillar complementary to NATO"}},
        ["defence"],
        monkeypatch,
        goals=goals,
    )
    assert out == {}


def test_rate_stances_drops_only_the_unrated_topic(monkeypatch):
    payload = {
        "ukraine": {"stance": 2, "evidence": "przekażemy 5 mld"},
        "defence": {"stance": 0, "evidence": None},
    }
    out = _rate(payload, ["ukraine", "defence"], monkeypatch)
    assert set(out) == {"ukraine"}


# --- asserts_absence / _drop_absent_topics ----------------------------------


@pytest.mark.parametrize(
    "position",
    [
        "France does not explicitly address hybrid threats in the provided text.",
        "The text does not provide specific information on EU enlargement goals.",
        "The statement focuses on economic resilience but does not specifically mention climate neutrality.",
        "Green transition is not explicitly mentioned as a topic of discussion between France and Malaysia.",
        "Poland's participation implies adherence to democratic principles, though no explicit statement is made.",
        "The text mentions the agenda but does not provide a specific stance regarding enlargement.",
    ],
)
def test_asserts_absence_catches_meta_commentary(position):
    assert enrich.asserts_absence(position)


@pytest.mark.parametrize(
    "position",
    [
        # The critical negatives: real positions that happen to contain "does not".
        # Deleting one of these would silently discard a genuine negative stance.
        "Germany emphasizes that the G7 is a community of values that does not tolerate breaches of international law.",
        "France does not provide weapons to the parties to the conflict.",
        "Poland does not accept the proposed migration pact.",
        "Germany will not support further accession talks until the reforms land.",
        "France stresses continued support for Ukraine as a key priority.",
        "",
    ],
)
def test_asserts_absence_leaves_real_positions_alone(position):
    assert not enrich.asserts_absence(position)


def test_drop_absent_topics_splits_kept_and_dropped():
    positions = {
        "ukraine": "France stresses continued support for Ukraine.",
        "defence": "France highlights defence industry cooperation with Sweden.",
        "hybrid": "France does not explicitly address hybrid threats in the provided text.",
        "rule_of_law": "France does not explicitly address rule of law in the provided text.",
    }
    kept, dropped = enrich._drop_absent_topics(list(positions), positions)
    assert kept == ["ukraine", "defence"]
    assert dropped == ["hybrid", "rule_of_law"]


def test_drop_absent_topics_keeps_everything_when_all_real():
    positions = {"ukraine": "France pledges EUR 5bn.", "defence": "France signs a frigate deal."}
    kept, dropped = enrich._drop_absent_topics(list(positions), positions)
    assert kept == ["ukraine", "defence"]
    assert dropped == []


def test_drop_absent_topics_tolerates_missing_position():
    kept, dropped = enrich._drop_absent_topics(["ukraine"], {})
    assert (kept, dropped) == (["ukraine"], [])
