"""Per-grouping scoping in pipeline/render.py — the filters that keep one
format's members, topics and copy out of another's site."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipeline.render import (
    COUNTRY_PROFILE,
    GROUPING_SITES,
    GROUPINGS,
    ISSUE_ORDER,
    RENDERED_GROUPINGS,
    WEIMAR,
    _stance_rows,
    build_convergence_clusters,
    compute_score_density,
    header_gradient,
    number_word,
)
from tests.conftest import event_dict

E3 = GROUPINGS["e3"]
TODAY = datetime(2026, 7, 21, tzinfo=UTC)


def _rated(source, date, topic, score, fp="data/events/x/2026-07/a.yaml"):
    return event_dict(
        source_name=source,
        date=date,
        file_path=fp,
        issue_areas=[topic],
        stances={topic: {"score": score, "evidence": "q"}},
    )


# --- config sanity ----------------------------------------------------------


def test_rendered_groupings_are_all_configured():
    for key in RENDERED_GROUPINGS:
        assert key in GROUPINGS, f"{key} is rendered but not loadable"


def test_every_member_has_a_country_profile():
    # render_grouping indexes COUNTRY_PROFILE directly for paths, swatches and
    # flag colours — a missing member would only fail at render time.
    for g in GROUPINGS.values():
        for actor in g.actors:
            profile = COUNTRY_PROFILE[actor]
            assert profile["sources"], f"{actor} has no ingested source"
            assert {"swatch", "path", "capital", "band", "spoke"} <= set(profile)


def test_display_order_matches_groupings_yaml_membership():
    # GROUPING_SITES fixes order only; it must never add or drop a member.
    for key, site in GROUPING_SITES.items():
        assert set(site["actors"]) == set(GROUPINGS[key].actors)


def test_grouping_topics_follow_the_global_issue_order():
    for g in GROUPINGS.values():
        assert list(g.topics) == [t for t in ISSUE_ORDER if t in set(g.topics)]


def test_e3_shape():
    assert E3.actors == ("FR", "DE", "UK")  # never alphabetical
    assert E3.topics == ("defence", "iran")
    assert E3.relevance_key == "e3_relevant"
    assert E3.slug == "e3"


# --- _stance_rows scoping ---------------------------------------------------


def test_stance_rows_drop_non_member_publishers():
    # A Polish MFA item can be e3_relevant (it concerns DE/FR/UK) but Poland is
    # not an E3 member, so it must not become an E3 actor.
    events = [
        _rated("polish_mfa", "2026-07-15", "defence", 2),
        _rated("uk_fcdo", "2026-07-15", "defence", 1),
    ]
    assert _stance_rows(events, E3) == [("2026-07-15", "UK", "defence", 1)]


def test_stance_rows_drop_topics_the_grouping_doesnt_track():
    # Sidecars carry stances across the union of all groupings' topics; the E3
    # tracks defence and Iran, not enlargement.
    events = [
        _rated("german_mfa", "2026-07-15", "enlargement", 2),
        _rated("german_mfa", "2026-07-16", "iran", 1),
    ]
    assert _stance_rows(events, E3) == [("2026-07-16", "DE", "iran", 1)]


def test_stance_rows_default_to_weimar():
    events = [_rated("uk_fcdo", "2026-07-15", "defence", 1)]
    assert _stance_rows(events) == []
    assert _stance_rows(events, E3) == [("2026-07-15", "UK", "defence", 1)]


# --- clusters ---------------------------------------------------------------


def test_clusters_scope_actors_and_topics_to_the_grouping():
    events = [
        _rated("uk_fcdo", "2026-07-14", "iran", 2, "data/events/uk/1.yaml"),
        _rated("france_diplomatie", "2026-07-16", "iran", 1, "data/events/fr/2.yaml"),
        # Poland is not an E3 member: on its own it can't make a cluster.
        _rated("polish_mfa", "2026-07-15", "iran", 1, "data/events/pl/3.yaml"),
    ]
    clusters = build_convergence_clusters(events, grouping=E3)
    assert len(clusters) == 1
    assert clusters[0]["area"] == "iran"
    assert clusters[0]["actors"] == ["FR", "UK"]  # display order, not discovery


def test_iran_never_clusters_on_the_weimar_site():
    # Iran is an E3 topic; the Weimar site must not surface it even though the
    # same German statements are relevant to both formats.
    events = [
        _rated("german_mfa", "2026-07-14", "iran", 1, "data/events/de/1.yaml"),
        _rated("france_diplomatie", "2026-07-15", "iran", 1, "data/events/fr/2.yaml"),
    ]
    assert build_convergence_clusters(events, grouping=WEIMAR) == []


# --- density ----------------------------------------------------------------


def test_density_slices_cover_exactly_the_groupings_members_and_topics():
    events = [
        _rated("uk_fcdo", "2026-07-20", "defence", 1),
        _rated("german_mfa", "2026-07-20", "defence", 2),
    ]
    density = compute_score_density(events, today=TODAY, grouping=E3)
    assert list(density) == ["ALL", "FR", "DE", "UK"]
    assert list(density["ALL"]) == ["overall", "defence", "iran"]
    assert "ukraine" not in density["ALL"]


# --- presentation helpers ---------------------------------------------------


@pytest.mark.parametrize("n,word", [(1, "one"), (3, "three"), (6, "six"), (11, "11")])
def test_number_word(n, word):
    assert number_word(n) == word


def test_header_gradient_has_one_band_per_member():
    for g in GROUPINGS.values():
        gradient = header_gradient(g)
        for actor in g.actors:
            assert COUNTRY_PROFILE[actor]["band"] in gradient
