from __future__ import annotations

from .feedbase import FeedIngester

# Latvian Ministry of Foreign Affairs (mfa.gov.lv) — Baltic Three member.
# Native-language (lv) news feed. Confirmed working; the site also publishes a
# separate .../rss/events calendar feed (ministerial diary, not news text),
# which "articles" is the better match for position extraction.
FEED_URL = "https://www.mfa.gov.lv/lv/rss/articles"


class LatvianMFAIngester(FeedIngester):
    source_name = "latvian_mfa"
    source_lang = "lv"
    feed_url = FEED_URL
