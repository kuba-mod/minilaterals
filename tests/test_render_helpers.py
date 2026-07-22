"""Tier 2 — pure math/geometry helpers in pipeline/render.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipeline.render import (
    SCORES,
    _fmt_stance,
    _stance_norm,
    build_divergence_leaderboard,
    build_score_density_cells,
    cluster_key,
    compute_score_density,
    compute_topic_weekly_stances,
)
from tests.conftest import cluster_from_events, event_dict


def _stance_event(source, date, score, area="enlargement", *, title="t", position="p", evidence="e"):
    """A loaded-event dict carrying one topic stance, in render.py's shape."""
    return {
        "source_name": source,
        "date": date,
        "title": title,
        "source_url": f"https://example.test/{date}",
        "extracted": {"position": position, "stances": {area: {"score": score, "evidence": evidence}}},
    }


def test_stance_norm_maps_range():
    assert _stance_norm(-2.0) == pytest.approx(0.0)
    assert _stance_norm(0.0) == pytest.approx(0.5)
    assert _stance_norm(2.0) == pytest.approx(1.0)


def test_fmt_stance_shows_sign():
    assert _fmt_stance(1.3) == "+1.3"
    assert _fmt_stance(-0.5) == "-0.5"
    assert _fmt_stance(0.0) == "+0.0"


def test_cluster_key_stable_and_order_independent():
    c1 = cluster_from_events(
        "ukraine",
        {"DE": [event_dict(file_path="a.yaml")], "FR": [event_dict(file_path="b.yaml")]},
    )
    c2 = cluster_from_events(
        "ukraine",
        {"FR": [event_dict(file_path="b.yaml")], "DE": [event_dict(file_path="a.yaml")]},
    )
    key = cluster_key(c1)
    assert len(key) == 12
    assert key == cluster_key(c2)  # sorted paths → stable regardless of actor order


# --- score density (heatmap replacing the averaged-line chart) ------------


def test_score_density_bins_into_the_right_score_row_and_actor_slice():
    events = [
        # Windows are anchored to `today` (2026-06-29), not the calendar:
        # the rightmost window is the 7 days ending on `today`
        # (2026-06-23..2026-06-29), the one before it ends 7 days earlier
        # (..2026-06-22). So 06-22 lands in the older window, and 06-23/06-24
        # — despite being calendar-adjacent to 06-22 — land in the newest one.
        _stance_event("german_mfa", "2026-06-22", 2, "enlargement"),
        _stance_event("polish_mfa", "2026-06-23", 0, "enlargement"),
        _stance_event("polish_mfa", "2026-06-24", 0, "enlargement"),
    ]
    density = compute_score_density(events, today=datetime(2026, 6, 29, tzinfo=UTC))
    all_enl = density["ALL"]["enlargement"]
    assert all_enl["weeks"] == ["2026-06-22", "2026-06-29"]
    old_idx, new_idx = 0, 1
    assert all_enl["grid"][SCORES.index(2)][old_idx] == 1
    assert all_enl["grid"][SCORES.index(0)][new_idx] == 2
    assert all_enl["row_totals"] == [1, 0, 2, 0, 0]
    assert all_enl["grand_total"] == 3
    # A per-capital slice isolates just that capital's statements.
    pl_enl = density["PL"]["enlargement"]
    assert pl_enl["row_totals"] == [0, 0, 2, 0, 0]
    de_enl = density["DE"]["enlargement"]
    assert de_enl["row_totals"] == [1, 0, 0, 0, 0]


def test_score_density_last_window_is_a_full_window_not_a_partial_calendar_week():
    # Regression: the rightmost column must cover the full window_days before
    # the cutoff, even when the cutoff falls mid-(calendar-)week — otherwise
    # the most recent column under-reports relative to older, complete columns.
    events = [
        _stance_event("german_mfa", "2026-07-16", 1, "enlargement"),  # Thursday
        _stance_event("france_diplomatie", "2026-07-21", 1, "enlargement"),  # Tuesday (the cutoff)
    ]
    density = compute_score_density(events, today=datetime(2026, 7, 21, tzinfo=UTC))
    all_enl = density["ALL"]["enlargement"]
    assert all_enl["weeks"][-1] == "2026-07-21"
    # Both statements fall in the 7 days ending on the cutoff (07-15..07-21),
    # so both land in the rightmost column, not split across a Monday-anchored
    # calendar-week boundary that would fall between them.
    assert all_enl["grid"][SCORES.index(1)][-1] == 2


def test_score_density_handles_negative_two_in_the_bottom_row():
    events = [_stance_event("german_mfa", "2026-06-22", -2, "enlargement")]
    density = compute_score_density(events, today=datetime(2026, 6, 29, tzinfo=UTC))
    grid = density["ALL"]["enlargement"]["grid"]
    assert SCORES[-1] == -2
    assert grid[-1] == [1, 0]  # last row is -2; lands in its own (first) week
    assert grid[0] == [0, 0]  # +2 row stays empty


