from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# SPIP RSS endpoint confirmed unreachable May 2026; scrape official statements directly.
LISTING_URL = "https://www.diplomatie.gouv.fr/en/press/press-room/official-statements-and-speeches"
BASE_URL = "https://www.diplomatie.gouv.fr"
SOURCE_NAME = "france_diplomatie"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WeimTracker/1.0)"}


def _parse_date(raw: str | None) -> tuple[str, str]:
    if not raw:
        now = datetime.now(UTC)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Clean up ordinals, extra text, and punctuation (e.g. "On : May 13th 2026" → "May 13 2026")
    cleaned = raw.strip()
    cleaned = re.sub(r"^On\s*:\s*", "", cleaned)  # Remove "On : " prefix
    cleaned = re.sub(r"(\d{1,2})(?:st|nd|rd|th)", r"\1", cleaned)  # Remove ordinals
    cleaned = re.sub(r",", "", cleaned)  # Remove commas

    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d %B %Y",
        "%B %d %Y",  # "May 13 2026" (after ordinal cleanup)
        "%d %b %Y",  # "13 May 2026" or "13 May 2026" (3-letter month)
    ):
        try:
            dt = datetime.strptime(cleaned.strip(), fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


class FranceDiplomatieIngester(BaseIngester):
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
            items = soup.select("article")
            if not items:
                break

            any_in_window = False
            for item in items:
                a_tag = item.find("a", href=True)
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                if not title:
                    continue
                href = a_tag["href"]
                item_url = href if href.startswith("http") else BASE_URL + href

                date_tag = item.find("time") or item.find(class_=lambda c: c and "date" in c)
                raw_date = (date_tag.get("datetime") or date_tag.get_text(strip=True)) if date_tag else None
                date, published_at = _parse_date(raw_date)

                summary, article_date = self._fetch_body(item_url)
                # Prefer article-page date when listing page didn't have one
                if article_date:
                    date, published_at = article_date, article_date + "T00:00:00Z"

                time.sleep(1)

                if self.since and date < self.since:
                    continue

                any_in_window = True
                yield Event(
                    source_name=SOURCE_NAME,
                    title=title,
                    text=summary,
                    source_url=item_url,
                    source_lang=self.source_lang,
                    source_published_at=published_at,
                    date=date,
                ).classify()

            # In backfill mode: stop only when an entire page had no items on or after `since`.
            # In normal mode: fetch only the first page.
            if not self.since or not any_in_window:
                break
            page += 1

    def _fetch_body(self, url: str) -> tuple[str, str | None]:
        """Returns (body_text, date_str_or_None). Date extracted from article page if available."""
        try:
            r = requests.get(url, timeout=15, headers=_HEADERS)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            # Try to extract the publication date from the article page
            date_str = None
            # Look for date in: <time>, class="*date*", itemprop="datePublished", or diplomatie--hdp-date divs
            date_tag = (
                soup.find("time")
                or soup.find(class_=lambda c: c and ("date" in c.lower() if c else False))
                or soup.find(attrs={"itemprop": "datePublished"})
            )
            if date_tag:
                raw = date_tag.get("datetime") or date_tag.get_text(strip=True)
                if raw:
                    date_str, _ = _parse_date(raw)

            article = soup.find("article")
            if article:
                paragraphs = [
                    p.get_text(" ", strip=True) for p in article.find_all("p") if len(p.get_text(strip=True)) > 40
                ]
                return " ".join(paragraphs), date_str
        except Exception:
            pass
        return "", None
