#!/usr/bin/env python3
"""
Fetches current vote counts from the hub page's "vote for the next grouping"
feature (worker/index.js's /api/votes) and reports them as a histogram — a
terminal bar chart by default, or an HTML report for the weekly email (see
.github/workflows/vote_report.yml).

Grouping display names come from pipeline.render.HUB_GROUPINGS, so the report
stays in sync with the hub page without a second slug→name mapping to maintain.

Usage:
    uv run python -m pipeline.vote_report
    uv run python -m pipeline.vote_report --format html > /tmp/vote_report.html
    uv run python -m pipeline.vote_report --url https://<branch>.workers.dev/api/votes
"""

from __future__ import annotations

import argparse
import html as html_lib

import requests

from pipeline.render import HUB_GROUPINGS

DEFAULT_URL = "https://minilaterals.com/api/votes"
BAR_WIDTH = 30  # terminal bar chart max width, in characters


def fetch_counts(url: str) -> dict[str, int]:
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()["counts"]


def _ranked(counts: dict[str, int]) -> list[tuple[str, int]]:
    """(display name, count) for every known grouping, highest votes first."""
    rows = [(m["name"], counts.get(m["slug"], 0)) for m in HUB_GROUPINGS]
    return sorted(rows, key=lambda r: (-r[1], r[0]))


def render_text(counts: dict[str, int]) -> str:
    rows = _ranked(counts)
    total = sum(c for _, c in rows)
    max_count = max((c for _, c in rows), default=0)
    name_width = max((len(name) for name, _ in rows), default=0)

    lines = [f"Vote report — {total} total vote{'' if total == 1 else 's'} across {len(rows)} groupings", ""]
    for name, count in rows:
        bar_len = round(BAR_WIDTH * count / max_count) if max_count else 0
        bar = "#" * bar_len
        lines.append(f"{name:<{name_width}}  {bar:<{BAR_WIDTH}} {count}")
    return "\n".join(lines)


def render_html(counts: dict[str, int]) -> str:
    rows = _ranked(counts)
    total = sum(c for _, c in rows)
    max_count = max((c for _, c in rows), default=0)

    bar_rows = []
    for name, count in rows:
        pct = round(100 * count / max_count) if max_count else 0
        bar_rows.append(
            "<tr>"
            f'<td style="padding:4px 10px 4px 0;white-space:nowrap;font-family:sans-serif;'
            f'font-size:13px;color:#1c1812;">{html_lib.escape(name)}</td>'
            f'<td style="width:100%;padding:4px 0;">'
            f'<div style="background:#8a3a23;height:14px;border-radius:3px;'
            f'width:{pct}%;min-width:{2 if count else 0}px;"></div></td>'
            f'<td style="padding:4px 0 4px 10px;font-family:sans-serif;font-size:13px;'
            f'color:#7a7060;text-align:right;">{count}</td>'
            "</tr>"
        )

    return (
        '<div style="font-family:sans-serif;color:#1c1812;">'
        f"<p>{total} total vote{'' if total == 1 else 's'} across {len(rows)} groupings.</p>"
        '<table style="border-collapse:collapse;width:100%;max-width:640px;">'
        + "".join(bar_rows)
        + "</table></div>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help="votes API endpoint (default: production)")
    parser.add_argument("--format", choices=["text", "html"], default="text")
    args = parser.parse_args()

    counts = fetch_counts(args.url)
    print(render_html(counts) if args.format == "html" else render_text(counts))


if __name__ == "__main__":
    main()
