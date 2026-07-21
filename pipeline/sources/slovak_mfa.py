from __future__ import annotations

from .feedbase import FeedIngester

# Slovak Ministry of Foreign and European Affairs (mzv.sk) — Visegrád Group
# member. Native-language (sk) news feed. (feed_url unverified in the authoring
# environment — see feedbase.py.)
FEED_URL = "https://www.mzv.sk/rss/-/asset_publisher/rss/aktuality"


class SlovakMFAIngester(FeedIngester):
    source_name = "slovak_mfa"
    source_lang = "sk"
    feed_url = FEED_URL
