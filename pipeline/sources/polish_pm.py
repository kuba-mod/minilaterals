from __future__ import annotations

from .govpl import GovPlIngester


class PolishPMIngester(GovPlIngester):
    """Chancellery of the Prime Minister (KPRM) — Weimar summits are leader-level,
    and the MFA does not reliably cover what the PM's office announces."""
    source_name = "polish_pm"
    source_lang = "en"
    news_url = "https://www.gov.pl/web/primeminister/news"
