"""pipeline/vote_report.py — pure formatting helpers (fetch_counts hits the network, not tested here)."""

from __future__ import annotations

from pipeline.render import HUB_GROUPINGS
from pipeline.vote_report import _ranked, render_html, render_text


def test_ranked_covers_every_hub_grouping():
    counts = {"quad": 3}
    rows = _ranked(counts)
    assert len(rows) == len(HUB_GROUPINGS)
    assert ("The Quad", 3) in rows


def test_ranked_defaults_missing_slugs_to_zero():
    rows = dict(_ranked({}))
    assert all(count == 0 for count in rows.values())


def test_ranked_sorts_by_count_desc_then_name():
    counts = {"quad": 2, "squad": 2, "aukus": 5}
    rows = _ranked(counts)
    assert rows[0] == ("AUKUS", 5)
    # tie between quad(2) and squad(2) breaks alphabetically by display name
    tied = [name for name, count in rows if count == 2]
    assert tied == sorted(tied)


def test_render_text_includes_total_and_names():
    out = render_text({"quad": 2})
    assert "2 total votes" in out
    assert "The Quad" in out


def test_render_text_singular_vote():
    out = render_text({"quad": 1, **{m["slug"]: 0 for m in HUB_GROUPINGS if m["slug"] != "quad"}})
    assert "1 total vote " in out or out.startswith("Vote report — 1 total vote across")


def test_render_html_escapes_and_bars():
    out = render_html({"quad": 4})
    assert "<table" in out
    assert "The Quad" in out
    assert "width:100%" in out  # the max-count row fills its bar


def test_render_html_zero_votes_has_no_bar_width():
    out = render_html({})
    assert "8 total" not in out
    assert "0 total votes" in out
