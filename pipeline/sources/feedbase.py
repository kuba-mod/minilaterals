from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime

import feedparser
import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# Generic RSS/Atom ingester used by the additional-minilateral MFA sources
# (E3, Visegrád, Baltic Three, AUKUS). Standardised feed parsing (via
# feedparser) is far less fragile than site-specific HTML scraping, so each new
# source is a thin subclass that only sets source_name, source_lang, and
# feed_url. A subclass whose ministry has no usable feed can override fetch().
#
# NOTE: feed_url values are the ministries' official RSS/Atom endpoints per
# published conventions, but were not live-verified in the authoring
# environment (outbound access was restricted). The collect workflow runs with
# open network; the first CI run surfaces any endpoint that needs adjusting.
# run_ingester() isolates per-source failures, so a dead feed logs and yields
# nothing rather than breaking the day's collection.

_HEADERS = {"User-Agent": "minilaterals.com diplomatic tracker (+https://minilaterals.com)"}

_MAX_TEXT = 5000


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def _entry_text(entry) -> str:
    """Best available body text from a feed entry: full content if present,
    else the summary/description, with any markup stripped."""
    content = entry.get("content")
    if content:
        raw = content[0].get("value", "") if isinstance(content, list) else str(content)
        text = _strip_html(raw)
        if text:
            return text[:_MAX_TEXT]
    return _strip_html(entry.get("summary", ""))[:_MAX_TEXT]


def _entry_dates(entry) -> tuple[str, str]:
    """(date 'YYYY-MM-DD', ISO datetime) from an entry's published/updated time.
    feedparser normalises most formats into *_parsed struct_time (UTC)."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        dt = datetime(*parsed[:6], tzinfo=UTC)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


class FeedIngester(BaseIngester):
    source_name = ""
    source_lang = "en"
    feed_url = ""
    # Every event from a FeedIngester subclass came via a syndicated feed, so this
    # is "rss" for both RSS and Atom (the existing collection_method vocabulary —
    # see schemas.py — doesn't distinguish the two). A subclass can override this
    # if it ever needs a different label.
    collection_method = "rss"

    def fetch(self) -> Iterator[Event]:
        yield from self.parse_feed(self._download())

    def _download(self) -> bytes:
        """Fetch the raw feed body via requests (with an explicit timeout), then
        hand the bytes to feedparser to parse — NOT feedparser.parse(url), which
        does its own network fetch with no timeout of its own and can hang
        indefinitely on an unresponsive server, stalling the whole sequential
        ingest run. Split out so tests can drive parse_feed() directly with a
        fixture instead of hitting the network."""
        try:
            r = requests.get(self.feed_url, headers=_HEADERS, timeout=15)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — surfaced by run_ingester
            print(f"[{self.source_name}] feed error: {exc}")
            return b""
        return r.content

    def parse_feed(self, feed) -> Iterator[Event]:
        if not feed:
            return
        # Accept either a pre-parsed feedparser result (from _download) or a raw
        # string (tests pass fixture XML straight in).
        if isinstance(feed, (str, bytes)):
            feed = feedparser.parse(feed)
        entries = getattr(feed, "entries", [])
        if not entries:
            print(f"[{self.source_name}] feed yielded no entries ({self.feed_url})")
            return
        for entry in entries:
            title = (entry.get("title") or "").strip()
            url = entry.get("link") or ""
            if not title or not url:
                continue
            date, published_at = _entry_dates(entry)
            if self.since and date < self.since:
                continue
            yield Event(
                source_name=self.source_name,
                title=title,
                text=_entry_text(entry),
                source_url=url,
                source_lang=self.source_lang,
                collection_method=self.collection_method,
                source_published_at=published_at,
                date=date,
            )
            time.sleep(0.1)
