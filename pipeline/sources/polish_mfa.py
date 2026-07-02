from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urljoin

import time

import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# No RSS feed; scrape the news listing page directly.
NEWS_URL = "https://www.gov.pl/web/diplomacy/news-"
BASE_URL = "https://www.gov.pl"
SOURCE_NAME = "polish_mfa"

_HEADERS = {"User-Agent": "WeimTracker/1.0 (+https://github.com/weimar-tracker)"}


def _parse_date(raw: str | None) -> tuple[str, str]:
    if not raw:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for fmt in (
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%d %B %Y",
        "%B %d, %Y",
        "%a, %d %b %Y %H:%M:%S %z",
    ):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")



class PolishMFAIngester(BaseIngester):
    source_name = SOURCE_NAME
    source_lang = "en"

    def _fetch_body(self, url: str) -> str:
        """Fetch full article text from an individual news page."""
        try:
            r = requests.get(url, timeout=15, headers=_HEADERS)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            article = soup.find(class_="editor-content") or soup.find("article") or soup.find("main")
            if article:
                paragraphs = [p.get_text(" ", strip=True) for p in article.find_all("p")
                              if len(p.get_text(strip=True)) > 40]
                if paragraphs:
                    return " ".join(paragraphs)
        except Exception:
            pass
        return ""

    def fetch(self) -> Iterator[Event]:
        # gov.pl paginates with ?page=N (1-indexed); base URL is page 1
        page = 1
        consecutive_empty = 0
        while True:
            page_url = NEWS_URL if page == 1 else f"{NEWS_URL}?page={page}"
            try:
                r = requests.get(page_url, timeout=15, headers=_HEADERS)
                r.raise_for_status()
            except Exception as exc:
                print(f"[{SOURCE_NAME}] page {page} error: {exc}")
                break
            soup = BeautifulSoup(r.text, "lxml")

            items = soup.select("article li")
            if not items:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
                page += 1
                continue
            consecutive_empty = 0

            all_before_since = True
            for item in items:
                a_tag = item.select_one(".title a") or item.find("a", href=True)
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                if not title:
                    continue
                href = a_tag["href"]
                url = href if href.startswith("http") else urljoin(BASE_URL, href)

                date_tag = item.select_one(".date") or item.find("time")
                raw_date = (date_tag.get("datetime") or date_tag.get_text(strip=True)) if date_tag else None
                date, published_at = _parse_date(raw_date)

                if self.since and date >= self.since:
                    all_before_since = False
                elif not self.since:
                    all_before_since = False

                if self.since and date < self.since:
                    continue

                intro_tag = item.select_one(".intro") or item.find("p")
                intro = intro_tag.get_text(" ", strip=True) if intro_tag else ""
                body = self._fetch_body(url)
                summary = body or intro
                time.sleep(0.5)

                yield Event(
                    source_name=SOURCE_NAME,
                    title=title,
                    text=summary,
                    source_url=url,
                    source_lang=self.source_lang,
                    source_published_at=published_at,
                    date=date,
                ).classify()

            if all_before_since or not self.since:
                break
            page += 1
