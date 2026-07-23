from __future__ import annotations

from .feedbase import FeedIngester

# Slovak Ministry of Foreign and European Affairs (mzv.sk) — Visegrád Group
# member. Native-language (sk) news feed — a Liferay "journal content RSS"
# export, confirmed to return recent press-release items.
FEED_URL = "https://www.mzv.sk/sk/web/sk/home/-/journal/rss/"


class SlovakMFAIngester(FeedIngester):
    source_name = "slovak_mfa"
    source_lang = "sk"
    feed_url = FEED_URL
