from __future__ import annotations

from .govpl import GovPlIngester


class PolishMFAIngester(GovPlIngester):
    source_name = "polish_mfa"
    source_lang = "en"
    # No RSS feed; scrape the news listing page directly (trailing dash is the
    # real gov.pl slug, not a typo).
    news_url = "https://www.gov.pl/web/diplomacy/news-"
