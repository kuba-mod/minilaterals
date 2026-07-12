#!/usr/bin/env python3
"""
Weimar Triangle tracker — static HTML renderer.

Reads data/events/**/*.yaml + data/meetings.yaml + data/milestones.yaml + data/annual.yaml
and renders HTML pages to docs/ (or --output DIR).

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
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

try:
    import numpy as _np

    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

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
}

SOURCE_LABELS = {
    "german_mfa": "German MFA",
    "france_diplomatie": "France Diplomatie",
    "polish_mfa": "Polish MFA",
}

ACTOR_LABELS = {
    "DE": "Germany",
    "FR": "France",
    "PL": "Poland",
}

WEIMAR_ACTORS = ["FR", "DE", "PL"]

COUNTRY_FM = {
    "FR": {
        "name": "Jean-Noël Barrot",
        "role": "Minister for Europe and Foreign Affairs",
        "ministry": "Quai d'Orsay",
        "swatch": "fr",
        "path": "france",
        "source": "france_diplomatie",
    },
    "DE": {
        "name": "Johann Wadephul",
        "role": "Federal Minister for Foreign Affairs",
        "ministry": "Auswärtiges Amt",
        "swatch": "de",
        "path": "germany",
        "source": "german_mfa",
    },
    "PL": {
        "name": "Radosław Sikorski",
        "role": "Deputy Prime Minister and Minister of Foreign Affairs",
        "ministry": "MSZ",
        "swatch": "pl",
        "path": "poland",
        "source": "polish_mfa",
    },
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
EMBEDDINGS_FILE = ROOT / "data" / "embeddings.json"
POSITION_EMBEDDINGS_FILE = ROOT / "data" / "position_embeddings.json"
GOAL_EMBEDDINGS_FILE = ROOT / "data" / "goal_embeddings.json"
COMMENTARY_FILE = ROOT / "data" / "commentary.json"

# Convergence score thresholds — legacy peer-to-peer mode (within-topic, so naturally elevated)
CONVERGENCE_CONVERGING = 0.72
CONVERGENCE_PARALLEL = 0.50

# Goal-anchored thresholds: cosine of full article text vs. short goal sentence is lower
# than peer-to-peer. Calibrated against observed score range (0.25–0.55).
CONVERGENCE_GOAL_CONVERGING = 0.42
CONVERGENCE_GOAL_PARALLEL = 0.28

# Stance-based alignment: each event×topic carries an LLM-judged stance in -2..+2
# vs. the Weimar goal. Agreement label comes from the spread between per-country
# mean stances (spread is in stance units, so the thresholds are self-explanatory:
# ≤0.5 = same bucket, ≤1.5 = adjacent buckets).
STANCE_ALIGNED_SPREAD = 0.5
STANCE_MIXED_SPREAD = 1.5

COLOR_GREEN = "#4d6b38"
COLOR_AMBER = "#8a6320"
COLOR_RED = "#a14132"


def _stance_agreement(spread: float) -> tuple[str, str]:
    """Map spread between per-country mean stances to (label, color)."""
    if spread <= STANCE_ALIGNED_SPREAD:
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


def load_embeddings() -> dict[str, list[float]]:
    if EMBEDDINGS_FILE.exists():
        return json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    return {}


def load_commentary() -> dict[str, str]:
    if COMMENTARY_FILE.exists():
        return json.loads(COMMENTARY_FILE.read_text(encoding="utf-8"))
    return {}


def load_goal_embeddings() -> dict[str, list[float]]:
    if GOAL_EMBEDDINGS_FILE.exists():
        return json.loads(GOAL_EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    return {}


def load_position_embeddings() -> dict[str, list[float]]:
    if POSITION_EMBEDDINGS_FILE.exists():
        return json.loads(POSITION_EMBEDDINGS_FILE.read_text(encoding="utf-8"))
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


def _dot(a: list[float], b: list[float]) -> float:
    """Cosine similarity for pre-normalised vectors = dot product."""
    return sum(x * y for x, y in zip(a, b, strict=True))


def _mean_vec(vecs: list[list[float]]) -> list[float] | None:
    if not vecs:
        return None
    n = len(vecs[0])
    mean = [sum(v[i] for v in vecs) / len(vecs) for i in range(n)]
    mag = math.sqrt(sum(x * x for x in mean))
    return [x / mag for x in mean] if mag > 0 else None


def score_cluster_stances(cluster: dict) -> dict | None:
    """
    Score a cluster from LLM-judged stance ratings (-2..+2 vs. the Weimar goal).

    Per actor: mean of that actor's event stances for the cluster topic.
    Agreement label from the spread between actor means (see _stance_agreement).
    `overall` is the mean stance across actors — how strongly the capitals
    collectively back the goal — and is fully auditable via the evidence quotes
    stored on each event.
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
    label, color = _stance_agreement(spread)

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


