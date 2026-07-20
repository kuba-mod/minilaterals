from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# gov.pl serves every ministry's news listing with the same DOM, so one scraper
# covers the MFA, the PM's chancellery, and any future gov.pl ministry —
# subclasses only set source_name and news_url.
BASE_URL = "https://www.gov.pl"

_HEADERS = {"User-Agent": "minilaterals.com Weimar Triangle tracker (+https://minilaterals.com/weimar-triangle)"}


def _parse_date(raw: str | None) -> tuple[str, str]:
    if not raw:
        now = datetime.now(UTC)
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
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


class GovPlIngester(BaseIngester):
    source_name = ""
    source_lang = "en"
    news_url = ""  # full listing URL, e.g. https://www.gov.pl/web/diplomacy/news-

    def _fetch_body(self, url: str) -> str:
        """Fetch full article text from an individual news page."""
        try:
            r = requests.get(url, timeout=15, headers=_HEADERS)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            article = soup.find(class_="editor-content") or soup.find("article") or soup.find("main")
            if article:
                paragraphs = [
                    p.get_text(" ", strip=True) for p in article.find_all("p") if len(p.get_text(strip=True)) > 40
                ]
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
            page_url = self.news_url if page == 1 else f"{self.news_url}?page={page}"
            try:
                r = requests.get(page_url, timeout=15, headers=_HEADERS)
                r.raise_for_status()
            except Exception as exc:
                print(f"[{self.source_name}] page {page} error: {exc}")
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
                    source_name=self.source_name,
                    title=title,
                    text=summary,
                    source_url=url,
                    source_lang=self.source_lang,
                    collection_method="html",
                    source_published_at=published_at,
                    date=date,
                )

            if all_before_since or not self.since:
                break
            page += 1
