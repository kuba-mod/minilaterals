from __future__ import annotations

from .feedbase import FeedIngester

# Lithuanian Ministry of Foreign Affairs (urm.lt) — Baltic Three member.
# Native-language (lt) news feed. Confirmed working.
FEED_URL = "https://www.urm.lt/globalnewsrss"


class LithuanianMFAIngester(FeedIngester):
    source_name = "lithuanian_mfa"
    source_lang = "lt"
    feed_url = FEED_URL
