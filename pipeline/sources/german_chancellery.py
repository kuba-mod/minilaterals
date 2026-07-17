from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# No stable RSS endpoint verified for the Bundesregierung newsroom; scrape the
# listing directly (same federal design system as auswaertiges-amt.de). The
# German listing has fuller coverage than the English translation, consistent
# with polish_pm scraping gov.pl's Polish listing rather than a thinner
# English one. The listing page has no server-rendered teaser markup —
# results are embedded as a JSON blob (`BPA.initialSearchResultsJson`) that
# the frontend hydrates client-side, so we parse that blob instead of
# guessing teaser CSS classes.
LISTING_URL = "https://www.bundesregierung.de/breg-de/aktuelles"
BASE_URL = "https://www.bundesregierung.de"
SOURCE_NAME = "german_chancellery"

_RESULTS_JSON = re.compile(r"BPA\.initialSearchResultsJson\s*=\s*(\{.*?\});\s*\n", re.S)

_HEADERS = {"User-Agent": "minilaterals.com Weimar Triangle tracker (+https://minilaterals.com/weimar-triangle)"}


def _parse_date(raw: str | None) -> tuple[str, str]:
    """Return (date_str 'YYYY-MM-DD', published_at ISO). Falls back to today."""
    if raw:
        raw = raw.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
            "%d.%m.%Y",
            "%d %B %Y",
            "%B %d, %Y",
            "%a, %d %b %Y %H:%M:%S %z",
        ):
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
    now = datetime.now(UTC)
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
    source_lang = "de"

    def fetch(self) -> Iterator[Event]:
        page = 1
        while True:
            # The search endpoint's ?page= param is 0-indexed from the *second*
            # page: no param = page 1, ?page=1 = page 2, ?page=2 = page 3, etc.
            url = LISTING_URL if page == 1 else f"{LISTING_URL}?page={page - 1}"
            try:
                r = requests.get(url, timeout=15, headers=_HEADERS)
                r.raise_for_status()
            except Exception as exc:
                print(f"[{SOURCE_NAME}] page {page} error: {exc}")
                break

            m = _RESULTS_JSON.search(r.text)
            if not m:
                break
            try:
                items = json.loads(m.group(1))["result"]["items"]
            except (json.JSONDecodeError, KeyError):
                break
            if not items:
                break

            all_before_since = True
            for item in items:
                teaser = BeautifulSoup(item.get("payload", ""), "lxml")
                a_tag = teaser.find("a", href=True)
                if not a_tag:
                    continue
                href = a_tag["href"]
                item_url = href if href.startswith("http") else BASE_URL + href
                title = teaser.find(["h2", "h3"])
                title = title.get_text(" ", strip=True) if title else a_tag.get_text(" ", strip=True)
                if not title:
                    continue

                body, article_date = self._fetch_body(item_url)
                date, published_at = _parse_date(article_date or item.get("sortDate"))
                time.sleep(0.5)

                if self.since and date >= self.since:
                    all_before_since = False
                elif not self.since:
                    all_before_since = False

                if self.since and date < self.since:
                    continue

                summary = body or teaser.get_text(" ", strip=True)
                yield Event(
                    source_name=SOURCE_NAME,
                    title=title,
                    text=summary,
                    source_url=item_url,
                    source_lang=self.source_lang,
                    collection_method="html",
                    source_published_at=published_at,
                    date=date,
                )

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
                for attrs in ({"property": "article:published_time"}, {"itemprop": "datePublished"}, {"name": "date"}):
                    meta = soup.find("meta", attrs=attrs)
                    if meta and meta.get("content"):
                        date_str = _strict_date(meta["content"])
                        if date_str:
                            break

            # Scope to <main> first — the cookie-consent dialog also carries a
            # "bpa-richtext" class and sits outside <main>, so searching the
            # whole document picks up banner text instead of the article body.
            container = soup.find("main") or soup.find("article") or soup
            article = container.find(class_=re.compile(r"bpa-richtext|c-article__body|c-richtext|article-content"))
            if not article:
                article = container
            if article:
                paragraphs = [
                    p.get_text(" ", strip=True) for p in article.find_all("p") if len(p.get_text(strip=True)) > 40
                ]
                return " ".join(paragraphs), date_str
            return "", date_str
        except Exception:
            pass
        return "", None
