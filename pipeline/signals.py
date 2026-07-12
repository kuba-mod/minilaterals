#!/usr/bin/env python3
"""
Weimar Triangle tracker — "silence as signal" divergence alerts.

The core insight: agreement isn't the only interesting signal. A ministry going
quiet on a topic it normally comments on — while its Weimar partners keep
publishing — is itself informative, and nobody else is watching publication
frequency closely enough to notice it. See GitHub issue #19 for the full spec.

Two computation cadences, kept deliberately separate:
  1. Published (weekly, frozen, part of the citable record) — `main()` here,
     run once per edition cut (mirrors pipeline/comment.py). Reads/writes
     data/signals_history.json (dedup/rate-limit state across editions) and
     data/signals.json (the signals rendered on the site this edition).
  2. Live (daily, unfrozen, dogfood-only) — `main(live=True)`, run on every
     daily CI tick. Computes off current unfrozen data and pings ntfy/Telegram
     when a new silence appears; never touches the published files above.

Usage:
    python -m pipeline.signals               # edition step: update history + data/signals.json
    python -m pipeline.signals --dry-run      # print without writing
    python -m pipeline.signals --live         # daily dogfood check + notify
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

HISTORY_FILE = ROOT / "data" / "signals_history.json"
SIGNALS_FILE = ROOT / "data" / "signals.json"
LIVE_SNAPSHOT_FILE = ROOT / "data" / "signals_live_snapshot.json"

# Bump this whenever the thresholds/algorithm below change materially. Old
# entries in signals_history.json keep whatever version produced them — a past
# alert is never silently reinterpreted under new thresholds.
SIGNALS_METHODOLOGY_VERSION = "1.0"

EPSILON = 0.01

CAPITALS = {"DE": "Berlin", "FR": "Paris", "PL": "Warsaw"}


def _week_start(d: date) -> date:
    """Monday-anchored calendar week containing `d`."""
    return d - timedelta(days=d.weekday())


def _last_complete_week_end(as_of: date) -> date:
    """
    A date inside the most recently *fully elapsed* Monday-Sunday week as of
    `as_of`. Editions cut on Tuesdays (one day into the new week), so
    "the week containing as_of" is almost always still in progress — comparing
    a 1-day-old partial week against a full-week baseline would flag nearly
    every actor/topic as silent regardless of real behaviour. The published
    cadence needs a week that's actually finished; the live/daily cadence
    deliberately does NOT use this (an in-progress week is exactly what gives
    it same-day detection).
    """
    week_start = _week_start(as_of)
    if (as_of - week_start).days < 6:
        return week_start - timedelta(days=1)  # last day of the previous complete week
    return as_of


# ---------------------------------------------------------------------------
# Core detector (pure function of events + as_of)
# ---------------------------------------------------------------------------


def compute_silence_signals(
    events: list[dict],
    as_of: date,
    lookback_weeks: int = 12,
    min_baseline_per_week: float = 0.5,
    silence_ratio_threshold: float = 0.2,
) -> list[dict]:
    """
    Detect (actor, issue_area) pairs whose publication rate this week has
    dropped near-zero relative to their own trailing baseline.

    Bucketing reads the actor↔source mapping from wherever it currently lives
    in pipeline/render.py (SOURCE_ACTOR) rather than hardcoding the 3-MFA
    assumption, so this keeps working once heads-of-government sources land.
    """
    from pipeline.render import ISSUE_ORDER, SOURCE_ACTOR

    actors = sorted(set(SOURCE_ACTOR.values()))
    current_week = _week_start(as_of)

    # counts[(actor, area, week_iso)] — explicit, includes weeks with zero
    # events implicitly (missing key == 0), which is exactly what silence
    # detection needs (unlike compute_topic_weekly_stances, which only ever
    # sees weeks an actor actually published in).
    counts: dict[tuple[str, str, str], int] = {}
    for e in events:
        if not e.get("weimar_relevant"):
            continue
        actor = SOURCE_ACTOR.get(e.get("source_name", ""))
        if actor not in actors:
            continue
        date_str = e.get("date") or ""
        if not date_str:
            continue
        try:
            event_week = _week_start(date.fromisoformat(date_str)).isoformat()
        except ValueError:
            continue
        for area in e.get("issue_areas") or []:
            if area not in ISSUE_ORDER:
                continue
            key = (actor, area, event_week)
            counts[key] = counts.get(key, 0) + 1

    baseline_weeks = [(current_week - timedelta(weeks=i)).isoformat() for i in range(1, lookback_weeks + 1)]

    signals: list[dict] = []
    for actor in actors:
        for area in ISSUE_ORDER:
            baseline_total = sum(counts.get((actor, area, w), 0) for w in baseline_weeks)
            baseline_mean = baseline_total / lookback_weeks
            if baseline_mean < min_baseline_per_week:
                continue  # actor rarely talks about this topic — going quiet isn't news

            current_count = counts.get((actor, area, current_week.isoformat()), 0)
            ratio = current_count / max(baseline_mean, EPSILON)
            if ratio > silence_ratio_threshold:
                continue

            severity = baseline_mean / max(current_count, EPSILON)
            others = {
                other: counts.get((other, area, current_week.isoformat()), 0) for other in actors if other != actor
            }
            signals.append(
                {
                    "actor": actor,
                    "issue_area": area,
                    "week": current_week.isoformat(),
                    "current_count": current_count,
                    "baseline_mean": round(baseline_mean, 2),
                    "baseline_weeks": lookback_weeks,
                    "ratio": round(ratio, 3),
                    "severity": round(severity, 2),
                    "others": others,
                }
            )

    signals.sort(key=lambda s: s["severity"], reverse=True)
    return signals


def format_evidence(signal: dict) -> str:
    """
    Evidence-first phrasing per the plan's signal-discipline rule: never say
    "X went silent," always give the actual counts and baseline it deviates
    from. There's no quote for a silence signal — the numbers are the evidence.
    """
    from pipeline.render import ISSUE_LABELS

    actor_label = CAPITALS.get(signal["actor"], signal["actor"])
    area_label = ISSUE_LABELS.get(signal["issue_area"], signal["issue_area"]).lower()
    n = signal["current_count"]
    base = (
        f"{actor_label}: {n} statement{'s' if n != 1 else ''} on {area_label} this week "
        f"vs. a {signal['baseline_weeks']}-week average of {signal['baseline_mean']:.1f}/week"
    )
    others = signal.get("others") or {}
    if others:
        others_str = " and ".join(f"{CAPITALS.get(a, a)} ({c})" for a, c in sorted(others.items()))
        return f"{base}, while {others_str} published."
    return f"{base}."


# ---------------------------------------------------------------------------
# History / rate-limit / dedup (published cadence only)
# ---------------------------------------------------------------------------


def _load_history() -> dict:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return {}


def _save_history(history: dict) -> None:
    HISTORY_FILE.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")


def build_edition_signals(
    fired: list[dict], history: dict, week_str: str, max_signals: int = 3
) -> tuple[list[dict], dict]:
    """
    Apply cross-edition dedup + the ~3/week rate limit.

    A pair keeps firing every week its silence persists, but only counts as
    "new" the first time or "deepening" if the ratio drops further than the
    best ratio seen during the current streak — otherwise it's "ongoing" and
    carries its original first_fired_week so the UI can say "ongoing since X"
    instead of repeating the same alert. All currently-active signals (any
    status) are eligible for the top-`max_signals` published set; callers that
    want to avoid re-alerting on unchanged silences (e.g. the RSS feed) should
    filter to status in {"new", "deepening"}.
    """
    updated = dict(history)
    fired_keys: set[str] = set()
    candidates: list[dict] = []

    for sig in fired:
        key = f"{sig['actor']}|{sig['issue_area']}"
        fired_keys.add(key)
        prev = history.get(key)

        if not prev or prev.get("status") != "active":
            status = "new"
            first_fired_week = week_str
            best_ratio = sig["ratio"]
        elif sig["ratio"] < prev.get("best_ratio", 1.0) - 1e-9:
            status = "deepening"
            first_fired_week = prev.get("first_fired_week", week_str)
            best_ratio = sig["ratio"]
        else:
            status = "ongoing"
            first_fired_week = prev.get("first_fired_week", week_str)
            best_ratio = prev.get("best_ratio", sig["ratio"])

        enriched = {
            **sig,
            "status": status,
            "first_fired_week": first_fired_week,
            "evidence": format_evidence(sig),
            "methodology_version": SIGNALS_METHODOLOGY_VERSION,
        }
        candidates.append(enriched)

        updated[key] = {
            "actor": sig["actor"],
            "issue_area": sig["issue_area"],
            "first_fired_week": first_fired_week,
            "last_fired_week": week_str,
            "best_ratio": best_ratio,
            "status": "active",
            "methodology_version": SIGNALS_METHODOLOGY_VERSION,
        }

    # A pair that was active but didn't fire this week has resolved; the next
    # time it fires (if ever) is treated as "new" again, not a continuation.
    for key, entry in history.items():
        if key not in fired_keys and entry.get("status") == "active":
            updated[key] = {**entry, "status": "resolved"}

    candidates.sort(key=lambda s: s["severity"], reverse=True)
    published = candidates[:max_signals]
    return published, updated


# ---------------------------------------------------------------------------
# Live dogfood monitoring (daily, unpublished)
# ---------------------------------------------------------------------------


def _push_notification(text: str) -> None:
    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()
    if ntfy_topic:
        try:
            requests.post(
                f"https://ntfy.sh/{ntfy_topic}",
                data=text.encode("utf-8"),
                headers={"Title": "Weimar Triangle: new silence signal"},
                timeout=10,
            )
        except Exception as exc:
            print(f"  ntfy push failed: {exc}")

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if bot_token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        except Exception as exc:
            print(f"  Telegram push failed: {exc}")

    if not ntfy_topic and not (bot_token and chat_id):
        print("  (no NTFY_TOPIC or TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID configured — skipping push)")


def run_live_check(dry_run: bool = False) -> None:
    """
    Daily, ungated, dogfood-only: compute directly off current unfrozen data
    (no edition cutoff), diff against yesterday's snapshot, and push anything
    new. Never touches signals_history.json or data/signals.json — those are
    the citable published record.
    """
    from pipeline.render import load_events

    events = load_events(weimar_only=True)
    today = date.today()
    fired = compute_silence_signals(events, as_of=today)
    fired_keys = {f"{s['actor']}|{s['issue_area']}" for s in fired}

    previous_keys: set[str] = set()
    if LIVE_SNAPSHOT_FILE.exists():
        previous_keys = set(json.loads(LIVE_SNAPSHOT_FILE.read_text(encoding="utf-8")))

    new_keys = fired_keys - previous_keys
    new_signals = [s for s in fired if f"{s['actor']}|{s['issue_area']}" in new_keys]

    print(f"Live check {today.isoformat()}: {len(fired)} active, {len(new_signals)} new")
    for s in new_signals:
        evidence = format_evidence(s)
        print(f"  NEW: {evidence}")
        if not dry_run:
            _push_notification(f"New silence developing:\n{evidence}")

    if not dry_run:
        LIVE_SNAPSHOT_FILE.write_text(json.dumps(sorted(fired_keys)), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI (published edition step)
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Silence-as-signal divergence alerts")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    parser.add_argument("--as-of", default=None, metavar="YYYY-MM-DD", help="Edition cutoff override")
    parser.add_argument("--live", action="store_true", help="Daily dogfood check (unfrozen data, pushes notifications)")
    args = parser.parse_args()

    if args.live:
        run_live_check(dry_run=args.dry_run)
        return

    from pipeline.render import load_events, resolve_edition_date

    edition_dt = resolve_edition_date(args.as_of)
    edition_cutoff = edition_dt.strftime("%Y-%m-%d")
    events = [e for e in load_events(weimar_only=True) if (e.get("date") or "") <= edition_cutoff]

    eval_as_of = _last_complete_week_end(edition_dt.date())
    week_str = _week_start(eval_as_of).isoformat()
    fired = compute_silence_signals(events, as_of=eval_as_of)
    history = _load_history()
    published, updated_history = build_edition_signals(fired, history, week_str)

    print(f"Fired: {len(fired)}  Published this edition ({week_str}): {len(published)}")
    for s in published:
        print(f"  [{s['status']}] {s['evidence']}")

    if not args.dry_run:
        _save_history(updated_history)
        SIGNALS_FILE.write_text(json.dumps(published, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {len(published)} entries to {SIGNALS_FILE}")


if __name__ == "__main__":
    main()
