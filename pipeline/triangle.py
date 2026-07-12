#!/usr/bin/env python3
"""
Weimar Triangle tracker — deforming triangle visual.

Computes the three pairwise stance divergences between DE/FR/PL (mean absolute
gap between per-topic mean stances, averaged across shared topics) and renders
them as an SVG triangle whose edge lengths track divergence: aligned weeks read
as roughly equilateral, a rift visibly stretches the corresponding edge.

`compute_triangle_divergence()` is called by pipeline.render at every render to
draw the *current* edition's triangle. The *previous* edition's triangle (for
the side-by-side "how did it change" comparison) is read back from
data/triangle_history.json rather than recomputed, so it doesn't drift as new
events accumulate between edition cuts.

data/triangle_history.json is written only at edition-cut time — see
cut_edition() below, invoked as `python -m pipeline.triangle` from
.github/workflows/collect.yml right after the weekly edition cut, mirroring
how data/commentary.json is written by pipeline.comment on the same schedule.

Usage:
    python -m pipeline.triangle               # persist this edition's divergence
    python -m pipeline.triangle --as-of 2026-06-24
    python -m pipeline.triangle --dry-run
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

TRIANGLE_HISTORY_FILE = ROOT / "data" / "triangle_history.json"

PAIRS = [("DE", "FR"), ("FR", "PL"), ("DE", "PL")]

# Calibrated against the observed corpus (Jan–Jul 2026, 618 rated statements):
# mean per-topic pairwise gap 0.42, median 0.33, p90 1.0, p95 1.33, max 2.0
# (stances are -2..+2, so a gap of 2.0 already implies opposite-sign averages).
# Using the observed max as the "fully stretched" scale means typical weeks sit
# well under full stretch and only genuine outlier weeks hit it.
DIVERGENCE_SCALE_MAX = 2.0

L_BASE = 200.0  # px — edge length at divergence == 0 (perfectly aligned)
L_MAX = 400.0  # px — edge length at divergence >= DIVERGENCE_SCALE_MAX

VIEWBOX = 700  # square canvas, generous enough that no geometry clips (see below)
CENTER = VIEWBOX / 2


def compute_triangle_divergence(
    events: list[dict],
    as_of: datetime,
    window_days: int = 14,
    previous: dict[str, float] | None = None,
) -> dict:
    """
    Returns {"DE-FR": float, "FR-PL": float, "DE-PL": float, "n_topics": int}
    where each value is the mean absolute difference between the two actors'
    per-topic mean stances, averaged across topics where both actors published
    a rated statement in the window. Higher = more divergent.

    If a pair has zero overlapping topics in the window, falls back to that
    pair's value in `previous` (the last published edition) rather than
    fabricating a zero — a missing comparison is not the same as agreement.
    If there is also no previous value, the pair defaults to 0.0 (nothing to
    show yet; this only happens on the very first edition using this feature).
    """
    from pipeline.render import ISSUE_ORDER

    mfa_sources = {"german_mfa": "DE", "france_diplomatie": "FR", "polish_mfa": "PL"}
    window_end = as_of.strftime("%Y-%m-%d")
    window_start = (as_of - timedelta(days=window_days)).strftime("%Y-%m-%d")

    per_topic_actor: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for e in events:
        actor = mfa_sources.get(e.get("source_name", ""))
        if not actor:
            continue
        date = e.get("date", "")
        if not (window_start <= date <= window_end):
            continue
        stances = (e.get("extracted") or {}).get("stances") or {}
        for topic, entry in stances.items():
            if topic in ISSUE_ORDER and entry and isinstance(entry.get("score"), int):
                per_topic_actor[topic][actor].append(entry["score"])

    per_topic_means = {
        topic: {a: sum(scores) / len(scores) for a, scores in by_actor.items()}
        for topic, by_actor in per_topic_actor.items()
    }

    previous = previous or {}
    result: dict[str, float] = {}
    n_topics = 0
    for a1, a2 in PAIRS:
        gaps = [abs(means[a1] - means[a2]) for means in per_topic_means.values() if a1 in means and a2 in means]
        key = f"{a1}-{a2}"
        if gaps:
            result[key] = sum(gaps) / len(gaps)
            n_topics = max(n_topics, len(gaps))
        else:
            result[key] = previous.get(key, 0.0)

    result["n_topics"] = n_topics
    return result


def _map_length(divergence: float) -> float:
    frac = max(0.0, min(1.0, divergence / DIVERGENCE_SCALE_MAX))
    return L_BASE + (L_MAX - L_BASE) * frac


def _enforce_triangle_inequality(a: float, b: float, c: float) -> tuple[float, float, float]:
    """Scale down whichever side violates a+b>c (each pair) so a valid triangle always exists."""
    for _ in range(3):
        if a >= b + c:
            a = (b + c) * 0.98
        elif b >= a + c:
            b = (a + c) * 0.98
        elif c >= a + b:
            c = (a + b) * 0.98
        else:
            break
    return a, b, c


def _solve_vertices(divergence: dict[str, float]) -> dict[str, tuple[float, float]]:
    """
    Map divergence scalars to edge lengths, clamp to a valid triangle, and solve
    for vertex coordinates via SSS placement (DE, FR on a baseline; solve for PL).
    """
    a = _map_length(divergence["FR-PL"])  # side opposite DE
    b = _map_length(divergence["DE-PL"])  # side opposite FR
    c = _map_length(divergence["DE-FR"])  # side opposite PL
    a, b, c = _enforce_triangle_inequality(a, b, c)

    de = (0.0, 0.0)
    fr = (c, 0.0)
    pl_x = (b * b - a * a + c * c) / (2 * c)
    pl_y = math.sqrt(max(b * b - pl_x * pl_x, 0.0))
    pl = (pl_x, pl_y)

    # Center on the centroid (not the DE vertex) so week-to-week size changes
    # read as the shape "breathing" rather than the whole triangle sliding.
    cx = (de[0] + fr[0] + pl[0]) / 3
    cy = (de[1] + fr[1] + pl[1]) / 3
    return {
        "DE": (CENTER + de[0] - cx, CENTER + de[1] - cy),
        "FR": (CENTER + fr[0] - cx, CENTER + fr[1] - cy),
        "PL": (CENTER + pl[0] - cx, CENTER + pl[1] - cy),
    }


def _blend_hex(c1: str, c2: str) -> str:
    """Midpoint blend of two #rrggbb colors — used to tint an edge by its pair."""
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    return f"#{(r1 + r2) // 2:02x}{(g1 + g2) // 2:02x}{(b1 + b2) // 2:02x}"