def score_cluster_convergence(
    cluster: dict,
    emb_store: dict,
    goal_emb_store: dict | None = None,
    pos_emb_store: dict | None = None,
) -> dict | None:
    """
    Score convergence for a cluster.

    Goal-anchored mode (for officially agreed Weimar topics): measures how well
    each actor's articles align with the shared Weimar goal for this topic.
    Uses topic-specific position embeddings (pos_emb_store) when available — these
    are focused one-sentence LLM summaries in English per topic — falling back to
    full-text embeddings for events that haven't been enriched yet.
    overall = mean(actor_goal_alignments).
    Thresholds: ≥0.42 Converging, ≥0.28 Parallel, <0.28 Diverging.

    Legacy peer-to-peer mode (for other topics): computes pairwise cosine
    similarity between actor mean embeddings (unchanged behaviour).
    Thresholds: ≥0.72 Converging, ≥0.50 Parallel.

    Return dict shape is identical in both modes so all callers work unchanged.
    """
    actor_vecs: dict[str, list[list[float]]] = defaultdict(list)
    area = cluster["area"]
    for actor, items in cluster["by_actor"].items():
        for item in items:
            fpath = item["event"].get("_file_path")
            if not fpath:
                continue
            # For goal-anchored scoring prefer the topic-specific position embedding
            topic_key = f"{fpath}#{area}"
            fallback_key = f"{fpath}#overall"
            if pos_emb_store and topic_key in pos_emb_store:
                actor_vecs[actor].append(pos_emb_store[topic_key])
            elif pos_emb_store and fallback_key in pos_emb_store:
                actor_vecs[actor].append(pos_emb_store[fallback_key])
            elif fpath in emb_store:
                actor_vecs[actor].append(emb_store[fpath])

    actors_with_emb = [a for a in cluster["actors"] if actor_vecs.get(a)]
    if len(actors_with_emb) < 2:
        return None

    means = {a: _mean_vec(actor_vecs[a]) for a in actors_with_emb}
    area = cluster["area"]

    # Goal-anchored scoring
    if goal_emb_store and area in goal_emb_store:
        goal_vec = goal_emb_store[area]
        actor_alignments = {a: _dot(means[a], goal_vec) for a in actors_with_emb}
        pairs = [{"actors": a, "score": round(actor_alignments[a], 3)} for a in actors_with_emb]
        overall = sum(actor_alignments.values()) / len(actor_alignments)
        if overall >= CONVERGENCE_GOAL_CONVERGING:
            label, color = "Converging", "#4d6b38"
        elif overall >= CONVERGENCE_GOAL_PARALLEL:
            label, color = "Parallel", "#8a6320"
        else:
            label, color = "Diverging", "#a14132"
        return {
            "pairs": pairs,
            "overall": round(overall, 3),
            "label": label,
            "color": color,
            "actors_scored": actors_with_emb,
            "scoring_mode": "goal_anchored",
        }

    # Legacy peer-to-peer scoring
    pairs = []
    for i, a1 in enumerate(actors_with_emb):
        for a2 in actors_with_emb[i + 1 :]:
            sim = _dot(means[a1], means[a2])
            pairs.append({"actors": f"{a1}_{a2}", "score": round(sim, 3)})

    overall = sum(p["score"] for p in pairs) / len(pairs)
    if overall >= CONVERGENCE_CONVERGING:
        label, color = "Converging", "#4d6b38"
    elif overall >= CONVERGENCE_PARALLEL:
        label, color = "Parallel", "#8a6320"
    else:
        label, color = "Diverging", "#a14132"

    return {
        "pairs": pairs,
        "overall": round(overall, 3),
        "label": label,
        "color": color,
        "actors_scored": actors_with_emb,
        "scoring_mode": "peer_to_peer",
    }


