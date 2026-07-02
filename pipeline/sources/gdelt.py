from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests

from .base import BaseIngester, Event

# GDELT DOC 2.0 API — no auth required; rate limit undocumented, be conservative.
# Cap weimar_score at 0.6 — GDELT relevance is noisier than official MFA sources.
API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
SOURCE_NAME = "gdelt"
SCORE_CAP = 0.6

_QUERY = '"Weimar Triangle" OR ("Germany" "France" "Poland" "trilateral")'
_HEADERS = {"User-Agent": "WeimTracker/1.0 (+https://github.com/weimar-tracker)"}


def _parse_date(raw: str | None) -> tuple[str, str]:
    """GDELT dates are 'YYYYMMDDTHHMMSSZ' or ISO 8601."""
    if not raw:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
    # GDELT compact format: 20260507T140000Z
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


class GDELTIngester(BaseIngester):
    source_name = SOURCE_NAME
    source_lang = "en"

    def fetch(self) -> Iterator[Event]:
        if self.since:
            yield from self._fetch_range(self.since)
        else:
            yield from self._fetch_window("7d")

    def _fetch_window(self, timespan: str) -> Iterator[Event]:
        params = {
            "query": _QUERY,
            "mode": "artlist",
            "format": "json",
            "timespan": timespan,
            "maxrecords": 250,
        }
        yield from self._query(params)

    def _fetch_range(self, since: str) -> Iterator[Event]:
        """Query in monthly chunks from `since` to today to stay within GDELT limits."""
        start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        today = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59)
        chunk_start = start
        while chunk_start < today:
            chunk_end = min(chunk_start + timedelta(days=30), today)
            params = {
                "query": _QUERY,
                "mode": "artlist",
                "format": "json",
                "startdatetime": chunk_start.strftime("%Y%m%d%H%M%S"),
                "enddatetime": chunk_end.strftime("%Y%m%d%H%M%S"),
                "maxrecords": 250,
            }
            print(f"[{SOURCE_NAME}] fetching {chunk_start.date()} → {chunk_end.date()}")
            yield from self._query(params)
            time.sleep(6)  # GDELT requires ≥5s between requests
            chunk_start = chunk_end + timedelta(seconds=1)

    def _query(self, params: dict) -> Iterator[Event]:
        try:
            r = requests.get(API_URL, params=params, timeout=20, headers=_HEADERS)
            if r.status_code == 429:
                print(f"[{SOURCE_NAME}] Rate limited — skipping chunk")
                return
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            print(f"[{SOURCE_NAME}] API error: {exc}")
            return

        articles = data.get("articles") or []
        for article in articles:
            url = article.get("url", "")
            title = article.get("title", "").strip()
            if not url or not title:
                continue
            date, published_at = _parse_date(article.get("seendate"))
            summary = article.get("socialimage", "")

            event = Event(
                source_name=SOURCE_NAME,
                title=title,
                text=summary,
                source_url=url,
                source_lang=article.get("language", "en").lower()[:2],
                source_published_at=published_at,
                date=date,
            ).classify()

            event.weimar_score = round(min(event.weimar_score, SCORE_CAP), 3)
            yield event
            time.sleep(0.3)
