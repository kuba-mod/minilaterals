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

--reset/--reset-all (clearing manipulated or test votes) need a token with
"Workers KV Storage: Edit" instead (Edit implies Read) — widen the token's
scope, or create a second one just for resets.

Usage:
    uv run python -m pipeline.vote_report
    uv run python -m pipeline.vote_report --reset quad       # one grouping, with confirmation prompt
    uv run python -m pipeline.vote_report --reset-all --yes  # everything, no prompt
"""

from __future__ import annotations

import argparse
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


def _list_keys(headers: dict[str, str], prefix: str) -> list[str]:
    resp = requests.get(f"{API_BASE}/keys", headers=headers, params={"prefix": prefix}, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"Cloudflare API error: {body.get('errors')}")
    return [key["name"] for key in body["result"]]


def _delete_keys(headers: dict[str, str], keys: list[str]) -> None:
    if not keys:
        return
    resp = requests.post(f"{API_BASE}/bulk/delete", headers=headers, json=keys, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"Cloudflare API error: {body.get('errors')}")
    unsuccessful = (body.get("result") or {}).get("unsuccessful_keys") or []
    if unsuccessful:
        raise RuntimeError(f"Cloudflare failed to delete {len(unsuccessful)} key(s): {unsuccessful}")


def fetch_counts() -> dict[str, int]:
    headers = _auth_headers()
    keys = _list_keys(headers, "votes:")

    counts: dict[str, int] = {}
    for key in keys:
        slug = key.removeprefix("votes:")
        value_resp = requests.get(f"{API_BASE}/values/{key}", headers=headers, timeout=10)
        value_resp.raise_for_status()
        counts[slug] = int(value_resp.text or "0")
    return counts


def reset_slug(slug: str, *, skip_confirm: bool) -> int:
    """Delete a grouping's vote counter and every per-IP voter marker (production keys only —
    preview:* test data from branch deployments is left alone). Returns how many keys were deleted."""
    headers = _auth_headers()
    keys = _list_keys(headers, f"voter:{slug}:")
    keys.append(f"votes:{slug}")  # harmless if it doesn't exist — KV delete is a no-op on missing keys

    if not skip_confirm:
        print(f"About to delete {len(keys)} key(s) for '{slug}':")
        for key in keys:
            print(f"  {key}")
        if input("Type 'yes' to confirm: ").strip().lower() != "yes":
            print("Aborted, nothing deleted.")
            return 0

    _delete_keys(headers, keys)
    print(f"Deleted {len(keys)} key(s) for '{slug}'.")
    return len(keys)


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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", metavar="SLUG", help="delete one grouping's vote counter and voter markers")
    parser.add_argument("--reset-all", action="store_true", help="delete every tracked grouping's votes")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt for --reset/--reset-all")
    args = parser.parse_args()

    valid_slugs = {m["slug"] for m in HUB_GROUPINGS}

    if args.reset_all:
        for slug in sorted(valid_slugs):
            reset_slug(slug, skip_confirm=args.yes)
        return

    if args.reset:
        if args.reset not in valid_slugs:
            sys.exit(f"Unknown slug '{args.reset}'. Valid slugs: {', '.join(sorted(valid_slugs))}")
        reset_slug(args.reset, skip_confirm=args.yes)
        return

    print(render_text(fetch_counts()))


if __name__ == "__main__":
    main()
