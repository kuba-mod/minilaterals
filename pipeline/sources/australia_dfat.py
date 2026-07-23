from __future__ import annotations

from .feedbase import FeedIngester

# Australia — AUKUS member. Foreign-policy output is published by the Foreign
# Minister's office; the portfolio site exposes a media-release RSS feed.
# (feed_url unverified in the authoring environment — see feedbase.py.)
FEED_URL = "https://www.foreignminister.gov.au/rss.xml"


class AustraliaDFATIngester(FeedIngester):
    source_name = "australia_dfat"
    source_lang = "en"
    feed_url = FEED_URL
