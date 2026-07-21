"""Multi-grouping relevance + actor vocabulary in pipeline/enrich.py."""

from __future__ import annotations

import pytest

from pipeline import enrich
from pipeline.enrich import (
    GROUPINGS,
    _grouping_relevance,
    _normalize_actors,
    _normalize_formats,
)

# --- config sanity ----------------------------------------------------------


def test_expected_groupings_present():
    assert set(GROUPINGS) == {"weimar", "e3", "visegrad", "baltic", "aukus"}
    assert GROUPINGS["e3"].member_set == {"DE", "FR", "UK"}
    assert GROUPINGS["visegrad"].member_set == {"PL", "CZ", "SK", "HU"}
    assert GROUPINGS["baltic"].member_set == {"EE", "LV", "LT"}
    assert GROUPINGS["aukus"].member_set == {"AU", "UK", "US"}


def test_actor_vocabulary_covers_every_member():
    # Every member code in any grouping must be normalizable back to itself.
    for g in GROUPINGS.values():
        for code in g.members:
            assert _normalize_actors([code], "unknown_source") == [code]


# --- _normalize_actors ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (["United Kingdom"], ["UK"]),
        (["GB"], ["UK"]),
        (["USA"], ["US"]),
        (["Australia"], ["AU"]),
        (["Czechia"], ["CZ"]),
        (["Slovak"], ["SK"]),
        (["Hungary"], ["HU"]),
        (["Estonia", "Latvia", "Lithuania"], ["EE", "LV", "LT"]),
        (["france", "GERMANY"], ["DE", "FR"]),  # returned in canonical order
        (["Narnia"], []),  # unknown dropped
    ],
)
def test_normalize_actors_aliases(raw, expected):
    assert set(_normalize_actors(raw, "unknown_source")) == set(expected)


def test_normalize_actors_folds_source_country():
    # A UK FCDO release with no country named still counts the UK.
    assert _normalize_actors([], "uk_fcdo") == ["UK"]
    assert _normalize_actors([], "estonian_mfa") == ["EE"]


# --- _normalize_formats -----------------------------------------------------


def test_normalize_formats_keeps_known_keys_only():
    assert _normalize_formats(["AUKUS", "weimar", "nato"]) == {"aukus", "weimar"}
    assert _normalize_formats(None) == set()


# --- _grouping_relevance ----------------------------------------------------


def _matched(flags):
    return {k for k, v in flags.items() if v}


def test_relevance_all_members_present_is_a_signal():
    flags = _grouping_relevance(["DE", "FR", "UK"], set(), ["iran"], "uk_fcdo")
    assert flags["e3_relevant"] and flags["e3_signal"]
    # Not a Weimar or AUKUS event.
    assert not flags["weimar_relevant"]
    assert not flags["aukus_relevant"]


def test_relevance_two_members_on_tracked_topic():
    flags = _grouping_relevance(["UK", "US"], set(), ["defence"], "us_state")
    assert flags["aukus_relevant"]
    assert not flags["aukus_signal"]  # only 2 of 3 members, no explicit mention


def test_relevance_known_actor_single_country():
    # Estonia alone on energy: baltic member + tracked topic via known-actor rule.
    flags = _grouping_relevance(["EE"], set(), ["energy"], "estonian_mfa")
    assert _matched(flags) == {"baltic_relevant"}


def test_relevance_explicit_format_is_a_signal():
    # Text explicitly names AUKUS even with a single actor present.
    flags = _grouping_relevance(["US"], {"aukus"}, ["submarines"], "us_state")
    assert flags["aukus_relevant"] and flags["aukus_signal"]


def test_widened_vocabulary_does_not_leak_into_weimar():
    # Two non-Weimar countries must never make an item Weimar-relevant.
    flags = _grouping_relevance(["UK", "US"], set(), ["defence"], "us_state")
    assert not flags["weimar_relevant"]
    assert not flags["trilateral_signal"]


def test_weimar_backward_compatible():
    # A DE MFA Ukraine release is Weimar-relevant exactly as before.
    flags = _grouping_relevance(["DE"], set(), ["ukraine"], "german_mfa")
    assert flags["weimar_relevant"]
    assert not flags["trilateral_signal"]


def test_topic_outside_grouping_does_not_make_relevant():
    # green_transition isn't an AUKUS topic, so US+UK on it isn't AUKUS-relevant.
    flags = _grouping_relevance(["US", "UK"], set(), ["green_transition"], "us_state")
    assert not flags["aukus_relevant"]


def test_prompt_vocabulary_matches_config():
    # The actor/topic lists injected into the prompt are derived from the config.
    assert set(enrich._ACTOR_ORDER) == {c for g in GROUPINGS.values() for c in g.members}
    assert set(enrich.ALL_TOPICS) == {t for g in GROUPINGS.values() for t in g.topics}
