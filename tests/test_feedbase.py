"""Feed parsing for the RSS/Atom-based minilateral ingesters (feedbase.py)."""

from __future__ import annotations

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
