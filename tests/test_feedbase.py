"""Feed parsing for the RSS/Atom-based minilateral ingesters (feedbase.py)."""

from __future__ import annotations

from unittest.mock import patch

import requests

from pipeline.sources import ALL_INGESTERS
from pipeline.sources.feedbase import FeedIngester, _entry_text, _strip_html

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>UK, France and Germany statement on Iran</title>
    <link href="https://www.gov.uk/government/news/e3-iran"/>
    <updated>2026-07-15T09:30:00Z</updated>
    <summary type="html">&lt;p&gt;The E3 called on Iran to return to full compliance.&lt;/p&gt;</summary>
  </entry>
  <entry>
    <title>Older item</title>
    <link href="https://www.gov.uk/government/news/old"/>
    <updated>2026-01-02T00:00:00Z</updated>
    <summary>Old news.</summary>
  </entry>
</feed>"""

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Baltic energy security statement</title>
    <link>https://vm.ee/et/uudised/energy</link>
    <pubDate>Wed, 16 Jul 2026 12:00:00 +0000</pubDate>
    <description>Estonia, Latvia and Lithuania on energy independence.</description>
  </item>
</channel></rss>"""


class _StubFeed(FeedIngester):
    source_name = "stub_feed"
    source_lang = "en"
    feed_url = "https://example.test/feed"


def test_strip_html():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert _strip_html("") == ""


def test_parse_atom_yields_events():
    events = list(_StubFeed().parse_feed(ATOM))
    assert len(events) == 2
    e = events[0]
    assert e.title == "UK, France and Germany statement on Iran"
    assert e.source_url == "https://www.gov.uk/government/news/e3-iran"
    assert e.date == "2026-07-15"
    assert e.collection_method == "rss"
    assert e.source_lang == "en"
    assert "full compliance" in e.text


def test_parse_rss_yields_events():
    events = list(_StubFeed().parse_feed(RSS))
    assert len(events) == 1
    assert events[0].date == "2026-07-16"
    assert "energy independence" in events[0].text


def test_since_filters_older_entries():
    ing = _StubFeed(since="2026-07-01")
    events = list(ing.parse_feed(ATOM))
    assert [e.date for e in events] == ["2026-07-15"]


def test_empty_feed_yields_nothing():
    assert list(_StubFeed().parse_feed("")) == []
    assert list(_StubFeed().parse_feed("<feed></feed>")) == []


def test_new_sources_registered():
    names = {c.source_name for c in ALL_INGESTERS}
    for expected in (
        "uk_fcdo",
        "us_state",
        "australia_dfat",
        "czech_mfa",
        "slovak_mfa",
        "hungarian_mfa",
        "estonian_mfa",
        "latvian_mfa",
        "lithuanian_mfa",
    ):
        assert expected in names


def test_entry_text_prefers_full_content():
    entry = {
        "content": [{"value": "<p>Full body text here.</p>"}],
        "summary": "short summary",
    }
    assert _entry_text(entry) == "Full body text here."


# --- _download ---------------------------------------------------------------
# Regression coverage for a real incident: feedparser.parse(url) does its own
# network fetch with no timeout of its own, so an unresponsive (not just
# 404ing) server hangs that call indefinitely — and since ingest.py runs
# ingesters sequentially, one hanging source stalls the entire collect run with
# no exception ever raised for run_ingester to catch. _download() must fetch via
# requests (which has an explicit timeout) and hand feedparser only the bytes.


def test_download_uses_bounded_timeout():
    with patch("pipeline.sources.feedbase.requests.get") as mock_get:
        mock_get.return_value.content = b"<feed></feed>"
        mock_get.return_value.raise_for_status.return_value = None
        _StubFeed()._download()
    _, kwargs = mock_get.call_args
    assert kwargs.get("timeout") is not None
    assert kwargs["timeout"] <= 30


def test_download_never_lets_feedparser_fetch_the_url():
    # feedparser.parse must never be called with a bare URL string/None from
    # _download — only with the already-downloaded bytes.
    with patch("pipeline.sources.feedbase.requests.get") as mock_get:
        mock_get.return_value.content = b"<feed></feed>"
        mock_get.return_value.raise_for_status.return_value = None
        result = _StubFeed()._download()
    assert result == b"<feed></feed>"


def test_download_returns_empty_bytes_on_request_failure():
    with patch("pipeline.sources.feedbase.requests.get", side_effect=requests.exceptions.Timeout("boom")):
        result = _StubFeed()._download()
    assert result == b""
