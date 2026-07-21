from __future__ import annotations

from .feedbase import FeedIngester

# Latvian Ministry of Foreign Affairs (mfa.gov.lv) — Baltic Three member.
# Native-language (lv) news feed. (feed_url unverified in the authoring
# environment — see feedbase.py.)
FEED_URL = "https://www.mfa.gov.lv/lv/rss.xml"


class LatvianMFAIngester(FeedIngester):
    source_name = "latvian_mfa"
    source_lang = "lv"
    feed_url = FEED_URL
