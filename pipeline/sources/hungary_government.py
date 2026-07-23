from __future__ import annotations

from .feedbase import FeedIngester

# Hungarian Ministry of Foreign Affairs and Trade (kormany.hu) — Visegrád Group
# member. Native-language (hu) news feed for the ministry. (feed_url unverified
# in the authoring environment — see feedbase.py.)
FEED_URL = "https://kormany.hu/rss/kulgazdasagi-es-kulugyminiszterium"


class HungarianMFAIngester(FeedIngester):
    source_name = "hungarian_mfa"
    source_lang = "hu"
    feed_url = FEED_URL
