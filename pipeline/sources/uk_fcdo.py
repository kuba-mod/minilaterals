from __future__ import annotations

from .feedbase import FeedIngester

# UK Foreign, Commonwealth & Development Office — E3 and AUKUS member.
# gov.uk exposes a stable Atom feed for any finder by appending `.atom` to the
# search path; scoping to the FCDO organisation gives its news & communications.
FEED_URL = (
    "https://www.gov.uk/search/news-and-communications.atom"
    "?organisations%5B%5D=foreign-commonwealth-development-office"
)


class UKFCDOIngester(FeedIngester):
    source_name = "uk_fcdo"
    source_lang = "en"
    feed_url = FEED_URL
