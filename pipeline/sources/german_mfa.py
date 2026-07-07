from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# Verified working May 2026 — previous URL at /SiteGlobals/Functions/RSSFeed/ is stale
RSS_URL = "https://www.auswaertiges-amt.de/static/includes/rss_en/RSS_Pressemitteilungen_Reden.xml"
FALLBACK_URL = "https://www.auswaertiges-amt.de/en/newsroom/news"
BASE_URL = "https://www.auswaertiges-amt.de"
SOURCE_NAME = "german_mfa"

# Wayback Machine endpoints, used only in backfill mode. The newsroom listing is
# JS-rendered so HTML pagination yields nothing, and the live RSS feed only holds
# the newest ~20 items — but web.archive.org snapshots the feed regularly, so
# replaying snapshots taken during a collection gap recovers the items that had
# already rolled out of the live feed.
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"

_HEADERS = {"User-Agent": "WeimTracker/1.0 (+https://github.com/weimar-tracker)"}


def _parse_date(raw: str | None) -> tuple[str, str]:
    """Return (date_str 'YYYY-MM-DD', published_at 'YYYY-MM-DDTHH:MM:SSZ')."""
    if not raw:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                "%d.%m.%Y", "%B %d, %Y"):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


class GermanMFAIngester(BaseIngester):
    source_name = SOURCE_NAME
    source_lang = "en"

    def fetch(self) -> Iterator[Event]:
        if self.since:
            # Backfill mode: paginate the HTML listing, supplement with RSS,
            # then replay Wayback snapshots of the feed for anything older than
            # the live feed window.
            seen_urls: set[str] = set()
            for event in self._fetch_html_paginated():
                seen_urls.add(event.source_url)
                yield event
            for event in self._fetch_rss():
                if event.source_url not in seen_urls:
                    seen_urls.add(event.source_url)
                    yield event
            for event in self._fetch_wayback_rss(seen_urls):
                yield event
            for event in self._fetch_wayback_articles(seen_urls):
                yield event
        else:
            items = list(self._fetch_rss())
            if items:
                yield from items
            else:
                yield from self._fetch_html_paginated()

    def _fetch_body(self, url: str) -> str:
        """Fetch full article text from an individual press release page."""
        try:
            r = requests.get(url, timeout=15, headers=_HEADERS)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            # Main article content is in .c-article__body or .c-richtext
            article = soup.find(class_=re.compile(r"c-article__body|c-richtext|article-content"))
            if not article:
                article = soup.find("article") or soup.find("main")
            if article:
                paragraphs = [p.get_text(" ", strip=True) for p in article.find_all("p")
                              if len(p.get_text(strip=True)) > 40]
                if paragraphs:
                    return " ".join(paragraphs)
        except Exception:
            pass
        return ""

    def _fetch_rss(self) -> Iterator[Event]:
        try:
            feed = feedparser.parse(RSS_URL, request_headers=_HEADERS)
            if not feed.entries:
                print(f"[{SOURCE_NAME}] RSS empty, falling back to HTML")
                return
            for entry in feed.entries:
                date, published_at = _parse_date(entry.get("published") or entry.get("updated"))
                url = entry.get("link", "")
                if not url.startswith("http"):
                    url = BASE_URL + url
                # Prefer full article body over RSS snippet
                body = self._fetch_body(url)
                summary = body or BeautifulSoup(entry.get("summary", ""), "lxml").get_text(" ", strip=True)
                time.sleep(0.5)
                yield Event(
                    source_name=SOURCE_NAME,
                    title=entry.get("title", "").strip(),
                    text=summary,
                    source_url=url,
                    source_lang=self.source_lang,
                    source_published_at=published_at,
                    date=date,
                ).classify()
        except Exception as exc:
            print(f"[{SOURCE_NAME}] RSS error: {exc}")

    def _fetch_wayback_rss(self, seen_urls: set[str]) -> Iterator[Event]:
        """Yield events from Wayback Machine snapshots of the RSS feed taken on or
        after `since`. Article bodies are fetched from the live site (the feed
        entry links point at auswaertiges-amt.de, which keeps old articles up —
        only the listing/feed window moves)."""
        try:
            r = requests.get(WAYBACK_CDX_URL, params={
                "url": RSS_URL,
                "from": self.since.replace("-", ""),
                "output": "json",
                "fl": "timestamp",
                "filter": "statuscode:200",
                "collapse": "timestamp:8",   # at most one snapshot per day
            }, timeout=30, headers=_HEADERS)
            r.raise_for_status()
            rows = r.json()
        except Exception as exc:
            print(f"[{SOURCE_NAME}] wayback CDX error: {exc}")
            return
        timestamps = [row[0] for row in rows[1:]]  # row 0 is the header
        print(f"[{SOURCE_NAME}] wayback: {len(timestamps)} feed snapshots since {self.since}")

        for ts in timestamps:
            # id_ returns the original feed bytes rather than the rewritten page
            snap_url = f"https://web.archive.org/web/{ts}id_/{RSS_URL}"
            try:
                r = requests.get(snap_url, timeout=30, headers=_HEADERS)
                r.raise_for_status()
                feed = feedparser.parse(r.content)
            except Exception as exc:
                print(f"[{SOURCE_NAME}] wayback snapshot {ts} error: {exc}")
                continue
            time.sleep(1)

            for entry in feed.entries:
                url = entry.get("link", "")
                if not url.startswith("http"):
                    url = BASE_URL + url
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                title = entry.get("title", "").strip()
                date, published_at = _parse_date(entry.get("published") or entry.get("updated"))
                if date < self.since:
                    continue
                # Skip the body fetch for events already on disk — every snapshot
                # overlaps heavily with the previous one and with prior runs.
                probe = Event(
                    source_name=SOURCE_NAME, title=title, text="", source_url=url,
                    source_lang=self.source_lang, source_published_at=published_at,
                    date=date,
                )
                if probe.output_path().exists():
                    continue
                body = self._fetch_body(url)
                summary = body or BeautifulSoup(entry.get("summary", ""), "lxml").get_text(" ", strip=True)
                time.sleep(0.5)
                probe.text = summary
                yield probe.classify()

    def _fetch_wayback_articles(self, seen_urls: set[str]) -> Iterator[Event]:
        """Discover article URLs the Wayback Machine captured under the newsroom
        path since `since`, and ingest them from the live site. Complements the
        feed-snapshot replay: individual articles get crawled (e.g. via links
        from other sites) even when the feed URL itself is never captured."""
        try:
            r = requests.get(WAYBACK_CDX_URL, params={
                "url": f"{BASE_URL}/en/newsroom/news/*",
                "from": self.since.replace("-", ""),
                "output": "json",
                "fl": "original",
                "collapse": "urlkey",
                "limit": "1000",
            }, timeout=30, headers=_HEADERS)
            r.raise_for_status()
            rows = r.json()
        except Exception as exc:
            print(f"[{SOURCE_NAME}] wayback article CDX error: {exc}")
            return
        urls = [row[0] for row in rows[1:]]  # row 0 is the header
        print(f"[{SOURCE_NAME}] wayback: {len(urls)} captured article URLs since {self.since}")

        # Dedupe by URL against events already on disk: the page <h1> can differ
        # from the RSS title of the same item, which would defeat the
        # filename-hash dedup and store the event twice.
        for path in Path("data/events").joinpath(SOURCE_NAME).glob("**/*.yaml"):
            try:
                stored = yaml.safe_load(path.read_text(encoding="utf-8"))
                if stored and stored.get("source_url"):
                    seen_urls.add(stored["source_url"])
            except Exception:
                continue

        for url in urls:
            url = url.split("?")[0].replace("http://", "https://")
            if url in seen_urls or url.rstrip("/") == f"{BASE_URL}/en/newsroom/news":
                continue
            seen_urls.add(url)
            try:
                r = requests.get(url, timeout=15, headers=_HEADERS)
                r.raise_for_status()
            except Exception:
                continue
            time.sleep(0.5)
            soup = BeautifulSoup(r.text, "lxml")

            date_tag = soup.find("time")
            raw_date = (date_tag.get("datetime") or date_tag.get_text(strip=True)) if date_tag else None
            if not raw_date:
                meta = soup.find("meta", attrs={"name": "date"}) or soup.find(attrs={"itemprop": "datePublished"})
                raw_date = meta.get("content") or meta.get("datetime") if meta else None
            if not raw_date:
                # A press release without a recoverable date is worse than a skip —
                # it would land in the wrong week on the timeline.
                print(f"[{SOURCE_NAME}] wayback article without date, skipping: {url}")
                continue
            date, published_at = _parse_date(raw_date)
            if date < self.since:
                continue

            title_tag = soup.find("h1") or soup.find("title")
            title = title_tag.get_text(" ", strip=True) if title_tag else ""
            if not title:
                continue

            probe = Event(
                source_name=SOURCE_NAME, title=title, text="", source_url=url,
                source_lang=self.source_lang, source_published_at=published_at,
                date=date,
            )
            if probe.output_path().exists():
                continue
            article = soup.find(class_=re.compile(r"c-article__body|c-richtext|article-content"))
            if not article:
                article = soup.find("article") or soup.find("main")
            paragraphs = [p.get_text(" ", strip=True) for p in article.find_all("p")
                          if len(p.get_text(strip=True)) > 40] if article else []
            probe.text = " ".join(paragraphs)
            yield probe.classify()

    def _fetch_html_paginated(self) -> Iterator[Event]:
        # The news listing may be JS-rendered; pagination is attempted but may yield
        # no results on pages beyond the first if the site requires JavaScript.
        page = 1
        while True:
            url = FALLBACK_URL if page == 1 else f"{FALLBACK_URL}?page={page}"
            try:
                r = requests.get(url, timeout=15, headers=_HEADERS)
                r.raise_for_status()
            except Exception as exc:
                print(f"[{SOURCE_NAME}] HTML page {page} error: {exc}")
                break
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select(".news-card, .c-teaser, article")
            if not cards:
                break

            all_before_since = True
            for card in cards:
                a_tag = card.find("a", href=True)
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                if not title:
                    continue
                href = a_tag["href"]
                item_url = href if href.startswith("http") else BASE_URL + href
                date_tag = card.find(attrs={"class": re.compile(r"date|time|meta", re.I)})
                date, published_at = _parse_date(date_tag.get_text(strip=True) if date_tag else None)

                if self.since and date >= self.since:
                    all_before_since = False
                elif not self.since:
                    all_before_since = False

                if self.since and date < self.since:
                    continue

                body = self._fetch_body(item_url)
                desc_tag = card.find("p")
                summary = body or (desc_tag.get_text(" ", strip=True) if desc_tag else "")
                time.sleep(0.5)
                yield Event(
                    source_name=SOURCE_NAME,
                    title=title,
                    text=summary,
                    source_url=item_url,
                    source_lang=self.source_lang,
                    source_published_at=published_at,
                    date=date,
                ).classify()

            if all_before_since or not self.since:
                break
            page += 1
