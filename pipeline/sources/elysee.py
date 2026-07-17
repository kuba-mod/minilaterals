from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# No RSS feed; scrape the news listing directly. The French site has fuller
# coverage than the English translation, consistent with polish_pm scraping
# gov.pl's Polish listing rather than a thinner English one.
LISTING_URL = "https://www.elysee.fr/toutes-les-actualites"
BASE_URL = "https://www.elysee.fr"
SOURCE_NAME = "elysee"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; minilaterals.com Weimar Triangle tracker; +https://minilaterals.com/weimar-triangle)"
}

# Élysée article URLs are under /emmanuel-macron/... — some embed the
# publication date (/emmanuel-macron/2026/07/13/slug), others don't
# (/emmanuel-macron/sommet-du-g7-devian-2026), so the date isn't a reliable
# filter for "is this an article link" — only the path prefix is.
_ARTICLE_PATH = re.compile(r"^/emmanuel-macron/.+")
_URL_DATE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")


def _date_from_url(url: str) -> str | None:
    m = _URL_DATE.search(url)
    if not m:
        return None
    date = "-".join(m.groups())
    if "1995-01-01" < date <= datetime.now(UTC).strftime("%Y-%m-%d"):
        return date
    return None


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


class ElyseeIngester(BaseIngester):
    """Présidence de la République — Weimar summits are leader-level, and the
    Quai d'Orsay does not reliably cover what the President announces."""

    source_name = SOURCE_NAME
    source_lang = "fr"

    def fetch(self) -> Iterator[Event]:
        page = 1
        prev_urls: frozenset[str] = frozenset()
        while True:
            url = LISTING_URL if page == 1 else f"{LISTING_URL}?page={page}"
            try:
                r = requests.get(url, timeout=15, headers=_HEADERS)
                r.raise_for_status()
            except Exception as exc:
                print(f"[{SOURCE_NAME}] page {page} error: {exc}")
                break
            soup = BeautifulSoup(r.text, "lxml")

            # Collect teaser links; the listing markup has changed before, so fall
            # back from <article> teasers to any dated article link on the page.
            links: list[tuple[str, str]] = []
            seen: set[str] = set()
            cards = soup.select("article") or [soup]
            for card in cards:
                for a_tag in card.find_all("a", href=True):
                    href = a_tag["href"].split("?")[0]
                    item_url = href if href.startswith("http") else BASE_URL + href
                    if not item_url.startswith(BASE_URL) or not _ARTICLE_PATH.match(item_url[len(BASE_URL) :]):
                        continue
                    # Listing cards concatenate a date badge + category badge + title
                    # into one <a>; the title is the last <span>, others are dropped.
                    spans = a_tag.find_all("span", recursive=False)
                    if spans:
                        title = spans[-1].get_text(" ", strip=True)
                    else:
                        title = a_tag.get_text(" ", strip=True)
                    if not title or item_url in seen:
                        continue
                    seen.add(item_url)
                    links.append((title, item_url))

            if not links:
                break

            # The site ignores ?page= on this listing and always returns the
            # same recent batch, so a repeated batch means we've hit the end
            # of what's paginatable — stop instead of looping forever.
            if seen == prev_urls:
                break
            prev_urls = frozenset(seen)

            all_before_since = True
            for title, item_url in links:
                date = _date_from_url(item_url)
                body, article_date = self._fetch_body(item_url)
                date = date or article_date
                time.sleep(0.5)
                if not date:
                    # A statement without a recoverable date would land in the
                    # wrong week on the timeline; skip it.
                    continue

                if self.since and date >= self.since:
                    all_before_since = False
                elif not self.since:
                    all_before_since = False

                if self.since and date < self.since:
                    continue

                yield Event(
                    source_name=SOURCE_NAME,
                    title=title,
                    text=body,
                    source_url=item_url,
                    source_lang=self.source_lang,
                    source_published_at=date + "T00:00:00Z",
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
                date_str = _parse_date(tag.get("datetime") or tag.get_text(strip=True))
            if not date_str:
                meta = soup.find("meta", attrs={"property": "article:published_time"})
                if meta:
                    date_str = _parse_date(meta.get("content"))

            article = soup.find("article") or soup.find("main")
            if article:
                paragraphs = [
                    p.get_text(" ", strip=True) for p in article.find_all("p") if len(p.get_text(strip=True)) > 40
                ]
                return " ".join(paragraphs), date_str
            return "", date_str
        except Exception:
            pass
        return "", None
