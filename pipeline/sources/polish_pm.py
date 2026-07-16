from __future__ import annotations

from .govpl import GovPlIngester


class PolishPMIngester(GovPlIngester):
    """Chancellery of the Prime Minister (KPRM) — Weimar summits are leader-level,
    and the MFA does not reliably cover what the PM's office announces. Unlike the
    MFA, KPRM has no English-language portal on gov.pl, so this scrapes the Polish
    site (gov.pl/web/premier)."""

    source_name = "polish_pm"
    source_lang = "pl"
    news_url = "https://www.gov.pl/web/premier/wydarzenia"
