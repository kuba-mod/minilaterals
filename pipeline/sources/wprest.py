from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseIngester, Event

# Generic ingester for .gov sites built on WordPress whose REST API
# (wp-json/wp/v2/<post-type>) is reachable even when the site's RSS feed
# discovery is broken. See us_state.py: state.gov's sitewide /feed/ endpoint
# 200s with a valid-but-empty <channel> (a stale "Custom Report Excerpts"
# feed, unconnected to the press-releases custom post type since a CMS
# migration), while the same content is served correctly through the REST
# API. A collection endpoint returns full post content in the same response
# as the listing, so — like FeedIngester — there's no separate per-article
# fetch to gate on already_ingested(). Subclasses set source_name,
# source_lang, and rest_url (the wp-json collection endpoint for the
# relevant post type, e.g. https://www.state.gov/wp-json/wp/v2/state_press_release
# — found via GET .../wp-json/wp/v2/types, whose entries carry a rest_base).

_HEADERS = {"User-Agent": "minilaterals.com diplomatic tracker (+https://minilaterals.com)"}

_MAX_TEXT = 5000
_PER_PAGE = 20


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def _parse_gmt(raw: str) -> tuple[str, str]:
    """(date 'YYYY-MM-DD', ISO datetime) from a WP REST `date_gmt` value, e.g.
    "2026-08-10T01:25:55" — already UTC, unlike the site-local `date` field."""
    if raw:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


class WPRestIngester(BaseIngester):
    source_name = ""
    source_lang = "en"
    rest_url = ""
    collection_method = "api"

    def fetch(self) -> Iterator[Event]:
        page = 1
        while True:
            items = self._fetch_page(page)
            if items is None:
                return
            if not items:
                if page == 1:
                    print(f"[{self.source_name}] REST endpoint yielded no entries ({self.rest_url})")
                return

            all_before_since = True
            for item in items:
                title = _strip_html((item.get("title") or {}).get("rendered", "")).strip()
                url = item.get("link") or ""
                if not title or not url:
                    continue
                date, published_at = _parse_gmt(item.get("date_gmt") or "")
                if self.since:
                    if date < self.since:
                        continue
                    all_before_since = False
                else:
                    all_before_since = False

                text = _strip_html((item.get("content") or {}).get("rendered", ""))[:_MAX_TEXT]
                yield Event(
                    source_name=self.source_name,
                    title=title,
                    text=text,
                    source_url=url,
                    source_lang=self.source_lang,
                    collection_method=self.collection_method,
                    source_published_at=published_at,
                    date=date,
                )

            # Daily mode only needs the newest page (items are date-descending);
            # backfill keeps paging until a whole page predates `since`.
            if all_before_since or not self.since:
                return
            page += 1

    def _fetch_page(self, page: int) -> list[dict] | None:
        try:
            r = requests.get(
                self.rest_url,
                headers=_HEADERS,
                params={"per_page": _PER_PAGE, "page": page},
                timeout=15,
            )
            # WP core answers a page past the last one with 400
            # rest_post_invalid_page_number — the normal end of pagination, not
            # a fetch failure.
            if r.status_code == 400:
                return []
            r.raise_for_status()
        except requests.exceptions.RequestException as exc:
            print(f"[{self.source_name}] REST error: {exc}")
            return None
        try:
            items = r.json()
        except ValueError:
            print(f"[{self.source_name}] REST response was not JSON ({self.rest_url})")
            return None
        return items if isinstance(items, list) else None
