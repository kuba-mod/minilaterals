from __future__ import annotations

import glob
import hashlib
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# No RSS feed; scrape the news listing directly. The French site has fuller
# coverage than the English translation, consistent with polish_pm scraping
# gov.pl's Polish listing rather than a thinner English one.
LISTING_URL = "https://www.elysee.fr/toutes-les-actualites"
BASE_URL = "https://www.elysee.fr"
SOURCE_NAME = "elysee"

# Wayback Machine CDX endpoint, used only in backfill mode. The listing ignores
# ?page= and always returns the same ~100-item batch (see the pagination loop
# below), so HTML pagination alone can't reach further back than that — but
# web.archive.org's crawl history of /emmanuel-macron/* article URLs can, and
# elysee.fr keeps old articles live, so backfill discovers URLs via CDX and
# fetches full text/date from the live site rather than from the snapshot.
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"

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


def _already_ingested(item_url: str, title: str) -> bool:
    """True if a file for this URL+title already exists under data/events/elysee,
    regardless of date/month. Lets daily runs skip the per-article body fetch for
    items already on disk — the listing page returns ~100 items at once (unlike
    the other sources' small page-1 batches), so without this a routine run would
    hit elysee.fr roughly 100 times for content already ingested days ago."""
    content_hash = hashlib.sha256((item_url + title).encode()).hexdigest()[:8]
    return bool(glob.glob(f"data/events/{SOURCE_NAME}/*/*-{content_hash}.yaml"))


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
        seen_urls: set[str] = set()
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
                seen_urls.add(item_url)
                # In daily mode (no --since) already-known items don't need a body
                # fetch at all — --since backfill still walks every item to find
                # the pagination boundary, so this only applies to routine runs.
                if not self.since and _already_ingested(item_url, title):
                    continue

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
                    collection_method="html",
                    source_published_at=date + "T00:00:00Z",
                    date=date,
                )

            if all_before_since or not self.since:
                break
            page += 1

        if self.since:
            yield from self._fetch_wayback_articles(seen_urls)

    def _fetch_wayback_articles(self, seen_urls: set[str]) -> Iterator[Event]:
        """Discover article URLs Wayback crawled under /emmanuel-macron/ since
        `since`, then fetch title/body/date from the live site — elysee.fr keeps
        old articles up, only the listing rolls off. This is the only way to
        backfill past the listing's ~100-item window (see module docstring)."""
        urls: list[str] = []
        for attempt in range(3):
            try:
                r = requests.get(
                    WAYBACK_CDX_URL,
                    params={
                        "url": f"{BASE_URL}/emmanuel-macron/*",
                        "from": self.since.replace("-", ""),
                        "output": "json",
                        "fl": "original",
                        "collapse": "urlkey",
                        "filter": "statuscode:200",
                        "limit": "5000",
                    },
                    timeout=180,
                    headers=_HEADERS,
                )
                r.raise_for_status()
                urls = [row[0] for row in r.json()[1:]]  # row 0 is the CDX header row
                break
            except Exception as exc:
                print(f"[{SOURCE_NAME}] wayback CDX error (attempt {attempt + 1}/3): {exc}")
                time.sleep(15)
        if not urls:
            return

        # Dedupe against events already on disk, not just this run's listing pass —
        # the CDX index and the live listing can each surface URLs the other missed.
        for path in Path("data/events").joinpath(SOURCE_NAME).glob("**/*.yaml"):
            try:
                stored = yaml.safe_load(path.read_text(encoding="utf-8"))
                if stored and stored.get("source_url"):
                    seen_urls.add(stored["source_url"])
            except Exception:
                continue

        # URLs with a date in the path sort for free; process those newest-first
        # so the per-article fetch below (needed to date/title the rest) is spent
        # on the most likely-relevant candidates first.
        candidates = sorted(
            {u.split("?")[0].replace("http://", "https://") for u in urls},
            key=lambda u: _date_from_url(u) or "",
            reverse=True,
        )
        print(f"[{SOURCE_NAME}] wayback: {len(candidates)} captured article URLs since {self.since}")

        dateless = 0
        for url in candidates[:500]:
            if not _ARTICLE_PATH.match(url[len(BASE_URL) :]) or url in seen_urls:
                continue
            seen_urls.add(url)

            result = self._fetch_article(url)
            time.sleep(0.5)
            if not result:
                continue
            title, body, date = result
            if not date:
                # A statement without a recoverable date would land in the wrong
                # week on the timeline; skip it.
                dateless += 1
                continue
            if date < self.since:
                continue

            yield Event(
                source_name=SOURCE_NAME,
                title=title,
                text=body,
                source_url=url,
                source_lang=self.source_lang,
                collection_method="wayback",
                source_published_at=date + "T00:00:00Z",
                date=date,
            )
        if dateless:
            print(f"[{SOURCE_NAME}] wayback: skipped {dateless} articles without a recoverable date")

    def _fetch_article(self, url: str) -> tuple[str, str, str | None] | None:
        """Returns (title, body_text, date_str_or_None) from an individual
        article page, or None on fetch failure. Unlike _fetch_body, also
        extracts the title — needed because wayback-discovered URLs don't come
        with a listing-card title the way the paginated fetch's links do."""
        try:
            r = requests.get(url, timeout=15, headers=_HEADERS)
            r.raise_for_status()
        except Exception:
            return None
        soup = BeautifulSoup(r.text, "lxml")

        title = None
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            title = og["content"].strip()
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(" ", strip=True)
        if not title:
            return None

        date_str = _date_from_url(url)
        if not date_str:
            tag = soup.find("time")
            if tag:
                date_str = _parse_date(tag.get("datetime") or tag.get_text(strip=True))
        if not date_str:
            meta = soup.find("meta", attrs={"property": "article:published_time"})
            if meta:
                date_str = _parse_date(meta.get("content"))

        article = soup.find("article") or soup.find("main")
        body = ""
        if article:
            paragraphs = [
                p.get_text(" ", strip=True) for p in article.find_all("p") if len(p.get_text(strip=True)) > 40
            ]
            body = " ".join(paragraphs)

        return title, body, date_str

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
