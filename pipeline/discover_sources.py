#!/usr/bin/env python3
"""
Source discovery tool — run this locally to find actual RSS URLs and
page structure for each MFA site.

Usage:
    python pipeline/discover_sources.py

It will:
  1. Try each known/guessed RSS URL and report which ones return valid feeds
  2. Fetch the press release listing page and extract RSS autodiscovery links
  3. Print enough of the HTML structure to fix the CSS selectors in the scrapers
"""
from __future__ import annotations

import re

import feedparser
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

SOURCES = [
    {
        "name": "france_diplomatie",
        "listing_url": (
            "https://www.diplomatie.gouv.fr/en/french-foreign-policy/"
            "press-releases-speeches-and-editorial/press-releases/"
        ),
        "rss_candidates": [
            "https://www.diplomatie.gouv.fr/en/french-foreign-policy/press-releases-speeches-and-editorial/press-releases/?RSS",
            "https://www.diplomatie.gouv.fr/spip.php?page=backend",
            "https://www.diplomatie.gouv.fr/en/rss.xml",
            "https://www.diplomatie.gouv.fr/feed/",
        ],
    },
    {
        "name": "german_mfa",
        "listing_url": "https://www.auswaertiges-amt.de/en/newsroom",
        "rss_candidates": [
            "https://www.auswaertiges-amt.de/SiteGlobals/Functions/RSSFeed/EN/RSSNewsfeed_Nachrichten.xml",
            "https://www.auswaertiges-amt.de/SiteGlobals/Functions/RSSFeed/DE/RSSNewsfeed_Nachrichten.xml",
            "https://www.auswaertiges-amt.de/en/newsroom/-/rss",
            "https://www.auswaertiges-amt.de/rss",
        ],
    },
    {
        "name": "polish_mfa",
        "listing_url": "https://www.gov.pl/web/diplomacy/press-service",
        "rss_candidates": [
            "https://www.gov.pl/web/diplomacy/rss",
            "https://www.gov.pl/web/diplomacy/rss.xml",
            "https://www.gov.pl/api/feed/press-releases",
        ],
    },
]


def section(title: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def try_rss(url: str) -> tuple[bool, int, str]:
    """Returns (ok, entry_count, error_msg)."""
    try:
        feed = feedparser.parse(url)
        n = len(feed.entries)
        if n > 0:
            return True, n, ""
        # feedparser doesn't raise on 404; check bozo flag
        if feed.get("bozo"):
            return False, 0, str(feed.get("bozo_exception", "bozo flag set"))
        status = feed.get("status", "?")
        return False, 0, f"HTTP {status}, 0 entries"
    except Exception as e:
        return False, 0, str(e)


def fetch_page(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")
    except Exception as e:
        print(f"  ✗ Could not fetch {url}: {e}")
        return None


def find_rss_autodiscovery(soup: BeautifulSoup) -> list[str]:
    links = soup.find_all("link", attrs={"type": re.compile(r"rss|atom|xml", re.I)})
    hrefs = [link.get("href", "") for link in links if link.get("href")]
    return hrefs


def print_article_structure(soup: BeautifulSoup, max_items: int = 3) -> None:
    """Print the first few article-like elements with their class names."""
    candidates = soup.select("article, .article, li.item, .news-item, .press-item, .c-teaser")
    if not candidates:
        # Broader fallback: any element with 'article' or 'press' in class names
        candidates = [
            el for el in soup.find_all(True)
            if any(re.search(r"article|press|news|release|item", c, re.I)
                   for c in el.get("class", []))
        ][:10]

    if not candidates:
        print("  No article-like elements found — page may be JS-rendered.")
        print("  Top-level structure:")
        for child in list(soup.body.children)[:5] if soup.body else []:
            if hasattr(child, "name") and child.name:
                classes = " ".join(child.get("class", []))
                print(f"    <{child.name} class='{classes}'>")
        return

    print(f"  Found {len(candidates)} candidate elements. First {max_items}:\n")
    for el in candidates[:max_items]:
        tag = el.name
        classes = " ".join(el.get("class", []))
        a = el.find("a", href=True)
        href = a["href"] if a else "—"
        link_text = a.get_text(strip=True)[:60] if a else "—"
        date_el = el.find(attrs={"class": re.compile(r"date|time|published", re.I)})
        date_text = date_el.get_text(strip=True) if date_el else "—"
        print(f"  <{tag} class='{classes}'>")
        print(f"    link : {href}")
        print(f"    text : {link_text}")
        print(f"    date : {date_text}")
        print()


def main() -> None:
    print("Weimar tracker — source discovery")
    print("This will take a minute; fetching live pages...\n")

    working_feeds = {}

    for source in SOURCES:
        section(source["name"])

        # 1. Try RSS candidates
        print("\n  RSS candidates:")
        for url in source["rss_candidates"]:
            ok, count, err = try_rss(url)
            if ok:
                print(f"  ✓ WORKING  {url}  ({count} entries)")
                working_feeds[source["name"]] = url
            else:
                print(f"  ✗ {url}")
                print(f"           {err}")

        # 2. Fetch listing page, look for autodiscovery
        print(f"\n  Fetching listing page: {source['listing_url']}")
        soup = fetch_page(source["listing_url"])
        if soup:
            rss_links = find_rss_autodiscovery(soup)
            if rss_links:
                print("\n  RSS autodiscovery links found in <head>:")
                for link in rss_links:
                    print(f"    {link}")
                    ok, count, err = try_rss(link)
                    status = f"✓ {count} entries" if ok else f"✗ {err}"
                    print(f"    → {status}")
                    if ok:
                        working_feeds[source["name"]] = link
            else:
                print("  No RSS autodiscovery links in <head>")

            print("\n  Page article structure (for selector tuning):")
            print_article_structure(soup)
        else:
            print("  Page fetch failed — may need browser/JS rendering")

    # Summary
    section("SUMMARY")
    if working_feeds:
        print("\n  Working RSS feeds found:")
        for name, url in working_feeds.items():
            print(f"    {name:25s} → {url}")
        print()
        print("  Next: update RSS_URL constants in pipeline/sources/*.py with these URLs")
    else:
        print("\n  No working RSS feeds found.")
        print("  Options:")
        print("  1. The sites may be JS-rendered — try playwright scraping")
        print("  2. Check if the sites have an API (look in devtools Network tab)")
        print("  3. Use a different approach: fetch full article pages via search APIs")
        print("     e.g. site:diplomatie.gouv.fr Weimar via SerpAPI or Brave Search API")


if __name__ == "__main__":
    main()
