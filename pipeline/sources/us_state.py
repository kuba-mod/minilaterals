from __future__ import annotations

from .feedbase import FeedIngester

# U.S. Department of State — AUKUS member. state.gov switched its content type
# from a "press-releases" taxonomy to a "releases" custom post type; the
# site-wide feed (confirmed via the page's <link rel="alternate"> tag) is here.
FEED_URL = "https://www.state.gov/feed/"


class USStateIngester(FeedIngester):
    source_name = "us_state"
    source_lang = "en"
    feed_url = FEED_URL
