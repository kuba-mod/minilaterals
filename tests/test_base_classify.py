"""Tier 1 — pure classification/scoring logic in pipeline/sources/base.py."""

from __future__ import annotations

import pytest

from pipeline.sources.base import Event, _match_any
from tests.conftest import make_event


def test_match_any_case_insensitive():
    assert _match_any([r"\bUkraine\b"], "support for ukraine")
    assert _match_any([r"\bNATO\b"], "the NATO summit")
    assert not _match_any([r"\bUkraine\b"], "no relevant country here")


# --- actor detection -------------------------------------------------------


def test_actors_detected_by_english_terms():
    ev = make_event(title="Germany and France meet", text="Poland also attended")
    assert sorted(ev.actors) == ["DE", "FR", "PL"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Statement from Berlin about Deutschland", "DE"),
        ("Allemagne reaffirms its position", "DE"),
        ("A meeting in Paris with Macron", "FR"),
        ("Le ministre français a parlé", "FR"),
        ("Warsaw and Tusk welcomed the deal", "PL"),
        ("Pologne soutient l'initiative", "PL"),
    ],
)
def test_actors_detected_multilingual(text, expected):
    ev = make_event(source_name="other_source", title="", text=text)
    assert expected in ev.actors


def test_no_actors_when_absent():
    ev = make_event(source_name="other_source", title="A local matter", text="nothing tracked here")
    assert ev.actors == []


# --- issue areas -----------------------------------------------------------


def test_issue_areas_detected():
    ev = make_event(title="Ukraine and NATO defence", text="hybrid cyber threats and enlargement")
    assert set(ev.issue_areas) >= {"ukraine", "defence", "hybrid", "enlargement"}


def test_issue_area_absent():
    ev = make_event(source_name="other_source", title="Cultural exchange programme", text="art and music")
    assert ev.issue_areas == []


# --- weimar_relevant: the three branches -----------------------------------


def test_relevant_via_trilateral_explicit():
    ev = make_event(source_name="other_source", title="Weimar Triangle statement", text="no issues named")
    assert ev.trilateral_signal is True
    assert ev.weimar_relevant is True


def test_relevant_via_all_three_actors():
    ev = make_event(source_name="other_source", title="Germany, France and Poland", text="met today")
    assert ev.trilateral_signal is True
    assert ev.weimar_relevant is True


def test_relevant_via_two_actors_plus_issue():
    ev = make_event(source_name="other_source", title="Germany and Poland on Ukraine", text="")
    assert ev.trilateral_signal is False
    assert ev.weimar_relevant is True


def test_two_actors_without_issue_not_relevant():
    ev = make_event(source_name="other_source", title="Germany and Poland cultural week", text="music festival")
    assert ev.weimar_relevant is False


def test_mfa_single_country_on_issue_is_relevant():
    # The MFA-source branch: a single-country MFA item on a tracked topic counts.
    ev = make_event(source_name="german_mfa", title="Statement on Ukraine", text="Germany comments")
    assert ev.actors == ["DE"]
    assert ev.weimar_relevant is True


def test_non_mfa_single_country_on_issue_not_relevant():
    # Same content from a non-MFA source is NOT relevant (needs 2+ actors).
    ev = make_event(source_name="other_source", title="Statement on Ukraine", text="Germany comments")
    assert ev.actors == ["DE"]
    assert ev.weimar_relevant is False


def test_mfa_item_without_issue_not_relevant():
    ev = make_event(source_name="german_mfa", title="Berlin cultural festival", text="art exhibition opens")
    assert ev.issue_areas == []
    assert ev.weimar_relevant is False


# --- weimar_score math -----------------------------------------------------


def test_score_explicit_plus_issue():
    # explicit (+0.5) + 1 issue (0.05) — single actor mentioned
    ev = make_event(source_name="other_source", title="Weimar Triangle on Ukraine", text="Germany speaks")
    assert ev.weimar_score == pytest.approx(0.55)


def test_score_three_actors_two_issues():
    # 3 actors (+0.3) + 2 issues (0.10), no explicit trilateral phrase
    ev = make_event(
        source_name="other_source",
        title="Germany France Poland",
        text="Ukraine and NATO defence discussed",
    )
    assert ev.weimar_score == pytest.approx(0.40)


def test_score_two_actors_one_issue():
    ev = make_event(source_name="other_source", title="Germany and France", text="Ukraine talks")
    # 2 actors (+0.15) + 1 issue (0.05)
    assert ev.weimar_score == pytest.approx(0.20)


def test_score_issue_contribution_capped():
    # Many issue areas — issue contribution caps at 0.2.
    ev = make_event(
        source_name="other_source",
        title="Germany France Poland",
        text="Ukraine NATO defence hybrid cyber enlargement accession climate rule of law democracy",
    )
    # explicit? no. 3 actors (0.3) + capped issues (0.2) = 0.5
    assert ev.weimar_score == pytest.approx(0.5)


def test_score_capped_at_one():
    ev = make_event(
        source_name="other_source",
        title="Weimar Triangle: Germany France Poland",
        text="Ukraine NATO defence hybrid cyber enlargement climate democracy rule of law",
    )
    # explicit 0.5 + 3 actors 0.3 + issues capped 0.2 = 1.0
    assert ev.weimar_score == 1.0
    assert ev.weimar_score <= 1.0


def test_score_is_rounded_to_three_places():
    ev = make_event(source_name="other_source", title="Germany and France", text="Ukraine")
    assert ev.weimar_score == round(ev.weimar_score, 3)


def test_classify_returns_self():
    ev = Event(
        source_name="german_mfa",
        title="Ukraine",
        text="",
        source_url="u",
        source_lang="en",
        source_published_at="2026-06-01T00:00:00Z",
        date="2026-06-01",
    )
    assert ev.classify() is ev


# --- hashing and paths -----------------------------------------------------


def test_content_hash_deterministic_and_short():
    ev = make_event(title="A title", source_url="https://x.test/1")
    h = ev.content_hash()
    assert len(h) == 8
    assert h == make_event(title="A title", source_url="https://x.test/1").content_hash()


def test_content_hash_depends_on_url_and_title():
    a = make_event(title="A", source_url="https://x.test/1").content_hash()
    b = make_event(title="B", source_url="https://x.test/1").content_hash()
    c = make_event(title="A", source_url="https://x.test/2").content_hash()
    assert a != b and a != c


def test_output_path_layout():
    ev = make_event(source_name="german_mfa", date="2026-06-15", source_url="https://x.test/1", title="T")
    p = ev.output_path()
    assert p.parts[-3:-1] == ("german_mfa", "2026-06")
    assert p.name == f"2026-06-15-{ev.content_hash()}.yaml"
    assert p.parts[0] == "data" and p.parts[1] == "events"


def test_enriched_path_uses_enriched_base():
    ev = make_event(source_name="polish_mfa", date="2026-06-15")
    assert ev.enriched_path().parts[:2] == ("data", "enriched")


def test_output_path_unknown_month_when_dateless():
    ev = make_event(date="")
    assert "unknown" in ev.output_path().parts
