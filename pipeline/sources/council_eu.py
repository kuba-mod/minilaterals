from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urljoin, urlencode

import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# RSS endpoint blocked by Cloudflare; scrape the press release listing instead.
LISTING_URL = "https://www.consilium.europa.eu/en/press/press-releases/"
BASE_URL = "https://www.consilium.europa.eu"
SOURCE_NAME = "council_eu"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def _parse_date(raw: str | None) -> tuple[str, str]:
    if not raw:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for fmt in (
        "%d/%m/%Y",
        "%d %B %Y",
        "%Y-%m-%d",
        "%B %d, %Y",
    ):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


class CouncilEUIngester(BaseIngester):
    source_name = SOURCE_NAME
    source_lang = "en"

    def fetch(self) -> Iterator[Event]:
        try:
            r = requests.get(LISTING_URL, timeout=20, headers=_HEADERS)
            if r.status_code == 403:
                print(f"[{SOURCE_NAME}] 403 Forbidden — Cloudflare may be blocking; skipping")
                return
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            # Council uses article cards; selectors are a best guess and may need updating
            items = soup.select(
                "article, .press-release-item, .listing__item, "
                ".content-block, .item-press-release"
            )
            if not items:
                # broad fallback: any element with a date and a link
                items = [
                    el for el in soup.select("li, div.item")
                    if el.find("a", href=True) and el.find("time")
                ]

            if not items:
                print(f"[{SOURCE_NAME}] No items found — selector may need updating")
                return

            for item in items[:30]:
                a_tag = item.find("a", href=True)
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                if not title:
                    continue
                href = a_tag["href"]
                url = href if href.startswith("http") else urljoin(BASE_URL, href)

                time_tag = item.find("time")
                if time_tag:
                    raw_date = time_tag.get("datetime") or time_tag.get_text(strip=True)
                else:
                    date_tag = item.find(class_=lambda c: c and "date" in c.lower())
                    raw_date = date_tag.get_text(strip=True) if date_tag else None
                date, published_at = _parse_date(raw_date)

                desc_tag = item.find("p")
                summary = desc_tag.get_text(" ", strip=True) if desc_tag else ""

                time.sleep(2)  # be polite to the Council server

                yield Event(
                    source_name=SOURCE_NAME,
                    title=title,
                    text=summary,
                    source_url=url,
                    source_lang=self.source_lang,
                    source_published_at=published_at,
                    date=date,
                ).classify()

        except Exception as exc:
            print(f"[{SOURCE_NAME}] Error: {exc}")
