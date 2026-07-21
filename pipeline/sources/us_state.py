from __future__ import annotations

from .feedbase import FeedIngester

# U.S. Department of State — AUKUS member. state.gov is a WordPress site; its
# press-releases collection is published at the conventional `/feed/` endpoint.
FEED_URL = "https://www.state.gov/press-releases/feed/"


class USStateIngester(FeedIngester):
    source_name = "us_state"
    source_lang = "en"
    feed_url = FEED_URL
