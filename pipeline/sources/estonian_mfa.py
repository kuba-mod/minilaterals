from __future__ import annotations

from .feedbase import FeedIngester

# Estonian Ministry of Foreign Affairs (vm.ee) — Baltic Three member.
# Native-language (et) news feed. Confirmed working.
FEED_URL = "https://vm.ee/rss-feeds/rss.xml"


class EstonianMFAIngester(FeedIngester):
    source_name = "estonian_mfa"
    source_lang = "et"
    feed_url = FEED_URL