def _allpairs_median_score(vecs_a: list[list[float]], vecs_b: list[list[float]]) -> tuple[float, float, float]:
    """All-pairs cosine sim for pre-normalised vectors; returns (median, q25, q75)."""
    if _HAS_NUMPY:
        a = _np.array(vecs_a)
        b = _np.array(vecs_b)
        sims = (a @ b.T).flatten()
        return float(_np.median(sims)), float(_np.percentile(sims, 25)), float(_np.percentile(sims, 75))
    sims = sorted(_dot(va, vb) for va in vecs_a for vb in vecs_b)
    n = len(sims)
    med = sims[n // 2] if n % 2 else (sims[n // 2 - 1] + sims[n // 2]) / 2
    return med, sims[max(0, n // 4)], sims[min(n - 1, 3 * n // 4)]


def compute_weekly_alignment(
    events: list[dict],
    emb_store: dict,
    pos_emb_store: dict | None = None,
    window_days: int = 14,
    today: datetime | None = None,
) -> list[dict | None]:
    """
    Rolling window_days alignment score, one entry per calendar week (oldest first).
    Returns None for weeks where <2 actors have embedded events.

    Uses pos_emb_store (position embeddings, keyed filepath#topic) as primary source,
    mean-pooling across topics per event. Falls back to emb_store (full-text) when no
    position embedding exists for an event.
    """
    if not pos_emb_store and not emb_store:
        return []

    mfa_sources = {"german_mfa", "france_diplomatie", "polish_mfa"}
    actor_map = {"german_mfa": "DE", "france_diplomatie": "FR", "polish_mfa": "PL"}

    # Build per-event representative vector: mean of all topic position vectors,
    # falling back to full-text embedding if no position embeddings exist.
    event_vecs: dict[str, list[float]] = {}
    for e in events:
        src = e.get("source_name", "")
        if src not in mfa_sources:
            continue
        fpath = e.get("_file_path", "")
        if not fpath:
            continue
        if pos_emb_store:
            topic_vecs = [v for k, v in pos_emb_store.items() if k.startswith(fpath + "#")]
            if topic_vecs:
                event_vecs[fpath] = [sum(col) / len(col) for col in zip(*topic_vecs, strict=True)]
                continue
        if fpath in emb_store:
            event_vecs[fpath] = emb_store[fpath]

    embedded: list[tuple[str, str, str]] = []
    for e in events:
        src = e.get("source_name", "")
        if src not in mfa_sources:
            continue
        fpath = e.get("_file_path", "")
        if fpath in event_vecs:
            embedded.append((e.get("date", ""), actor_map[src], fpath))

    if not embedded:
        return []

    earliest = min(d for d, _, _ in embedded)
    today = (today or datetime.now(UTC)).date()
    start_dt = datetime.strptime(earliest, "%Y-%m-%d").date()
    anchor = start_dt - timedelta(days=start_dt.weekday())  # snap to Monday

    results: list[dict | None] = []
    while anchor <= today:
        window_start = (anchor - timedelta(days=window_days)).strftime("%Y-%m-%d")
        window_end = anchor.strftime("%Y-%m-%d")

        actor_vecs: dict[str, list[list[float]]] = defaultdict(list)
        n_events = 0
        for date_str, actor, fpath in embedded:
            if window_start <= date_str <= window_end:
                actor_vecs[actor].append(event_vecs[fpath])
                n_events += 1

        actors_with_data = [a for a in ("DE", "FR", "PL") if actor_vecs.get(a)]
        if len(actors_with_data) < 2:
            results.append(None)
            anchor += timedelta(days=7)
            continue

        pair_medians: list[float] = []
        by_pair: dict[str, dict] = {}
        for i, a1 in enumerate(actors_with_data):
            for a2 in actors_with_data[i + 1 :]:
                med, q25, q75 = _allpairs_median_score(actor_vecs[a1], actor_vecs[a2])
                by_pair[f"{a1}_{a2}"] = {
                    "median": round(med, 3),
                    "q25": round(q25, 3),
                    "q75": round(q75, 3),
                }
                pair_medians.append(med)

        overall = sum(pair_medians) / len(pair_medians)
        if overall >= CONVERGENCE_CONVERGING:
            label, color = "Converging", "#4d6b38"
        elif overall >= CONVERGENCE_PARALLEL:
            label, color = "Parallel", "#8a6320"
        else:
            label, color = "Diverging", "#a14132"

        results.append(
            {
                "week": anchor.strftime("%Y-%m-%d"),
                "overall": round(overall, 3),
                "label": label,
                "color": color,
                "by_pair": by_pair,
                "actors_scored": actors_with_data,
                "n_events": n_events,
            }
        )
        anchor += timedelta(days=7)

    return results


def compute_latest_heatmap(
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
    mfa_sources = {"german_mfa", "france_diplomatie", "polish_mfa"}
    actor_map = {"german_mfa": "DE", "france_diplomatie": "FR", "polish_mfa": "PL"}

    # (date, actor, topic, stance) rows from all stance-rated events
    rows: list[tuple[str, str, str, int]] = []
    for e in events:
        src = e.get("source_name", "")
        if src not in mfa_sources:
            continue
        stances = (e.get("extracted") or {}).get("stances") or {}
        for topic, entry in stances.items():
            if topic in ISSUE_ORDER and entry and isinstance(entry.get("score"), int):
                rows.append((e.get("date", ""), actor_map[src], topic, entry["score"]))

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

            actors_with_data = [a for a in ("DE", "FR", "PL") if actor_scores.get(a)]
            if len(actors_with_data) < 2:
                series.append(None)
                continue

            actor_means = {a: sum(actor_scores[a]) / len(actor_scores[a]) for a in actors_with_data}
            spread = max(actor_means.values()) - min(actor_means.values())
            stance_avg = sum(actor_means.values()) / len(actor_means)
            label, color = _stance_agreement(spread)

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
        label, color = _stance_agreement(mean_spread)
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


def build_timeline_svg_data(weekly: list[dict | None]) -> dict | None:
    """
    Convert a weekly series into SVG-ready coordinate data for the template.

    Entries carry `overall` as a 0..1 plot value. Stance entries additionally
    carry `display` ("+1.3"), `band_lo`/`band_hi` (min/max country mean) and
    `per_actor`; legacy cosine entries carry `by_pair` and display as percent.
    """
    scored = [w for w in weekly if w is not None]
    if len(scored) < 2:
        return None

    PAD_L, PAD_R, PAD_T, PAD_B = 44, 20, 15, 35
    W, H = 800, 220
    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B
    n = len(weekly)

    def y_for(score: float) -> float:
        return PAD_T + chart_h * (1.0 - score)

    def x_for(idx: int) -> float:
        return PAD_L + idx * chart_w / (n - 1) if n > 1 else PAD_L + chart_w / 2

    stance_mode = any("stance_avg" in w for w in scored)

    points = []
    line_xy: list[str] = []
    band_top: list[str] = []
    band_bot: list[str] = []

    for i, w in enumerate(weekly):
        x = round(x_for(i), 1)
        if w is None:
            points.append({"x": x, "y": round(y_for(0.5), 1), "has_data": False})
            continue

        y = round(y_for(w["overall"]), 1)
        display = w.get("display") or f"{int(w['overall'] * 100)}%"

        if "band_lo" in w and "band_hi" in w:
            band_top.append(f"{x},{round(y_for(w['band_hi']), 1)}")
            band_bot.append(f"{x},{round(y_for(w['band_lo']), 1)}")
            detail = "  ".join(f"{a}: {m:+.1f}" for a, m in (w.get("per_actor") or {}).items())
        elif w.get("by_pair"):
            by_pair = w["by_pair"]
            q25_avg = sum(v["q25"] for v in by_pair.values()) / len(by_pair)
            q75_avg = sum(v["q75"] for v in by_pair.values()) / len(by_pair)
            band_top.append(f"{x},{round(y_for(q75_avg), 1)}")
            band_bot.append(f"{x},{round(y_for(q25_avg), 1)}")
            detail = "  ".join(
                f"{k}: {int(v['median'] * 100)}% (IQR {int(v['q25'] * 100)}–{int(v['q75'] * 100)}%)"
                for k, v in by_pair.items()
            )
        else:
            detail = ""

        actors_str = ", ".join(w.get("actors_scored") or [])
        n_ev = w.get("n_events", "?")
        tooltip = (
            f"Week of {w['week']}  ·  {w['label']} · avg stance {display}"
            if stance_mode
            else f"Week of {w['week']}  ·  {w['label']} {int(w['overall'] * 100)}%"
        )
        tooltip += (
            (f"\n{detail}" if detail else "")
            + (f"\nActors: {actors_str}  ·  " if actors_str else "\n")
            + f"{n_ev} rated statements  ·  14-day rolling window"
        )
        points.append(
            {
                "x": x,
                "y": y,
                "has_data": True,
                "week": w["week"],
                "tooltip": tooltip,
                "color": w["color"],
                "label": w["label"],
                "display": display,
            }
        )
        line_xy.append(f"{x},{y}")

    band_points = " ".join(band_top) + " " + " ".join(reversed(band_bot)) if band_top else ""

    # Reference lines: stance mode gets the rubric levels; cosine mode the old thresholds
    if stance_mode:
        ref_lines = [
            {"y": round(y_for(_stance_norm(1.0)), 1), "color": COLOR_GREEN, "label": "+1"},
            {"y": round(y_for(_stance_norm(0.0)), 1), "color": COLOR_AMBER, "label": "0"},
        ]
    else:
        ref_lines = [
            {
                "y": round(y_for(CONVERGENCE_GOAL_CONVERGING), 1),
                "color": COLOR_GREEN,
                "label": f"{int(CONVERGENCE_GOAL_CONVERGING * 100)}%",
            },
            {
                "y": round(y_for(CONVERGENCE_GOAL_PARALLEL), 1),
                "color": COLOR_AMBER,
                "label": f"{int(CONVERGENCE_GOAL_PARALLEL * 100)}%",
            },
        ]

    return {
        "points": points,
        "line_points": " ".join(line_xy),
        "band_points": band_points,
        "ref_lines": ref_lines,
        "stance_mode": stance_mode,
        "viewbox": f"0 0 {W} {H}",
        "recent": scored[-1],
    }


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


def build_convergence_clusters(events: list[dict], window_days: int = 14) -> list[dict]:
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
                    "actors": sorted(actors_in_cluster),
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


CSV_HEADER = [
    "date", "actor", "source", "title", "source_url", "issue_areas",
    "trilateral_signal", "position", "stance_scores",
]


def _csv_escape(value: str) -> str:
    value = value.replace('"', '""')
    return f'"{value}"'


def write_current_month_export(out: Path, events: list[dict]) -> str:
    """
    Write a free, current-calendar-month CSV of tracked events into
    out/exports/weimar-YYYY-MM.csv. Full-history export is a premium teaser
    (see index.html) — this is the always-free sample.
    Returns the filename (not full path) for use in template links.
    """
    (out / "exports").mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc)
    month_prefix = today.strftime("%Y-%m")
    filename = f"weimar-{month_prefix}.csv"

    rows = [e for e in events if (e.get("date") or "").startswith(month_prefix)]
    rows.sort(key=lambda e: e.get("date", ""))

    lines = [",".join(CSV_HEADER)]
    for e in rows:
        actor = SOURCE_ACTOR.get(e.get("source_name", "")) or ""
        source = SOURCE_LABELS.get(e.get("source_name", ""), e.get("source_name", ""))
        issue_areas = "; ".join(e.get("issue_areas") or [])
        extracted = e.get("extracted") or {}
        position = extracted.get("position", "") or ""
        stances = extracted.get("stances") or {}
        stance_str = "; ".join(
            f"{topic}:{entry.get('score')}" for topic, entry in stances.items()
            if isinstance(entry, dict) and entry.get("score") is not None
        )
        fields = [
            e.get("date", ""),
            actor,
            source,
            e.get("title", "") or "",
            e.get("source_url", "") or "",
            issue_areas,
            "yes" if e.get("trilateral_signal") else "no",
            position,
            stance_str,
        ]
        lines.append(",".join(_csv_escape(str(f)) for f in fields))

    (out / "exports" / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return filename


# ---------------------------------------------------------------------------
# Static shareability assets
# ---------------------------------------------------------------------------

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="12" fill="#1c1812"/>
<circle cx="32" cy="32" r="3" fill="#f4ecdb"/>
<path d="M32 12 L32 21" stroke="#9a6a1f" stroke-width="3.5" stroke-linecap="round"/>
<path d="M13 45 L21 40" stroke="#1f4279" stroke-width="3.5" stroke-linecap="round"/>
<path d="M51 45 L43 40" stroke="#b22823" stroke-width="3.5" stroke-linecap="round"/>
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

ROBOTS_TXT = """User-agent: *
Allow: /
"""


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
        }
    )

    # Events after the edition cutoff exist in data/ but are not published yet —
    # they belong to the next edition.
    events = [e for e in load_events(weimar_only=True) if (e.get("date") or "") <= edition_cutoff]
    all_events = [e for e in load_events(weimar_only=False) if (e.get("date") or "") <= edition_cutoff]
    meetings = _load_yaml(ROOT / "data" / "meetings.yaml") or []
    milestones = _load_yaml(ROOT / "data" / "milestones.yaml") or []
    annual = _load_yaml(ROOT / "data" / "annual.yaml") or []
    run = load_latest_run()
    emb_store = load_embeddings()
    goal_emb_store = load_goal_embeddings()
    pos_emb_store = load_position_embeddings()
    clusters = build_convergence_clusters(events)
    commentary = load_commentary()
    for cluster in clusters:
        # Stance ratings are the primary scoring; embedding cosine is the fallback
        # for clusters whose events haven't been stance-rated yet.
        cluster["convergence"] = score_cluster_stances(cluster) or score_cluster_convergence(
            cluster, emb_store, goal_emb_store, pos_emb_store
        )
        cluster["commentary"] = commentary.get(cluster_key(cluster))

    weekly_alignment = compute_weekly_alignment(events, emb_store, pos_emb_store, today=edition_dt)
    topic_weekly = compute_topic_weekly_stances(events, today=edition_dt)
    stance_overall = topic_weekly.get("overall") or []
    # Use the stance series only once it has enough scored weeks to draw a line;
    # otherwise fall back to the embedding-based series (partial-backfill state).
    if sum(1 for w in stance_overall if w is not None) >= 2:
        initial_weekly = stance_overall
    else:
        initial_weekly = weekly_alignment
        topic_weekly = {}
    timeline_svg = build_timeline_svg_data(initial_weekly)
    topic_series_json = json.dumps(
        {
            k: [
                None
                if w is None
                else {
                    "week": w["week"],
                    "score": w["overall"],
                    "display": w.get("display") or f"{int(w['overall'] * 100)}%",
                    "label": w["label"],
                    "color": w["color"],
                }
                for w in v
            ]
            for k, v in topic_weekly.items()
        }
    )
    # Pill state per topic: latest scored week of the stance series (survives
    # quiet weeks, unlike the old 7-day heatmap); cosine heatmap as fallback.
    heatmap = compute_latest_heatmap(clusters, today=edition_dt)
    for area in ISSUE_ORDER:
        series = topic_weekly.get(area) or []
        latest = next((w for w in reversed(series) if w is not None), None)
        if latest and sum(1 for w in series if w is not None) >= 2:
            heatmap[area] = {
                "label": latest["label"],
                "color": latest["color"],
                "overall": latest["overall"],
                "display": latest["display"],
            }

    # Recent events: last 90 days before the edition cutoff
    cutoff = (edition_dt - timedelta(days=90)).strftime("%Y-%m-%d")
    recent_events = [e for e in events if (e.get("date") or "") >= cutoff]

    # Per-country stats for country cards and country pages
    cutoff_7 = (edition_dt - timedelta(days=7)).strftime("%Y-%m-%d")
    weekly_counts: dict[str, int] = {a: 0 for a in WEIMAR_ACTORS}
    for e in events:
        if (e.get("date") or "") >= cutoff_7:
            actor = SOURCE_ACTOR.get(e.get("source_name", ""))
            if actor in weekly_counts:
                weekly_counts[actor] += 1

    # Pairwise stance agreement over the last 14 days: for each topic both
    # countries rated, agreement = 1 - |meanA - meanB| / 4 (4 = full scale width);
    # pair score = mean across shared topics. Falls back to embedding medians
    # when no stance data exists.
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

    if not pair_scores:
        latest_pw = next((w for w in reversed(weekly_alignment) if w is not None), None)
        if latest_pw:
            for pk, pv in (latest_pw.get("by_pair") or {}).items():
                pair_scores[pk] = int((pv.get("median") or 0) * 100)

    def _pair_score(a1: str, a2: str) -> int | str:
        for k in (f"{a1}_{a2}", f"{a2}_{a1}"):
            if k in pair_scores:
                return pair_scores[k]
        return "—"

    country_stats = {
        actor: {
            **COUNTRY_FM[actor],
            "weekly_count": weekly_counts[actor],
            "align": {other: _pair_score(actor, other) for other in WEIMAR_ACTORS if other != actor},
        }
        for actor in WEIMAR_ACTORS
    }

    # Continue-card date range, anchored to the edition cutoff
    today_utc = edition_dt
    coverage_from = (today_utc - timedelta(days=14)).strftime("%-d %b")
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

    export_filename = write_current_month_export(out, all_events)

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
            timeline_svg=timeline_svg,
            heatmap=heatmap,
            issue_order=ISSUE_ORDER,
            country_stats=country_stats,
            weimar_actors=WEIMAR_ACTORS,
            coverage_from=coverage_from,
            coverage_to=coverage_to,
            next_tuesday_str=next_tuesday_str,
            topic_series_json=topic_series_json,
            latest_event_date=latest_event_date,
            stale_days=stale_days,
            export_filename=export_filename,
        ),
        encoding="utf-8",
    )

    # docs/meetings/index.html
    (out / "meetings").mkdir(exist_ok=True)
    tmpl = env.get_template("meetings.html")
    (out / "meetings" / "index.html").write_text(
        tmpl.render(meetings=meetings, milestones=milestones, annual=annual),
        encoding="utf-8",
    )

    # docs/sources/index.html
    (out / "sources").mkdir(exist_ok=True)
    tmpl = env.get_template("sources.html")
    (out / "sources" / "index.html").write_text(
        tmpl.render(
            source_stats=source_stats,
            source_health=source_health,
            run=run,
            source_labels=SOURCE_LABELS,
            all_events_count=len(all_events),
            weimar_events_count=len(events),
        ),
        encoding="utf-8",
    )

    # docs/{country}/index.html — one page per Weimar country
    tmpl = env.get_template("country.html")
    for actor in WEIMAR_ACTORS:
        fm = COUNTRY_FM[actor]
        country_events = [e for e in recent_events if SOURCE_ACTOR.get(e.get("source_name", "")) == actor]
        others = [cs for a, cs in country_stats.items() if a != actor]
        (out / fm["path"]).mkdir(exist_ok=True)
        (out / fm["path"] / "index.html").write_text(
            tmpl.render(
                actor=actor,
                fm=fm,
                country_events=country_events,
                stats=country_stats[actor],
                others=others,
                weimar_actors=WEIMAR_ACTORS,
                country_stats=country_stats,
            ),
            encoding="utf-8",
        )

    # Root-level deploy files, beside (not inside) the base-path subdir.
    # 404.html is what Cloudflare serves for unknown paths (wrangler.jsonc
    # not_found_handling); _redirects sends the bare root into the site and is
    # only meaningful when the site sits under a path prefix.
    (root / "404.html").write_text(env.get_template("404.html").render() + "\n", encoding="utf-8")
    if base_path:
        (root / "_redirects").write_text(f"/  {base_path}/  301\n", encoding="utf-8")

    print(f"Rendered → {root.resolve()}")
    print(f"  recent events (90d): {len(recent_events)}, clusters: {len(clusters)}")
    print(f"  meetings: {len(meetings)}, milestones: {len(milestones)}")


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