# Same dark card + cream center dot + rounded colored strokes as FAVICON_SVG
# (pipeline/render.py), so the standalone triangle reads as the site's mark.
BG_DARK = "#1c1812"
CREAM = "#f4ecdb"


def render_triangle_svg(divergence: dict[str, float], actor_colors: dict[str, str]) -> str:
    """Standalone SVG string, styled after the site's favicon: a dark rounded card with
    thick rounded-cap edges tinted by each pair's blended colors, a cream center dot,
    and colored vertex dots labeled DE/FR/PL."""
    vertices = _solve_vertices(divergence)
    de, fr, pl = vertices["DE"], vertices["FR"], vertices["PL"]

    def edge(a1: str, p1: tuple[float, float], a2: str, p2: tuple[float, float]) -> str:
        color = _blend_hex(actor_colors[a1], actor_colors[a2])
        return (
            f'<line x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}" '
            f'stroke="{color}" stroke-width="8" stroke-linecap="round"/>'
        )

    def vertex(actor: str, p: tuple[float, float], label_dy: float) -> str:
        return (
            f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="10" fill="{actor_colors[actor]}"/>'
            f'<text x="{p[0]:.1f}" y="{p[1] + label_dy:.1f}" text-anchor="middle" font-size="20" '
            f'font-weight="600" font-family="Georgia, serif" fill="{actor_colors[actor]}">{actor}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEWBOX} {VIEWBOX}">
<rect width="{VIEWBOX}" height="{VIEWBOX}" rx="20" fill="{BG_DARK}"/>
{edge("DE", de, "FR", fr)}
{edge("FR", fr, "PL", pl)}
{edge("DE", de, "PL", pl)}
<circle cx="{CENTER}" cy="{CENTER}" r="4" fill="{CREAM}"/>
{vertex("DE", de, -20)}
{vertex("FR", fr, -20)}
{vertex("PL", pl, 34)}
</svg>
"""


# ---------------------------------------------------------------------------
# History persistence — edition-cut time only
# ---------------------------------------------------------------------------


def load_triangle_history() -> dict[str, dict]:
    if TRIANGLE_HISTORY_FILE.exists():
        return json.loads(TRIANGLE_HISTORY_FILE.read_text(encoding="utf-8"))
    return {}


def save_triangle_history(history: dict[str, dict]) -> None:
    TRIANGLE_HISTORY_FILE.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def previous_triangle_entry(history: dict[str, dict], edition_cutoff: str) -> dict | None:
    """Most recent history entry strictly before `edition_cutoff`, or None if there isn't one."""
    older = sorted(cutoff for cutoff in history if cutoff < edition_cutoff)
    return history[older[-1]] if older else None


def cut_edition(as_of: str | None = None, dry_run: bool = False) -> dict:
    from pipeline.render import load_events, resolve_edition_date

    edition_dt = resolve_edition_date(as_of)
    edition_cutoff = edition_dt.strftime("%Y-%m-%d")

    events = load_events(weimar_only=True)
    events = [e for e in events if (e.get("date") or "") <= edition_cutoff]

    history = load_triangle_history()
    previous = previous_triangle_entry(history, edition_cutoff)
    divergence = compute_triangle_divergence(events, edition_dt, previous=previous)

    print(f"Triangle divergence for {edition_cutoff}: {divergence}")
    if dry_run:
        return divergence

    history[edition_cutoff] = divergence
    save_triangle_history(history)
    print(f"→ data/triangle_history.json ({len(history)} editions)")
    return divergence


def main() -> None:
    parser = argparse.ArgumentParser(description="Persist this edition's triangle divergence")
    parser.add_argument(
        "--as-of",
        default=None,
        metavar="YYYY-MM-DD",
        help="Edition cutoff override (default: data/edition.yaml, else today)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    args = parser.parse_args()
    cut_edition(as_of=args.as_of, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
