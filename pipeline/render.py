#!/usr/bin/env python3
"""
Weimar Triangle tracker — static HTML renderer.

Reads data/events/**/*.yaml + data/meetings.yaml and renders HTML pages to docs/
(or --output DIR). The Meetings page itself is not currently rendered — see the
"docs/meetings/index.html" comment below — so data/milestones.yaml and
data/annual.yaml aren't read here right now either.

Usage:
    python -m pipeline.render               # renders to docs/
    python -m pipeline.render --output /tmp/test
    python -m pipeline.render --as-of 2026-06-24   # render a past edition

The site is rendered "as of" an edition cutoff date: events dated after it are
excluded and all rolling windows anchor to it. The cutoff comes from --as-of,
else data/edition.yaml (written by the weekly CI edition cut), else today.
This makes rendering a pure function of (templates, data, cutoff), so a
layout-only merge redeploys the same frozen edition instead of leaking data
ingested since the last cut.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = ROOT / "pipeline" / "templates"
EDITION_FILE = ROOT / "data" / "edition.yaml"


def resolve_edition_date(as_of: str | None = None) -> datetime:
    """
    The date the site is rendered "as of". Resolution: --as-of flag →
    data/edition.yaml cutoff → today (dev fallback).
    """
    if as_of is None and EDITION_FILE.exists():
        loaded = yaml.safe_load(EDITION_FILE.read_text(encoding="utf-8")) or {}
        cutoff = loaded.get("cutoff")
        as_of = str(cutoff) if cutoff else None
    if as_of:
        return datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=UTC)
    return datetime.now(UTC)


SOURCE_ACTOR = {
    "german_mfa": "DE",
    "france_diplomatie": "FR",
    "polish_mfa": "PL",
    "german_chancellery": "DE",
    "elysee": "FR",
    "polish_pm": "PL",
}

SOURCE_LABELS = {
    "german_mfa": "German MFA",
    "france_diplomatie": "France Diplomatie",
    "polish_mfa": "Polish MFA",
    "german_chancellery": "German Chancellery",
    "elysee": "Élysée",
    "polish_pm": "Polish PM Chancellery",
}

ACTOR_LABELS = {
    "DE": "Germany",
    "FR": "France",
    "PL": "Poland",
}

WEIMAR_ACTORS = ["FR", "DE", "PL"]

# Each Weimar country now speaks through two principal sources: its foreign
# ministry and its executive office (chancellery / presidency / PM office).
# Institution names are kept in the native language. No office-holder names —
# those change and go stale; the institution is what we actually track.
COUNTRY_PROFILE = {
    "FR": {
        "swatch": "fr",
        "path": "france",
        "sources": [
            {"type": "Foreign ministry", "institution": "Quai d'Orsay", "source": "france_diplomatie"},
            {"type": "Executive office", "institution": "Élysée", "source": "elysee"},
        ],
    },
    "DE": {
        "swatch": "de",
        "path": "germany",
        "sources": [
            {"type": "Foreign ministry", "institution": "Auswärtiges Amt", "source": "german_mfa"},
            {"type": "Executive office", "institution": "Bundeskanzleramt", "source": "german_chancellery"},
        ],
    },
    "PL": {
        "swatch": "pl",
        "path": "poland",
        "sources": [
            {"type": "Foreign ministry", "institution": "MSZ", "source": "polish_mfa"},
            {"type": "Executive office", "institution": "Kancelaria Prezesa Rady Ministrów", "source": "polish_pm"},
        ],
    },
}

# Flattened source_name -> {type, institution}, for labelling which of a
# country's two voices each event came from.
SOURCE_META = {
    s["source"]: {"type": s["type"], "institution": s["institution"]}
    for prof in COUNTRY_PROFILE.values()
    for s in prof["sources"]
}

# Ingest method + native page language per source, for the sources-table
# columns. Matches each ingester's primary (native-language) fetch path —
# see design principle #9: native newsroom first, English fallback only if
# that native path fails.
SOURCE_INGEST = {
    "german_mfa": ("RSS", "DE"),
    "france_diplomatie": ("RSS", "FR"),
    "polish_mfa": ("HTML scraper", "PL"),
    "german_chancellery": ("HTML scraper", "DE"),
    "elysee": ("HTML scraper", "FR"),
    "polish_pm": ("HTML scraper", "PL"),
}

ACTOR_COLORS = {
    "DE": "#9a6a1f",
    "FR": "#1f4279",
    "PL": "#b22823",
    "EU": "#6a7a9a",
}

ISSUE_LABELS = {
    "ukraine": "Ukraine",
    "defence": "Defence",
    "hybrid": "Hybrid Threats",
    "enlargement": "Enlargement",
    "green_transition": "Green Transition",
    "rule_of_law": "Rule of Law",
}

ISSUE_ORDER = [
    "ukraine",
    "defence",
    "hybrid",
    "enlargement",
    "green_transition",
    "rule_of_law",
]

ERA_COLORS = {
    "founding": "#5a8a5a",
    "accession": "#4a6a8a",
    "dormancy": "#7a6a3a",
    "crisis1": "#8a6030",
    "limbo": "#5a4a3a",
    "revival": "#5a6a4a",
    "renaissance": "#9a8a28",
}

ERA_LABELS = {
    "founding": "Founding Era",
    "accession": "Accession Era",
    "dormancy": "Dormancy Era",
    "crisis1": "Crimea Response",
    "limbo": "Limbo Era",
    "revival": "Ukraine Revival",
    "renaissance": "Renaissance",
}

TYPE_COLORS = {
    "FM": "#c4b240",
    "FM+": "#9a8a28",
    "Summit": "#5a8a9a",
    "Defence": "#6a5a9a",
    "Finance": "#5a8a5a",
    "Parl.": "#666666",
    "Sectoral": "#6a7a5a",
    "Statement": "#7a5a3a",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

EVENTS_DIR = ROOT / "data" / "events"
ENRICHED_DIR = ROOT / "data" / "enriched"
COMMENTARY_FILE = ROOT / "data" / "commentary.json"

# Stance-based alignment: each event×topic carries an LLM-judged stance in -2..+2
# vs. the Weimar goal. The label is a function of two independent axes:
#   - spread: how far apart the per-country mean stances are from each other
#     (≤0.5 = same bucket, ≤1.5 = adjacent buckets) — "do the capitals agree?"
#   - overall: the cross-country mean stance itself — "do they back the goal?"
# A low spread alone is not good news: three capitals in lockstep at -2 are in
# full agreement, but agreement *against* the goal, not for it. Only a low
# spread AND a goal-backing overall earns the green "Aligned" label.
STANCE_ALIGNED_SPREAD = 0.5
STANCE_MIXED_SPREAD = 1.5
GOAL_BACKING_OVERALL = 0.5
GOAL_AGAINST_OVERALL = -0.5

# A weekly point resting on fewer rated statements than this is low-confidence:
# it renders as a hollow ring so a spike on 2–3 statements can't masquerade as a
# strong signal. Kept in sync with the JS constant in templates/index.html.
LOW_CONFIDENCE_N = 4

# The timeline shows a fixed trailing window rather than the full history, so
# a source that starts coverage partway through (e.g. a newly added country
# feed) doesn't read as a conspicuous gap at the left edge of the chart.
TIMELINE_WEEKS = 12

COLOR_GREEN = "#4d6b38"
COLOR_GREEN_LIGHT = "#6d8a4c"  # +1 row on the score-density heatmap — a lighter
# shade than +2's COLOR_GREEN, so "supports" reads as related to but weaker
# than "advances" without relying on opacity alone to carry that distinction.
COLOR_AMBER = "#8a6320"
COLOR_RED = "#a14132"

# Row order (top to bottom) for the score-density heatmap: +2 backs the goal
# most strongly, -2 opposes it most strongly. Colour and short description
# per row — both +1/+2 are shades of green and both -1/-2 share COLOR_RED
# (asymmetric on purpose: two granular "how strongly positive" shades, but
# opposition is opposition regardless of degree once it's opposition at all).
# Wording matches the per-event stance tag on the convergence cards below
# (index.html: "+2 actively advances · +1 supports · 0 neutral · −1 hedges ·
# −2 opposes") so a reader doesn't learn two vocabularies for the same scale.
SCORES = [2, 1, 0, -1, -2]
SCORE_COLOR = {2: COLOR_GREEN, 1: COLOR_GREEN_LIGHT, 0: COLOR_AMBER, -1: COLOR_RED, -2: COLOR_RED}
SCORE_DESC = {2: "advances", 1: "supports", 0: "neutral", -1: "hedges", -2: "opposes"}


def _stance_agreement(spread: float, overall: float) -> tuple[str, str]:
    """Map spread + goal-alignment between per-country mean stances to (label, color)."""
    if spread <= STANCE_ALIGNED_SPREAD:
        if overall <= GOAL_AGAINST_OVERALL:
            return "Aligned against goal", COLOR_RED
        if overall < GOAL_BACKING_OVERALL:
            return "Noncommittal", COLOR_AMBER
        return "Aligned", COLOR_GREEN
    if spread <= STANCE_MIXED_SPREAD:
        return "Mixed", COLOR_AMBER
    return "Divergent", COLOR_RED


def _stance_norm(s: float) -> float:
    """Map a stance in -2..+2 to 0..1 for plotting."""
    return (s + 2.0) / 4.0


def _fmt_stance(s: float) -> str:
    return f"{s:+.1f}"


def _load_yaml(path: Path) -> list | dict | None:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_events(weimar_only: bool = True) -> list[dict]:
    files = sorted(glob.glob(str(EVENTS_DIR / "**" / "*.yaml"), recursive=True))
    events = []
    for f in files:
        try:
            d = yaml.safe_load(Path(f).read_text(encoding="utf-8"))
            if not d:
                continue
            # Merge computed fields from the enriched sidecar
            rel = Path(f).relative_to(EVENTS_DIR)
            enriched_path = ENRICHED_DIR / rel
            if enriched_path.exists():
                enriched = yaml.safe_load(enriched_path.read_text(encoding="utf-8"))
                if enriched:
                    d.update(enriched)
            if weimar_only and not d.get("weimar_relevant"):
                continue
            d["_file_path"] = str(Path(f).relative_to(ROOT))  # always data/events/... path
            events.append(d)
        except Exception:
            pass
    return sorted(events, key=lambda e: e.get("date", ""), reverse=True)


def load_commentary() -> dict[str, str]:
    if COMMENTARY_FILE.exists():
        return json.loads(COMMENTARY_FILE.read_text(encoding="utf-8"))
    return {}


def cluster_key(cluster: dict) -> str:
    """Stable cache key: SHA256 of sorted event file paths in the cluster."""
    paths = sorted(
        item["event"]["_file_path"]
        for items in cluster["by_actor"].values()
        for item in items
        if item["event"].get("_file_path")
    )
    return hashlib.sha256(json.dumps(paths).encode()).hexdigest()[:12]


def score_cluster_stances(cluster: dict) -> dict | None:
    """
    Score a cluster from LLM-judged stance ratings (-2..+2 vs. the Weimar goal).

    Per actor: mean of that actor's event stances for the cluster topic.
    `overall` is the mean stance across actors — how strongly the capitals
    collectively back the goal. Agreement label comes from both the spread
    between actor means and `overall` (see _stance_agreement) — low spread
    alone isn't "Aligned" if the capitals agree while opposing the goal.
    Fully auditable via the evidence quotes stored on each event.
    """
    area = cluster["area"]
    per_actor_scores: dict[str, list[int]] = defaultdict(list)
    for actor, items in cluster["by_actor"].items():
        for item in items:
            stances = (item["event"].get("extracted") or {}).get("stances") or {}
            entry = stances.get(area)
            if entry and isinstance(entry.get("score"), int):
                per_actor_scores[actor].append(entry["score"])

    actors_scored = [a for a in cluster["actors"] if per_actor_scores.get(a)]
    if len(actors_scored) < 2:
        return None

    actor_means = {a: sum(per_actor_scores[a]) / len(per_actor_scores[a]) for a in actors_scored}
    spread = max(actor_means.values()) - min(actor_means.values())
    overall = sum(actor_means.values()) / len(actor_means)
    label, color = _stance_agreement(spread, overall)

    return {
        "per_actor": {a: {"stance": round(actor_means[a], 1), "n": len(per_actor_scores[a])} for a in actors_scored},
        "spread": round(spread, 1),
        "overall": round(overall, 2),
        "display": _fmt_stance(overall),
        "label": label,
        "color": color,
        "actors_scored": actors_scored,
        "scoring_mode": "stance",
    }


def compute_latest_topic_pills(
    clusters: list[dict], days: int = 7, today: datetime | None = None
) -> dict[str, dict | None]:
    """Per-topic convergence for the most recent scored cluster within the last `days` days."""
    cutoff = ((today or datetime.now(UTC)) - timedelta(days=days)).strftime("%Y-%m-%d")
    result: dict[str, dict | None] = {area: None for area in ISSUE_ORDER}
    for cluster in clusters:
        area = cluster["area"]
        if result[area] is None and cluster.get("convergence") and cluster["date_to"] >= cutoff:
            conv = cluster["convergence"]
            result[area] = {
                "label": conv["label"],
                "color": conv["color"],
                "overall": conv["overall"],
                "display": conv.get("display") or f"{int(conv['overall'] * 100)}%",
            }
    return result


def _stance_rows(
    events: list[dict], topics: frozenset[str] = frozenset(ISSUE_ORDER)
) -> list[tuple[str, str, str, int]]:
    """
    (date, actor, topic, score) rows for every stance-rated statement from a
    Weimar source — all six: each country's foreign ministry *and* its
    executive office (`SOURCE_ACTOR` covers both; see design principle #3).

    This is the single place that decides which sources feed stance
    aggregation. Any stance-aggregating function should call this rather than
    re-deriving its own actor map: a second, narrower mapping wouldn't error,
    it would just silently drop executive-office statements from that one
    function's output — which is what happened when the per-country timeline
    chart was first built with its own {"german_mfa": "DE", ...}-only map,
    invisibly excluding chancellery/Élysée/KPRM statements from the chart.
    """
    rows: list[tuple[str, str, str, int]] = []
    for e in events:
        src = e.get("source_name", "")
        if src not in SOURCE_ACTOR:
            continue
        stances = (e.get("extracted") or {}).get("stances") or {}
        for topic, entry in stances.items():
            if topic in topics and entry and isinstance(entry.get("score"), int):
                rows.append((e.get("date", ""), SOURCE_ACTOR[src], topic, entry["score"]))
    return rows


def compute_topic_weekly_stances(
    events: list[dict], window_days: int = 14, today: datetime | None = None
) -> dict[str, list[dict | None]]:
    """
    Stance-based weekly series per topic, plus an 'overall' series.

    For each week (rolling window_days window): per-country mean stance for the
    topic, agreement label from the spread between country means, and the
    cross-country mean stance as the plotted value. Weeks where <2 countries
    published rated statements are None.
    Returns: {'overall': [...], 'ukraine': [...], ...}
    """
    rows = _stance_rows(events)
    if not rows:
        return {}

    earliest = min(d for d, _, _, _ in rows)
    today = (today or datetime.now(UTC)).date()
    start_dt = datetime.strptime(earliest, "%Y-%m-%d").date()
    anchor = start_dt - timedelta(days=start_dt.weekday())  # snap to Monday
    all_weeks = []
    while anchor <= today:
        all_weeks.append(anchor.strftime("%Y-%m-%d"))
        anchor += timedelta(days=7)

    per_topic: dict[str, list[dict | None]] = {}
    for area in ISSUE_ORDER:
        area_rows = [(d, a, s) for d, a, t, s in rows if t == area]
        if not area_rows:
            continue

        series: list[dict | None] = []
        for week_str in all_weeks:
            week_dt = datetime.strptime(week_str, "%Y-%m-%d").date()
            window_start = (week_dt - timedelta(days=window_days)).strftime("%Y-%m-%d")

            actor_scores: dict[str, list[int]] = defaultdict(list)
            for date_str, actor, score in area_rows:
                if window_start <= date_str <= week_str:
                    actor_scores[actor].append(score)

            actors_with_data = [a for a in WEIMAR_ACTORS if actor_scores.get(a)]
            if len(actors_with_data) < 2:
                series.append(None)
                continue

            actor_means = {a: sum(actor_scores[a]) / len(actor_scores[a]) for a in actors_with_data}
            spread = max(actor_means.values()) - min(actor_means.values())
            stance_avg = sum(actor_means.values()) / len(actor_means)
            label, color = _stance_agreement(spread, stance_avg)

            series.append(
                {
                    "week": week_str,
                    "overall": round(_stance_norm(stance_avg), 3),  # 0..1 for plotting
                    "stance_avg": round(stance_avg, 2),
                    "display": _fmt_stance(stance_avg),
                    "band_lo": round(_stance_norm(min(actor_means.values())), 3),
                    "band_hi": round(_stance_norm(max(actor_means.values())), 3),
                    "label": label,
                    "color": color,
                    "per_actor": {a: round(m, 1) for a, m in actor_means.items()},
                    "actors_scored": actors_with_data,
                    "n_events": sum(len(v) for v in actor_scores.values()),
                }
            )

        per_topic[area] = series

    if not per_topic:
        return {}

    # Overall = per-week mean of topic stance averages; agreement from mean spread
    overall_series: list[dict | None] = []
    for i, week_str in enumerate(all_weeks):
        entries = [per_topic[a][i] for a in per_topic if per_topic[a][i] is not None]
        if not entries:
            overall_series.append(None)
            continue
        stance_avg = sum(e["stance_avg"] for e in entries) / len(entries)
        mean_spread = sum((e["band_hi"] - e["band_lo"]) * 4.0 for e in entries) / len(entries)
        label, color = _stance_agreement(mean_spread, stance_avg)
        overall_series.append(
            {
                "week": week_str,
                "overall": round(_stance_norm(stance_avg), 3),
                "stance_avg": round(stance_avg, 2),
                "display": _fmt_stance(stance_avg),
                "band_lo": round(sum(e["band_lo"] for e in entries) / len(entries), 3),
                "band_hi": round(sum(e["band_hi"] for e in entries) / len(entries), 3),
                "label": label,
                "color": color,
                "n_events": sum(e.get("n_events", 0) for e in entries),
            }
        )

    return {"overall": overall_series, **per_topic}


def compute_score_density(
    events: list[dict], window_days: int = 7, today: datetime | None = None, weeks: int | None = None
) -> dict[str, dict[str, dict]]:
    """
    Per (capital, topic) slice, a score x week grid of how many rated
    statements landed at each stance level (2, 1, 0, -1, -2) in that window.
    Unlike `compute_topic_weekly_stances`' rolling mean, a window here is a
    fixed, non-overlapping bin: this chart shows the full distribution of
    individual ratings rather than their average, so one sharp statement
    (e.g. a -1 amid a run of +1s) is visible as an outlier cell instead of
    being smoothed away.

    Windows are anchored to `today` (the edition cutoff) rather than to the
    calendar: the rightmost window always ends exactly on `today` and covers
    the `window_days` immediately before it, and each window to its left is a
    further `window_days` back. A calendar-week (Monday-anchored) bucketing
    would let the rightmost column land mid-week and only show a partial
    window's worth of statements — since editions are cut on a fixed weekly
    schedule (`data/edition.yaml`), anchoring to `today` instead keeps every
    column, including the most recent one, a complete `window_days`-day slice
    and makes each column correspond 1:1 to the edition that reported it.

    Returns `{"ALL": {"overall": {...}, "ukraine": {...}, ...}, "FR": {...}, ...}`
    — 4 capitals ("ALL" + FR/DE/PL) x 7 topics ("overall" + ISSUE_ORDER) = 28
    slices, all sharing one window axis so switching slices never shifts the
    x-axis. Each slice is
    `{"weeks": [...], "grid": [[n, ...] x len(weeks)] (SCORES order),
      "row_totals": [n, ...] (SCORES order), "grand_total": n}`.
    Each entry in `weeks` is a window's *end* date (the edition date), not
    its start.

    `weeks` param caps to the most recent N windows (see TIMELINE_WEEKS), to
    match the trailing window the rest of the page shows.
    """
    rows = _stance_rows(events)
    if not rows:
        return {}

    earliest = min(d for d, _, _, _ in rows)
    today_d = (today or datetime.now(UTC)).date()
    start_dt = datetime.strptime(earliest, "%Y-%m-%d").date()

    n_buckets = (today_d - start_dt).days // window_days + 1
    if weeks is not None:
        n_buckets = min(n_buckets, weeks)
    week_labels = [
        (today_d - timedelta(days=window_days * (n_buckets - 1 - i))).strftime("%Y-%m-%d") for i in range(n_buckets)
    ]

    def bucket_of(date_str: str) -> int:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (n_buckets - 1) - (today_d - d).days // window_days

    def grid_for(actor: str | None, topic: str | None) -> dict:
        grid = [[0] * len(week_labels) for _ in SCORES]
        for date_str, a, t, score in rows:
            if actor is not None and a != actor:
                continue
            if topic is not None and t != topic:
                continue
            if score not in SCORES:
                continue
            wi = bucket_of(date_str)
            if 0 <= wi < len(week_labels):
                grid[SCORES.index(score)][wi] += 1
        row_totals = [sum(r) for r in grid]
        return {"weeks": week_labels, "grid": grid, "row_totals": row_totals, "grand_total": sum(row_totals)}

    density: dict[str, dict[str, dict]] = {}
    for actor in ["ALL", *WEIMAR_ACTORS]:
        a_filter = None if actor == "ALL" else actor
        density[actor] = {"overall": grid_for(a_filter, None)}
        for topic in ISSUE_ORDER:
            density[actor][topic] = grid_for(a_filter, topic)
    return density


def _score_label(score: int) -> str:
    return f"{score:+d}" if score else "0"


def build_score_density_cells(grid: list[list[int]], row_totals: list[int], weeks: list[str]) -> dict:
    """
    CSS-grid-ready row/cell data for one score-density slice (see
    `compute_score_density`) — a table of divs, not an SVG: each row is a grid
    of `len(weeks)` cells plus a label column and a margin column, so the
    template lays it out with `grid-template-columns` instead of pixel math.

    Colour is diverging by row — green shades for +2/+1 ("advances"/
    "supports"), amber for neutral, red for -1/-2 ("hedges"/"opposes") — using
    the site's existing Aligned/Mixed/Divergent palette (`SCORE_COLOR`), not a
    one-off hue. A filled cell's opacity ~ sqrt(count) so a single statement
    still reads clearly instead of washing out next to busier weeks; a cell
    with zero statements gets a dashed border instead of vanishing, so the
    grid's shape stays legible even where nothing happened that week.
    """
    grand_total = sum(row_totals)
    max_n = max((max(row) for row in grid), default=0) or 1
    # Each entry in `weeks` is already the window's end date, i.e. the
    # edition date that window's statements were reported under.
    edition_dates = [datetime.strptime(w, "%Y-%m-%d").date() for w in weeks]
    edition_full_labels = [d.strftime("%A %-d %b") for d in edition_dates]
    edition_short_labels = [d.strftime("%-d %b") for d in edition_dates]

    rows = []
    for ri, score in enumerate(SCORES):
        color = SCORE_COLOR[score]
        row_counts = grid[ri] if ri < len(grid) else [0] * len(weeks)
        total = row_totals[ri] if ri < len(row_totals) else 0
        share = round(100 * total / grand_total) if grand_total else 0

        cells = []
        for wi, edition_full_label in enumerate(edition_full_labels):
            n = row_counts[wi] if wi < len(row_counts) else 0
            filled = n > 0
            opacity = round(0.16 + 0.84 * math.sqrt(n / max_n), 3) if filled else 0.0
            cells.append(
                {
                    "filled": filled,
                    "color": color,
                    "opacity": opacity,
                    "tooltip": f"Edition of {edition_full_label}  ·  stance {_score_label(score)}  ·  {n} statement{'s' if n != 1 else ''}",
                }
            )

        rows.append(
            {
                "label": _score_label(score),
                "desc": SCORE_DESC[score],
                "color": color,
                "total": total,
                "share": share,
                "cells": cells,
            }
        )

    return {
        "weeks": weeks,
        "edition_labels": edition_short_labels,
        "edition_full_labels": edition_full_labels,
        "rows": rows,
        "grand_total": grand_total,
    }


def build_divergence_leaderboard(topic_weekly: dict[str, list[dict | None]]) -> list[dict]:
    """
    Rank topics by how far apart the three capitals were in the current week.

    The current week is the latest week any topic has scored. Topics scored that
    week are ranked by spread (most contested first); topics that were quiet that
    week fall to the bottom flagged `quiet`. This is what makes the "most
    relevant story" re-order on its own each week rather than sit in a fixed list.
    """
    topic_series = {a: s for a, s in topic_weekly.items() if a != "overall"}
    if not topic_series:
        return []

    # Current week = the latest week label present anywhere in the topic series.
    weeks = {w["week"] for s in topic_series.values() for w in (s or []) if w}
    if not weeks:
        return []
    current = max(weeks)

    rows = []
    for area in ISSUE_ORDER:
        series = topic_series.get(area) or []
        entry = next((w for w in series if w and w["week"] == current), None)
        latest = next((w for w in reversed(series) if w), None)
        if entry:
            n = entry.get("n_events", 0)
            rows.append(
                {
                    "area": area,
                    "quiet": False,
                    "spread": round((entry["band_hi"] - entry["band_lo"]) * 4.0, 2),
                    "label": entry["label"],
                    "color": entry["color"],
                    "stance_avg": entry.get("stance_avg"),
                    "display": entry.get("display"),
                    "n": n,
                    "low": isinstance(n, int) and n < LOW_CONFIDENCE_N,
                }
            )
        elif latest:
            # Topic exists but published nothing rateable in the current window.
            rows.append({"area": area, "quiet": True, "spread": -1.0, "label": None, "color": None})

    rows.sort(key=lambda r: (r["quiet"], -r["spread"]))
    return rows


def load_latest_run() -> dict | None:
    runs = sorted(glob.glob(str(ROOT / "data" / "runs" / "*.yaml")))
    if not runs:
        return None
    return _load_yaml(Path(runs[-1]))


def compute_source_health() -> dict[str, dict]:
    """
    Scan all run files for per-source health: date of last error-free fetch,
    and the most recent error if the source is currently failing.
    """
    health: dict[str, dict] = {}
    for run_file in sorted(glob.glob(str(ROOT / "data" / "runs" / "*.yaml"))):
        run = _load_yaml(Path(run_file))
        if not run:
            continue
        date = run.get("date", "")
        for s in run.get("sources", []):
            src = s.get("source", "")
            if not src:
                continue
            entry = health.setdefault(src, {"last_ok": None, "last_error": None, "last_error_date": None})
            if s.get("error"):
                entry["last_error"] = str(s["error"])[:200]
                entry["last_error_date"] = date
            else:
                entry["last_ok"] = date
                # A clean run supersedes older errors
                if entry["last_error_date"] and entry["last_error_date"] <= date:
                    entry["last_error"] = None
                    entry["last_error_date"] = None
    return health


# ---------------------------------------------------------------------------
# Convergence clustering
# ---------------------------------------------------------------------------


def build_convergence_clusters(events: list[dict], window_days: int = 7) -> list[dict]:
    """
    Group weimar_relevant events by topic into clusters where 2+ MFA actors
    published within window_days of each other.
    """
    # Expand each event into (area, actor, date, event) rows
    rows = []
    for e in events:
        actor = SOURCE_ACTOR.get(e.get("source_name", ""))
        if actor not in ("DE", "FR", "PL"):
            continue
        for area in e.get("issue_areas") or []:
            if area == "other":
                continue
            rows.append(
                {
                    "date": e.get("date", ""),
                    "actor": actor,
                    "area": area,
                    "event": e,
                }
            )

    # Group by area
    by_area: dict[str, list] = defaultdict(list)
    for row in rows:
        by_area[row["area"]].append(row)

    clusters = []
    for area in ISSUE_ORDER:
        items = sorted(by_area.get(area, []), key=lambda x: x["date"], reverse=True)
        if not items:
            continue

        used = set()
        for i, anchor in enumerate(items):
            if i in used:
                continue
            anchor_date = datetime.strptime(anchor["date"], "%Y-%m-%d")
            cluster_items = [anchor]
            for j, other in enumerate(items):
                if j == i or j in used:
                    continue
                other_date = datetime.strptime(other["date"], "%Y-%m-%d")
                if abs((anchor_date - other_date).days) <= window_days:
                    cluster_items.append(other)
                    used.add(j)
            used.add(i)

            actors_in_cluster = {x["actor"] for x in cluster_items}
            if len(actors_in_cluster) < 2:
                continue

            # Deduplicate by (actor, source_url)
            seen: dict[tuple, dict] = {}
            for x in cluster_items:
                key = (x["actor"], x["event"].get("source_url", ""))
                if key not in seen:
                    seen[key] = x
            cluster_items = list(seen.values())
            actors_in_cluster = {x["actor"] for x in cluster_items}

            dates = [x["date"] for x in cluster_items]
            # Group items by actor for template rendering
            by_actor: dict[str, list] = defaultdict(list)
            for x in cluster_items:
                by_actor[x["actor"]].append(x)

            clusters.append(
                {
                    "area": area,
                    "area_label": ISSUE_LABELS.get(area, area.title()),
                    "actors": [a for a in WEIMAR_ACTORS if a in actors_in_cluster],
                    "date_from": min(dates),
                    "date_to": max(dates),
                    "by_actor": dict(by_actor),
                }
            )

    # Sort by most recent activity, then keep only the most recent cluster per area
    sorted_clusters = sorted(clusters, key=lambda c: c["date_to"], reverse=True)
    seen_areas: set[str] = set()
    deduped: list[dict] = []
    for c in sorted_clusters:
        if c["area"] not in seen_areas:
            deduped.append(c)
            seen_areas.add(c["area"])
    return deduped


# ---------------------------------------------------------------------------
# Static shareability assets
# ---------------------------------------------------------------------------

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="12" fill="#1c1812"/>
<circle cx="32" cy="32" r="3.5" fill="#f4ecdb"/>
<path d="M32 12 L32 21" stroke="#cf9530" stroke-width="5" stroke-linecap="round"/>
<path d="M13 45 L21 40" stroke="#3a6bb0" stroke-width="5" stroke-linecap="round"/>
<path d="M51 45 L43 40" stroke="#c23a30" stroke-width="5" stroke-linecap="round"/>
</svg>
"""

OG_IMAGE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630">
<rect width="1200" height="630" fill="#f4ecdb"/>
<rect x="0" y="0" width="1200" height="8" fill="#1f4279"/>
<rect x="400" y="0" width="400" height="8" fill="#c8a648"/>
<rect x="800" y="0" width="400" height="8" fill="#b22823"/>
<text x="80" y="220" font-family="Georgia, serif" font-size="72" fill="#1c1812">The Weimar</text>
<text x="80" y="310" font-family="Georgia, serif" font-size="72" font-style="italic" fill="#8a3a23">Triangle</text>
<text x="80" y="380" font-family="Georgia, serif" font-size="30" fill="#3f372b">
  Are France, Germany and Poland pulling
</text>
<text x="80" y="420" font-family="Georgia, serif" font-size="30" fill="#3f372b">
  in the same direction?
</text>
<text x="80" y="560" font-family="monospace" font-size="20" fill="#7a7060">weimar-triangle · a coordination tracker</text>
</svg>
"""

HUB_OG_IMAGE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630">
<rect width="1200" height="630" fill="#f4ecdb"/>
<rect x="0" y="0" width="1200" height="8" fill="#1f4279"/>
<rect x="400" y="0" width="400" height="8" fill="#c8a648"/>
<rect x="800" y="0" width="400" height="8" fill="#b22823"/>
<text x="80" y="260" font-family="Georgia, serif" font-size="72" fill="#1c1812">The</text>
<text x="80" y="350" font-family="Georgia, serif" font-size="72" font-style="italic" fill="#8a3a23">minilaterals</text>
<text x="80" y="420" font-family="Georgia, serif" font-size="30" fill="#3f372b">monitor</text>
<text x="80" y="480" font-family="Georgia, serif" font-size="30" fill="#3f372b">
  Small groups of capitals, tracked week by week.
</text>
<text x="80" y="560" font-family="monospace" font-size="20" fill="#7a7060">minilaterals &middot; a coordination tracker</text>
</svg>
"""

ROBOTS_TXT = """User-agent: *
Allow: /
"""

# Umbrella hub page (minilaterals.com root): one card per minilateral grouping.
# Only the Weimar Triangle is live; the rest are placeholders until their own
# ingesters/render targets exist. Flag codes are ISO 3166-1 alpha-2, lowercase,
# for flagcdn.com.
HUB_GROUPINGS = [
    {
        "name": "The E3",
        "accent": "linear-gradient(90deg,#1f4279 0 33.33%,#c8a648 33.33% 66.66%,#c8102e 66.66%)",
        "members": ["de", "fr", "gb"],
        "member_names": "Germany · France · United Kingdom",
        "topics": ["Iran", "Defence", "Nuclear file"],
        "blurb": "Europe's lead trio on Iran and hard security — Berlin, Paris and London coordinating outside the EU frame.",
    },
    {
        "name": "The Visegrád Group",
        "accent": "linear-gradient(90deg,#b22823 0 25%,#11457e 25% 50%,#ee1c25 50% 75%,#2f7a46 75%)",
        "members": ["pl", "cz", "sk", "hu"],
        "member_names": "Poland · Czechia · Slovakia · Hungary",
        "topics": ["Migration", "EU funds", "Energy"],
        "blurb": "Central Europe's caucus inside the EU — four capitals that vote together more often than not.",
    },
    {
        "name": "The Baltic Three",
        "accent": "linear-gradient(90deg,#0072ce 0 33.33%,#9e3039 33.33% 66.66%,#fdb913 66.66%)",
        "members": ["ee", "lv", "lt"],
        "member_names": "Estonia · Latvia · Lithuania",
        "topics": ["Deterrence", "Russia", "Energy security"],
        "blurb": "NATO's north-eastern frontier — Tallinn, Riga and Vilnius rarely more than a sentence apart.",
    },
    {
        "name": "AUKUS",
        "accent": "linear-gradient(90deg,#00247d 0 33.33%,#c8102e 33.33% 66.66%,#3c3b6e 66.66%)",
        "members": ["au", "gb", "us"],
        "member_names": "Australia · United Kingdom · United States",
        "topics": ["Submarines", "Indo-Pacific", "Defence tech"],
        "blurb": "A Pacific security pact built around nuclear-powered submarines and shared defence technology.",
    },
]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(output_dir: str = "docs", as_of: str | None = None) -> None:
    # Set when this site is deployed under a path prefix on the minilaterals.com
    # umbrella (e.g. "/weimar-triangle") rather than at the domain root.
    base_path = os.environ.get("SITE_BASE_PATH", "").rstrip("/")

    # render.py owns the whole deployable tree. `root` is the directory Cloudflare
    # serves (docs/); the site itself lives in the base-path subdir beside the
    # root-level deploy files (_redirects, 404.html) written near the end.
    root = Path(output_dir)
    out = root / base_path.lstrip("/") if base_path else root
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").touch()
    (out / "robots.txt").write_text(ROBOTS_TXT, encoding="utf-8")
    (out / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")
    (out / "og-image.svg").write_text(OG_IMAGE_SVG, encoding="utf-8")
    if base_path:
        # The hub page (rendered further below) occupies the true domain root,
        # so it needs its own copies of these root-relative assets alongside
        # the ones the subsite already wrote into `out`.
        root.mkdir(parents=True, exist_ok=True)
        (root / ".nojekyll").touch()
        (root / "robots.txt").write_text(ROBOTS_TXT, encoding="utf-8")
        (root / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")
        (root / "og-image.svg").write_text(HUB_OG_IMAGE_SVG, encoding="utf-8")

    edition_dt = resolve_edition_date(as_of)
    edition_cutoff = edition_dt.strftime("%Y-%m-%d")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    env.globals.update(
        {
            "actor_labels": ACTOR_LABELS,
            "actor_colors": ACTOR_COLORS,
            "issue_labels": ISSUE_LABELS,
            "era_colors": ERA_COLORS,
            "era_labels": ERA_LABELS,
            "type_colors": TYPE_COLORS,
            "now": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "edition_date_str": edition_dt.strftime("%A %-d %b"),
            "base_path": base_path,
            "weimar_actors": WEIMAR_ACTORS,
            "country_paths": {a: COUNTRY_PROFILE[a]["path"] for a in WEIMAR_ACTORS},
        }
    )

    # Events after the edition cutoff exist in data/ but are not published yet —
    # they belong to the next edition.
    events = [e for e in load_events(weimar_only=True) if (e.get("date") or "") <= edition_cutoff]
    all_events = [e for e in load_events(weimar_only=False) if (e.get("date") or "") <= edition_cutoff]
    # milestones.yaml/annual.yaml feed meetings.html only, which isn't rendered
    # right now (see below) — meetings.yaml itself stays loaded for meetings_count.
    meetings = _load_yaml(ROOT / "data" / "meetings.yaml") or []
    run = load_latest_run()
    clusters = build_convergence_clusters(events)
    commentary = load_commentary()
    for cluster in clusters:
        # Convergence is scored purely from LLM-judged stance ratings. A cluster
        # whose events lack stances scores None and renders without a badge.
        cluster["convergence"] = score_cluster_stances(cluster)
        cluster["commentary"] = commentary.get(cluster_key(cluster))

    # Score-density heatmap: one column per calendar week, one row per stance
    # level (+2..-2), coloured by backing/neutral/opposing and sized by count.
    # Replaces the old averaged-line chart — this shows the full distribution
    # of individual ratings, so a single sharp statement (e.g. Poland's 11
    # July enlargement statement) is a visible outlier cell rather than
    # invisible inside a rolling mean. Capped to TIMELINE_WEEKS so a capital
    # whose coverage starts later doesn't read as empty columns at the left.
    density = compute_score_density(events, today=edition_dt, weeks=TIMELINE_WEEKS)
    capital_order = ["ALL", *WEIMAR_ACTORS]
    density_cells_json = json.dumps(
        {
            actor: {
                topic: build_score_density_cells(slice_["grid"], slice_["row_totals"], slice_["weeks"])
                for topic, slice_ in topics.items()
            }
            for actor, topics in density.items()
        }
    )
    has_density = bool(density)

    # Divergence ranking (topics by this week's FR·DE·PL spread) orders both the
    # topic toggle row and the convergence clusters below, so the most contested
    # story leads — but the ranking itself is never shown as a list.
    topic_weekly = compute_topic_weekly_stances(events, today=edition_dt)
    leaderboard = build_divergence_leaderboard(topic_weekly)
    ranked_areas = [r["area"] for r in leaderboard]
    topic_order = ["overall"] + ranked_areas + [a for a in ISSUE_ORDER if a not in ranked_areas]
    # Reorder clusters to match the ranking (most divergent topic first).
    rank_index = {area: i for i, area in enumerate(ranked_areas)}
    clusters.sort(key=lambda c: rank_index.get(c.get("area"), len(rank_index)))

    # Recent events: last 90 days before the edition cutoff
    cutoff = (edition_dt - timedelta(days=90)).strftime("%Y-%m-%d")
    recent_events = [e for e in events if (e.get("date") or "") >= cutoff]

    # Per-country stats for country cards and country pages
    cutoff_7 = (edition_dt - timedelta(days=7)).strftime("%Y-%m-%d")
    weekly_counts: dict[str, int] = {a: 0 for a in WEIMAR_ACTORS}
    # Per-source weekly counts, so each institution's activity shows separately
    # on the country page's sources strip.
    source_weekly_counts: dict[str, int] = defaultdict(int)
    for e in events:
        if (e.get("date") or "") >= cutoff_7:
            src = e.get("source_name", "")
            source_weekly_counts[src] += 1
            actor = SOURCE_ACTOR.get(src)
            if actor in weekly_counts:
                weekly_counts[actor] += 1

    # Pairwise stance agreement over the last 14 days: for each topic both
    # countries rated, agreement = 1 - |meanA - meanB| / 4 (4 = full scale width);
    # pair score = mean across shared topics. Pairs with no shared rated topic
    # show "—".
    pair_scores: dict[str, int] = {}
    cutoff_14 = (edition_dt - timedelta(days=14)).strftime("%Y-%m-%d")
    actor_topic_scores: dict[str, dict[str, list[int]]] = {a: defaultdict(list) for a in WEIMAR_ACTORS}
    for e in events:
        actor = SOURCE_ACTOR.get(e.get("source_name", ""))
        if actor not in actor_topic_scores or (e.get("date") or "") < cutoff_14:
            continue
        for topic, entry in (((e.get("extracted") or {}).get("stances")) or {}).items():
            if topic in ISSUE_ORDER and entry and isinstance(entry.get("score"), int):
                actor_topic_scores[actor][topic].append(entry["score"])

    for i, a1 in enumerate(WEIMAR_ACTORS):
        for a2 in WEIMAR_ACTORS[i + 1 :]:
            shared = set(actor_topic_scores[a1]) & set(actor_topic_scores[a2])
            if not shared:
                continue
            agreements = []
            for t in shared:
                m1 = sum(actor_topic_scores[a1][t]) / len(actor_topic_scores[a1][t])
                m2 = sum(actor_topic_scores[a2][t]) / len(actor_topic_scores[a2][t])
                agreements.append(1.0 - abs(m1 - m2) / 4.0)
            pair_scores[f"{a1}_{a2}"] = int(round(sum(agreements) / len(agreements) * 100))

    def _pair_score(a1: str, a2: str) -> int | str:
        for k in (f"{a1}_{a2}", f"{a2}_{a1}"):
            if k in pair_scores:
                return pair_scores[k]
        return "—"

    def _country_sources(actor: str) -> list[dict]:
        return [
            {**s, "weekly_count": source_weekly_counts.get(s["source"], 0)} for s in COUNTRY_PROFILE[actor]["sources"]
        ]

    country_stats = {
        actor: {
            "swatch": COUNTRY_PROFILE[actor]["swatch"],
            "path": COUNTRY_PROFILE[actor]["path"],
            "sources": _country_sources(actor),
            "weekly_count": weekly_counts[actor],
            "align": {other: _pair_score(actor, other) for other in WEIMAR_ACTORS if other != actor},
        }
        for actor in WEIMAR_ACTORS
    }

    # Continue-card date range, anchored to the edition cutoff. Matches the
    # convergence clusters' window_days so the diary blurb and heatmap agree
    # on what counts as "this edition".
    today_utc = edition_dt
    coverage_from = (today_utc - timedelta(days=7)).strftime("%-d %b")
    coverage_to = today_utc.strftime("%-d %b")
    weekday = today_utc.weekday()
    days_to_tue = (1 - weekday) % 7 or 7
    next_tuesday_str = (today_utc + timedelta(days=days_to_tue)).strftime("%A %-d %b")

    source_stats: dict[str, dict] = {}
    if run:
        for s in run.get("sources", []):
            source_stats[s["source"]] = s
    source_health = compute_source_health()

    # Staleness banner: newest ingested event vs. today
    latest_event_date = max((e.get("date") or "" for e in all_events), default="")
    stale_days = None
    if latest_event_date:
        stale_days = (today_utc.date() - datetime.strptime(latest_event_date, "%Y-%m-%d").date()).days

    # docs/index.html
    tmpl = env.get_template("index.html")
    (out / "index.html").write_text(
        tmpl.render(
            recent_events=recent_events,
            clusters=clusters,
            source_stats=source_stats,
            run=run,
            total_events=len(events),
            total_all=len(all_events),
            meetings_count=len(meetings),
            has_density=has_density,
            density_cells_json=density_cells_json,
            capital_order=capital_order,
            topic_order=topic_order,
            issue_order=ISSUE_ORDER,
            country_stats=country_stats,
            weimar_actors=WEIMAR_ACTORS,
            source_count=len(SOURCE_ACTOR),
            coverage_from=coverage_from,
            coverage_to=coverage_to,
            next_tuesday_str=next_tuesday_str,
            latest_event_date=latest_event_date,
            stale_days=stale_days,
        ),
        encoding="utf-8",
    )

    # docs/meetings/index.html — not rendered for now (kept in git: data/meetings.yaml,
    # data/milestones.yaml, data/annual.yaml, pipeline/templates/meetings.html), pending
    # a decision on what to do with this page.

    # docs/sources/index.html
    # Rows grouped by country (foreign ministry then executive office), driven
    # from COUNTRY_PROFILE so the table and the country pages stay in sync.
    source_rows = [
        {
            "source": s["source"],
            "actor": actor,
            "type": s["type"],
            "institution": s["institution"],
            "method": SOURCE_INGEST.get(s["source"], ("HTML scraper", "EN"))[0],
            "lang": SOURCE_INGEST.get(s["source"], ("HTML scraper", "EN"))[1],
        }
        for actor in WEIMAR_ACTORS
        for s in COUNTRY_PROFILE[actor]["sources"]
    ]
    (out / "sources").mkdir(exist_ok=True)
    tmpl = env.get_template("sources.html")
    (out / "sources" / "index.html").write_text(
        tmpl.render(
            source_stats=source_stats,
            source_health=source_health,
            run=run,
            source_labels=SOURCE_LABELS,
            source_rows=source_rows,
            all_events_count=len(all_events),
            weimar_events_count=len(events),
        ),
        encoding="utf-8",
    )

    # docs/{country}/index.html — one page per Weimar country
    tmpl = env.get_template("country.html")
    for actor in WEIMAR_ACTORS:
        profile = country_stats[actor]
        country_events = [e for e in recent_events if SOURCE_ACTOR.get(e.get("source_name", "")) == actor]
        others = [cs for a, cs in country_stats.items() if a != actor]
        (out / profile["path"]).mkdir(exist_ok=True)
        (out / profile["path"] / "index.html").write_text(
            tmpl.render(
                actor=actor,
                profile=profile,
                country_events=country_events,
                stats=country_stats[actor],
                others=others,
                weimar_actors=WEIMAR_ACTORS,
                country_stats=country_stats,
                source_meta=SOURCE_META,
            ),
            encoding="utf-8",
        )

    # Root-level deploy files, beside (not inside) the base-path subdir.
    # 404.html is what Cloudflare serves for unknown paths (wrangler.jsonc
    # not_found_handling).
    (root / "404.html").write_text(env.get_template("404.html").render() + "\n", encoding="utf-8")

    # docs/index.html — the minilaterals.com hub page, one card per minilateral
    # grouping. Only rendered when the site sits under a path prefix (base_path
    # set): that's what makes room for a hub at the true domain root. In local
    # dev (no SITE_BASE_PATH) `out` already *is* `root`, so the Weimar Triangle
    # tracker's own index.html lives there instead.
    if base_path:
        overall_series = topic_weekly.get("overall") or []
        latest_overall = next((w for w in reversed(overall_series) if w), None)
        weimar_badge = {"label": latest_overall["label"], "color": latest_overall["color"]} if latest_overall else None
        tmpl = env.get_template("hub.html")
        (root / "index.html").write_text(
            tmpl.render(
                base_path=base_path,
                weimar_actors=WEIMAR_ACTORS,
                issue_order=ISSUE_ORDER,
                issue_labels=ISSUE_LABELS,
                edition_hub_date_str=edition_dt.strftime("%-d %b %Y"),
                weimar_weekly_statements=sum(weekly_counts.values()),
                weimar_badge=weimar_badge,
                hub_groupings=HUB_GROUPINGS,
            ),
            encoding="utf-8",
        )

    print(f"Rendered → {root.resolve()}")
    print(f"  recent events (90d): {len(recent_events)}, clusters: {len(clusters)}")
    print(f"  meetings: {len(meetings)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Weimar tracker renderer")
    parser.add_argument("--output", default="docs", help="Output directory (default: docs)")
    parser.add_argument(
        "--as-of",
        default=None,
        metavar="YYYY-MM-DD",
        help="Edition cutoff override (default: data/edition.yaml, else today)",
    )
    args = parser.parse_args()
    render(args.output, as_of=args.as_of)


if __name__ == "__main__":
    main()