def test_score_density_includes_executive_office_statements():
    # Regression: compute_score_density (like build_country_line_series before
    # it) must go through the shared _stance_rows() helper, not a narrower
    # ministry-only map — otherwise a country's executive office (chancellery/
    # Élysée/KPRM) statements are invisible to the chart, as happened with the
    # 2026-07-11 Polish PM statement rated enlargement: -1.
    events = [_stance_event("polish_pm", "2026-07-11", -1, "enlargement")]
    density = compute_score_density(events, today=datetime(2026, 7, 13, tzinfo=UTC))
    assert density["PL"]["enlargement"]["grand_total"] == 1
    assert density["ALL"]["enlargement"]["row_totals"][SCORES.index(-1)] == 1


def test_score_density_weeks_cap_to_trailing_window():
    events = [
        _stance_event("german_mfa", "2026-02-02", 1, "ukraine"),
        _stance_event("france_diplomatie", "2026-06-22", 1, "ukraine"),
        _stance_event("polish_mfa", "2026-06-29", 1, "ukraine"),
    ]
    full = compute_score_density(events, today=datetime(2026, 6, 29, tzinfo=UTC))
    capped = compute_score_density(events, today=datetime(2026, 6, 29, tzinfo=UTC), weeks=3)
    assert len(capped["ALL"]["ukraine"]["weeks"]) == 3
    assert len(full["ALL"]["ukraine"]["weeks"]) > 3
    assert capped["ALL"]["ukraine"]["weeks"][-1] == full["ALL"]["ukraine"]["weeks"][-1]


# --- score density SVG geometry ---------------------------------------------


def test_score_density_cells_totals_colours_and_empty_cells():
    grid = [[0, 1], [0, 0], [0, 2], [0, 0], [1, 0]]  # +2, +1, 0, -1, -2 rows
    row_totals = [1, 0, 2, 0, 1]
    weeks = ["2026-06-16", "2026-06-23"]  # window end dates, i.e. edition dates (both Tuesdays)
    out = build_score_density_cells(grid, row_totals, weeks)
    assert out["weeks"] == weeks
    assert out["grand_total"] == sum(row_totals)
    # Every nonzero grid cell is marked filled; counts implied are recoverable
    # only via the cell's tooltip, so check tooltip-derived counts sum to the
    # grand total instead of re-deriving opacity.
    all_cells = [c for r in out["rows"] for c in r["cells"]]
    total_from_tooltips = sum(int(c["tooltip"].rsplit(" ", 2)[1]) for c in all_cells if c["filled"])
    assert total_from_tooltips == sum(row_totals)
    # A zero-count cell is unfilled (renders as a dashed placeholder, not a
    # coloured rect) and carries no opacity.
    empty_cells = [c for c in all_cells if not c["filled"]]
    assert empty_cells and all(c["opacity"] == 0.0 for c in empty_cells)
    # Rows are diverging by colour: +2/+1 are (different) greens, 0 is amber,
    # -1/-2 share the same red — not a single reused "gold" hue.
    by_label = {r["label"]: r for r in out["rows"]}
    assert by_label["+2"]["color"] != by_label["+1"]["color"]  # two green shades, not identical
    assert by_label["-1"]["color"] == by_label["-2"]["color"]  # both red
    assert by_label["+2"]["color"] != by_label["-2"]["color"]
    # The neutral row reads "stance 0", not "stance +0" (f"{0:+d}" would wrongly sign it).
    zero_cell = next(c for c in by_label["0"]["cells"] if c["filled"])
    assert "stance 0" in zero_cell["tooltip"] and "stance +0" not in zero_cell["tooltip"]
    # Row margin totals/labels/descriptions match input, in SCORES order.
    assert [r["label"] for r in out["rows"]] == ["+2", "+1", "0", "-1", "-2"]
    assert [r["desc"] for r in out["rows"]] == ["advances", "supports", "neutral", "hedges", "opposes"]
    assert [r["total"] for r in out["rows"]] == row_totals
    # Columns are labelled by their window's end date (the edition date).
    assert out["edition_labels"] == ["16 Jun", "23 Jun"]
    assert out["edition_full_labels"] == ["Tuesday 16 Jun", "Tuesday 23 Jun"]


# --- divergence leaderboard (orders pills + clusters) ----------------------


def test_leaderboard_ranks_by_current_week_spread():
    events = [
        _stance_event("german_mfa", "2026-06-29", 2, "enlargement"),
        _stance_event("polish_mfa", "2026-06-29", 0, "enlargement"),
        _stance_event("german_mfa", "2026-06-29", 1, "ukraine"),
        _stance_event("france_diplomatie", "2026-06-29", 1, "ukraine"),
    ]
    topic_weekly = compute_topic_weekly_stances(events, today=datetime(2026, 6, 29, tzinfo=UTC))
    board = build_divergence_leaderboard(topic_weekly)
    ranked = [r for r in board if not r["quiet"]]
    # enlargement (spread 2, Divergent) outranks ukraine (spread 0, Aligned).
    assert ranked[0]["area"] == "enlargement"
    assert ranked[0]["label"] == "Divergent"
    assert ranked[0]["spread"] == pytest.approx(2.0)
    assert ranked[-1]["area"] == "ukraine"
