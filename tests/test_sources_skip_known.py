"""Tier 2 — BaseIngester.already_ingested and the fetch-skip it gates.

Routine runs re-see the same listing/feed items every day (~97% of them), and
save() refuses to overwrite an existing file, so the per-article body fetch
behind a known item is always discarded. These tests pin that the skip fires,
that it doesn't fire in backfill mode, and — the part that's easy to regress —
that an ingester whose every item is skipped does NOT look like an empty feed
and trigger a heavier fallback path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.sources import base, german_mfa, polish_mfa
from pipeline.sources.base import BaseIngester, Event


class StubIngester(BaseIngester):
    source_name = "stub_source"

    def fetch(self):
        return iter(())


@pytest.fixture
def events_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(base, "EVENTS_DIR", tmp_path)
    return tmp_path


def _write(events_dir: Path, source: str, url: str, title: str, date: str = "2026-06-01") -> None:
    event = Event(
        source_name=source,
        title=title,
        text="",
        source_url=url,
        source_lang="de",
        source_published_at=f"{date}T00:00:00Z",
        date=date,
    )
    event.save(str(events_dir))


# --- already_ingested ------------------------------------------------------


def test_already_ingested_true_for_saved_event(events_dir):
    _write(events_dir, "stub_source", "https://x/1", "A")
    assert StubIngester().already_ingested("https://x/1", "A")


def test_already_ingested_false_for_unseen_event(events_dir):
    _write(events_dir, "stub_source", "https://x/1", "A")
    assert not StubIngester().already_ingested("https://x/2", "B")


def test_already_ingested_matches_regardless_of_month(events_dir):
    # The hash is over url+title only, so a file filed under a different month
    # still counts — several ingesters only learn an item's date from the
    # article page, i.e. from the fetch this predicate exists to avoid.
    _write(events_dir, "stub_source", "https://x/1", "A", date="2024-01-09")
    assert StubIngester().already_ingested("https://x/1", "A")


def test_already_ingested_scoped_to_own_source(events_dir):
    _write(events_dir, "other_source", "https://x/1", "A")
    assert not StubIngester().already_ingested("https://x/1", "A")


def test_already_ingested_false_when_source_dir_absent(events_dir):
    assert not StubIngester().already_ingested("https://x/1", "A")


# --- the skip in the daily RSS path ----------------------------------------

FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Known statement</title>
    <link>https://www.auswaertiges-amt.de/de/news/1</link>
    <pubDate>Mon, 01 Jun 2026 09:00:00 +0000</pubDate>
    <description>snippet</description>
  </item>
  <item>
    <title>New statement</title>
    <link>https://www.auswaertiges-amt.de/de/news/2</link>
    <pubDate>Tue, 02 Jun 2026 09:00:00 +0000</pubDate>
    <description>snippet</description>
  </item>
</channel></rss>"""


@pytest.fixture
def german_feed(monkeypatch):
    """Serve the fixture feed and record every article body fetch."""
    # Capture the real parser before patching — german_mfa.feedparser IS the
    # feedparser module, so a lambda calling feedparser.parse would recurse.
    real_parse = german_mfa.feedparser.parse
    monkeypatch.setattr(german_mfa.feedparser, "parse", lambda *a, **k: real_parse(FEED))
    fetched: list[str] = []

    def fake_body(self, url):
        fetched.append(url)
        return "body text"

    monkeypatch.setattr(german_mfa.GermanMFAIngester, "_fetch_body", fake_body)
    monkeypatch.setattr(german_mfa.time, "sleep", lambda *_: None)
    return fetched


def test_daily_run_skips_body_fetch_for_known_item(events_dir, german_feed):
    _write(events_dir, "german_mfa", "https://www.auswaertiges-amt.de/de/news/1", "Known statement")

    ingester = german_mfa.GermanMFAIngester()
    events = list(ingester.fetch())

    assert german_feed == ["https://www.auswaertiges-amt.de/de/news/2"]
    assert [e.title for e in events] == ["New statement"]
    assert ingester.known_skipped == 1


def test_all_known_does_not_trigger_html_fallback(events_dir, german_feed, monkeypatch):
    # The regression this guards: the daily fallback used to key off "_fetch_rss
    # yielded no events", which is now the normal case on a quiet day. Falling
    # back would paginate the listing and fetch every article on it.
    for url, title in (("1", "Known statement"), ("2", "New statement")):
        _write(events_dir, "german_mfa", f"https://www.auswaertiges-amt.de/de/news/{url}", title)

    def boom(self):
        raise AssertionError("HTML pagination must not run when the feed had entries")

    monkeypatch.setattr(german_mfa.GermanMFAIngester, "_fetch_html_paginated", boom)

    ingester = german_mfa.GermanMFAIngester()
    assert list(ingester.fetch()) == []
    assert german_feed == []
    assert ingester.known_skipped == 2


def test_empty_feed_still_falls_back_to_html(events_dir, monkeypatch):
    real_parse = german_mfa.feedparser.parse
    monkeypatch.setattr(german_mfa.feedparser, "parse", lambda *a, **k: real_parse("<rss><channel/></rss>"))
    called = []
    monkeypatch.setattr(
        german_mfa.GermanMFAIngester, "_fetch_html_paginated", lambda self: called.append(1) or iter(())
    )

    list(german_mfa.GermanMFAIngester().fetch())
    assert called == [1]


# --- backfill keeps walking every item -------------------------------------


def test_backfill_does_not_skip_known_items(events_dir, monkeypatch):
    # polish_mfa derives its pagination boundary from the dates of the items on
    # the page, so --since must still visit items already on disk.
    _write(events_dir, "polish_mfa", "https://www.gov.pl/a", "Known")

    fetched: list[str] = []
    monkeypatch.setattr(polish_mfa.PolishMFAIngester, "_fetch_body", lambda self, url: fetched.append(url) or "body")
    monkeypatch.setattr(polish_mfa.time, "sleep", lambda *_: None)

    page_1 = """<article><li><div class="title"><a href="/a">Known</a></div>
                <div class="date">01.06.2026</div></li></article>"""

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    # Only the first request carries items; later pages come back empty so the
    # backfill pagination terminates instead of re-serving page 1 forever.
    pages = iter([page_1])
    monkeypatch.setattr(polish_mfa.requests, "get", lambda *a, **k: FakeResponse(next(pages, "")))

    ingester = polish_mfa.PolishMFAIngester(since="2026-01-01")
    list(ingester.fetch())

    assert fetched == ["https://www.gov.pl/a"]
    assert ingester.known_skipped == 0
