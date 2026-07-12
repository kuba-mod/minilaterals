"""Tier 1 — LLM-response parsing/cleaning in pipeline/enrich.py."""

from __future__ import annotations

import json

import pytest

from pipeline import enrich
from pipeline.enrich import _clean_evidence, _clean_stance, _parse_json

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
    monkeypatch.setattr(enrich, "WEIMAR_GOALS", {"ukraine": "long-term support for Ukraine"})
    assert _clean_evidence("Germany will provide EUR 5bn in aid", "ukraine") == "Germany will provide EUR 5bn in aid"


def test_clean_evidence_drops_goal_copy(monkeypatch):
    goal = "The Weimar Triangle commits to long-term support for Ukraine"
    monkeypatch.setattr(enrich, "WEIMAR_GOALS", {"ukraine": goal})
    # Evidence copied verbatim from the goal statement must be dropped.
    assert _clean_evidence(goal, "ukraine") == ""


def test_clean_evidence_drops_substring_of_goal(monkeypatch):
    goal = "The Weimar Triangle commits to long-term support for Ukraine"
    monkeypatch.setattr(enrich, "WEIMAR_GOALS", {"ukraine": goal})
    assert _clean_evidence("long-term support for Ukraine", "ukraine") == ""


def test_clean_evidence_empty_input():
    assert _clean_evidence(None, "ukraine") == ""
    assert _clean_evidence("   ", "ukraine") == ""
