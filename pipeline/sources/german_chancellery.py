from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterator

import time

import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# No stable English RSS endpoint verified for the Bundesregierung newsroom;
# scrape the listing directly (same federal design system as auswaertiges-amt.de).
LISTING_URL = "https://www.bundesregierung.de/breg-en/news"
BASE_URL = "https://www.bundesregierung.de"
SOURCE_NAME = "german_chancellery"

_HEADERS = {"User-Agent": "WeimTracker/1.0 (+https://github.com/weimar-tracker)"}


def _parse_date(raw: str | None) -> tuple[str, str]:
    """Return (date_str 'YYYY-MM-DD', published_at ISO). Falls back to today."""
    if raw:
        raw = raw.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                    "%d.%m.%Y", "%d %B %Y", "%B %d, %Y",
                    "%a, %d %b %Y %H:%M:%S %z"):
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _strict_date(raw: str | None) -> str | None:
    """Parse to 'YYYY-MM-DD' or return None — never falls back to today,
    because a guessed date files an article in the wrong week."""
    if not raw:
        return None
    raw = raw.strip()[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%d %B %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


class GermanChancelleryIngester(BaseIngester):
    """Federal Chancellery / Bundesregierung newsroom — Weimar summits are
    leader-level, and the MFA does not reliably cover what the Chancellor
    announces."""
    source_name = SOURCE_NAME
    source_lang = "en"

    def fetch(self) -> Iterator[Event]:
        page = 1
        while True:
            url = LISTING_URL if page == 1 else f"{LISTING_URL}?page={page}"
            try:
                r = requests.get(url, timeout=15, headers=_HEADERS)
                r.raise_for_status()
            except Exception as exc:
                print(f"[{SOURCE_NAME}] page {page} error: {exc}")
                break
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select(".bpa-teaser, .c-teaser, .news-card, article")
            if not cards:
                break

            all_before_since = True
            for card in cards:
                a_tag = card.find("a", href=True)
                if not a_tag:
                    continue
                title = a_tag.get_text(" ", strip=True)
                if not title:
                    continue
                href = a_tag["href"]
                item_url = href if href.startswith("http") else BASE_URL + href

                body, article_date = self._fetch_body(item_url)
                if article_date:
                    date, published_at = article_date, article_date + "T00:00:00Z"
                else:
                    date_tag = card.find("time") or card.find(
                        attrs={"class": re.compile(r"date|time|meta", re.I)})
                    raw = (date_tag.get("datetime") or date_tag.get_text(strip=True)) if date_tag else None
                    date, published_at = _parse_date(raw)
                time.sleep(0.5)

                if self.since and date >= self.since:
                    all_before_since = False
                elif not self.since:
                    all_before_since = False

                if self.since and date < self.since:
                    continue

                desc_tag = card.find("p")
                summary = body or (desc_tag.get_text(" ", strip=True) if desc_tag else "")
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

    def _fetch_body(self, url: str) -> tuple[str, str | None]:
        """Returns (body_text, date_str_or_None) from an individual article page."""
        try:
            r = requests.get(url, timeout=15, headers=_HEADERS)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            date_str = None
            tag = soup.find("time")
            if tag:
                date_str = _strict_date(tag.get("datetime") or tag.get_text(strip=True))
            if not date_str:
                for attrs in ({"property": "article:published_time"},
                              {"itemprop": "datePublished"}, {"name": "date"}):
                    meta = soup.find("meta", attrs=attrs)
                    if meta and meta.get("content"):
                        date_str = _strict_date(meta["content"])
                        if date_str:
                            break

            article = soup.find(class_=re.compile(r"bpa-richtext|c-article__body|c-richtext|article-content"))
            if not article:
                article = soup.find("article") or soup.find("main")
            if article:
                paragraphs = [p.get_text(" ", strip=True) for p in article.find_all("p")
                              if len(p.get_text(strip=True)) > 40]
                return " ".join(paragraphs), date_str
            return "", date_str
        except Exception:
            pass
        return "", None
