#!/usr/bin/env python3
"""
Reads current vote counts directly from the VOTES KV namespace via the
Cloudflare API and prints them as a terminal histogram.

This deliberately does not go through any HTTP endpoint on the site —
worker/index.js only exposes routes to cast a vote or a notify-me signup, not
to read the tallies back (see the comment in worker/index.js), so this is the
only way to see the standings. It's authenticated with your own Cloudflare
API token, so it only works for whoever holds that token — not for anyone
who just visits the site or clones this repo.

Grouping display names come from pipeline.render.HUB_GROUPINGS, so the report
stays in sync with the hub page without a second slug→name mapping to maintain.

Only counts real production votes: worker/index.js buckets anything cast
against a non-production hostname (branch previews, *.workers.dev) under a
"preview:votes:*" key prefix instead of "votes:*", and the `prefix=votes:`
filter below only ever matches the latter — so votes from testing a preview
deployment never inflate what this reports.

Setup (one-time): create a token at
https://dash.cloudflare.com/profile/api-tokens with "Workers KV Storage:
Read" permission scoped to this account, then:

    export CLOUDFLARE_API_TOKEN=...

Usage:
    uv run python -m pipeline.vote_report
"""

from __future__ import annotations

import os
import sys

import requests

from pipeline.render import HUB_GROUPINGS

ACCOUNT_ID = "ee9d519739225a663addb76c8e7e0d34"
KV_NAMESPACE_ID = "59cf38506c0340eeaba6abed0fd552cb"
API_BASE = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}"
BAR_WIDTH = 30  # terminal bar chart max width, in characters


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not token:
        sys.exit(
            "CLOUDFLARE_API_TOKEN is not set.\n\n"
            "This script reads vote tallies directly from Cloudflare KV, authenticated as you —\n"
            "nobody else can run it without your token. Create one at\n"
            "https://dash.cloudflare.com/profile/api-tokens with 'Workers KV Storage: Read'\n"
            "permission scoped to this account, then:\n\n"
            "    export CLOUDFLARE_API_TOKEN=...\n"
        )
    return {"Authorization": f"Bearer {token}"}


def fetch_counts() -> dict[str, int]:
    headers = _auth_headers()

    keys_resp = requests.get(f"{API_BASE}/keys", headers=headers, params={"prefix": "votes:"}, timeout=10)
    keys_resp.raise_for_status()
    keys_body = keys_resp.json()
    if not keys_body.get("success"):
        raise RuntimeError(f"Cloudflare API error: {keys_body.get('errors')}")

    counts: dict[str, int] = {}
    for key in keys_body["result"]:
        slug = key["name"].removeprefix("votes:")
        value_resp = requests.get(f"{API_BASE}/values/{key['name']}", headers=headers, timeout=10)
        value_resp.raise_for_status()
        counts[slug] = int(value_resp.text or "0")
    return counts


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


def main() -> None:
    print(render_text(fetch_counts()))


if __name__ == "__main__":
    main()
