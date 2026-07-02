from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Iterator

import feedparser
import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# Verified working May 2026 — previous URL at /SiteGlobals/Functions/RSSFeed/ is stale
RSS_URL = "https://www.auswaertiges-amt.de/static/includes/rss_en/RSS_Pressemitteilungen_Reden.xml"
FALLBACK_URL = "https://www.auswaertiges-amt.de/en/newsroom/news"
BASE_URL = "https://www.auswaertiges-amt.de"
SOURCE_NAME = "german_mfa"

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
            # Backfill mode: paginate the HTML listing then supplement with RSS
            seen_urls: set[str] = set()
            for event in self._fetch_html_paginated():
                seen_urls.add(event.source_url)
                yield event
            for event in self._fetch_rss():
                if event.source_url not in seen_urls:
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
