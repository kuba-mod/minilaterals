from .australia_dfat import AustraliaDFATIngester
from .elysee import ElyseeIngester
from .estonian_mfa import EstonianMFAIngester
from .france_diplomatie import FranceDiplomatieIngester
from .german_chancellery import GermanChancelleryIngester
from .german_mfa import GermanMFAIngester
from .latvian_mfa import LatvianMFAIngester
from .polish_mfa import PolishMFAIngester
from .polish_pm import PolishPMIngester
from .uk_fcdo import UKFCDOIngester

# Visegrád Group (czech_mfa, slovak_mfa, hungary_government) is paused for now:
# their feed URLs have proven the most difficult to pin down live (czech_mfa
# has no confirmed feed at all yet; hungary_government's is an unverified
# guess). The classes are still in the repo, ready to re-register here once
# their feed URLs are confirmed working — this isn't a design change, just a
# collection pause so the confirmed-working groups (E3, AUKUS, Baltic Three)
# aren't held up by it. Poland (polish_mfa/polish_pm) still ingests as a
# Weimar source as before; it just isn't joined by CZ/SK/HU right now.
#
# lithuanian_mfa is also paused: its feed URL is correct (confirmed live), but
# the site sits behind Cloudflare bot-protection that returns a JS challenge —
# not solvable without impersonating a browser, which design principle #10
# rules out. The class stays in the repo in case the site's protection
# changes or an alternate reachable source for Lithuania turns up.
#
# us_state is paused too, for the same reason as lithuanian_mfa, confirmed via
# live workflow_dispatch runs rather than guessed: state.gov's sitewide /feed/
# RSS endpoint is a genuinely dead/orphaned feed (real XML, zero <item>s, not a
# block), but the actual data source — the state_press_release WordPress REST
# endpoint (see WPRestIngester in wprest.py) — returns real JSON when fetched
# from a browser and a 200 "Technical Difficulties" HTML page (not JSON, not a
# JS challenge either) when fetched from GitHub Actions' runner IPs. Adding a
# legitimate Accept: application/json header didn't change the outcome, which
# rules out a header-completeness explanation. Bypassing this would mean
# disguising the request further (a residential proxy, browser automation to
# clear a hidden challenge), which design principle #10 rules out just as it
# does for lithuanian_mfa. The class stays in the repo in case state.gov's
# protection changes, or an alternate reachable path is found.

ALL_INGESTERS = [
    # Weimar Triangle (DE/FR/PL) — MFAs + heads-of-government offices
    GermanMFAIngester,
    FranceDiplomatieIngester,
    PolishMFAIngester,
    GermanChancelleryIngester,
    ElyseeIngester,
    PolishPMIngester,
    # E3 (adds UK; reuses DE/FR)
    UKFCDOIngester,
    # AUKUS (adds AU; US paused — see comment above; reuses UK)
    AustraliaDFATIngester,
    # Baltic Three (EE/LV; LT paused — see comment above)
    EstonianMFAIngester,
    LatvianMFAIngester,
]
