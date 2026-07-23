from __future__ import annotations

from .feedbase import FeedIngester

# Czech Ministry of Foreign Affairs (mzv.gov.cz) — Visegrád Group member.
# Native-language (cs) press-release feed. (feed_url unverified in the authoring
# environment — see feedbase.py.)
FEED_URL = "https://mzv.gov.cz/jnp/cz/rss/tiskove_zpravy.xml"


class CzechMFAIngester(FeedIngester):
    source_name = "czech_mfa"
    source_lang = "cs"
    feed_url = FEED_URL
