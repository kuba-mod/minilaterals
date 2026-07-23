from .australia_dfat import AustraliaDFATIngester
from .elysee import ElyseeIngester
from .estonian_mfa import EstonianMFAIngester
from .france_diplomatie import FranceDiplomatieIngester
from .german_chancellery import GermanChancelleryIngester
from .german_mfa import GermanMFAIngester
from .latvian_mfa import LatvianMFAIngester
from .lithuanian_mfa import LithuanianMFAIngester
from .polish_mfa import PolishMFAIngester
from .polish_pm import PolishPMIngester
from .uk_fcdo import UKFCDOIngester
from .us_state import USStateIngester

# Visegrád Group (czech_mfa, slovak_mfa, hungary_government) is paused for now:
# their feed URLs have proven the most difficult to pin down live (czech_mfa
# has no confirmed feed at all yet; hungary_government's is an unverified
# guess). The classes are still in the repo, ready to re-register here once
# their feed URLs are confirmed working — this isn't a design change, just a
# collection pause so the confirmed-working groups (E3, AUKUS, Baltic Three)
# aren't held up by it. Poland (polish_mfa/polish_pm) still ingests as a
# Weimar source as before; it just isn't joined by CZ/SK/HU right now.

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
    # AUKUS (adds US/AU; reuses UK)
    USStateIngester,
    AustraliaDFATIngester,
    # Baltic Three (EE/LV/LT)
    EstonianMFAIngester,
    LatvianMFAIngester,
    LithuanianMFAIngester,
]
