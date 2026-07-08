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
        gaps = [
            abs(means[a1] - means[a2]) for means in per_topic_means.values() if a1 in means and a2 in means
        ]
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


def render_triangle_svg(divergence: dict[str, float], actor_colors: dict[str, str]) -> str:
    """Standalone SVG string — solid neutral edges, colored vertex dots labeled DE/FR/PL."""
    vertices = _solve_vertices(divergence)
    de, fr, pl = vertices["DE"], vertices["FR"], vertices["PL"]

    def label(actor: str, x: float, y: float, dy: float) -> str:
        return (
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="{actor_colors[actor]}" '
            f'stroke="#f4ecdb" stroke-width="2"/>'
            f'<text x="{x:.1f}" y="{y + dy:.1f}" text-anchor="middle" font-size="20" '
            f'font-family="Georgia, serif" fill="{actor_colors[actor]}">{actor}</text>'
        )

    points = f"{de[0]:.1f},{de[1]:.1f} {fr[0]:.1f},{fr[1]:.1f} {pl[0]:.1f},{pl[1]:.1f}"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEWBOX} {VIEWBOX}">
<polygon points="{points}" fill="#9a8a2818" stroke="#7a7060" stroke-width="2.5" stroke-linejoin="round"/>
{label("DE", de[0], de[1], -18)}
{label("FR", fr[0], fr[1], -18)}
{label("PL", pl[0], pl[1], 32)}
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
    TRIANGLE_HISTORY_FILE.write_text(
        json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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
