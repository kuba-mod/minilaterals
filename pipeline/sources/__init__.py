from .australia_dfat import AustraliaDFATIngester
from .czech_mfa import CzechMFAIngester
from .elysee import ElyseeIngester
from .estonian_mfa import EstonianMFAIngester
from .france_diplomatie import FranceDiplomatieIngester
from .german_chancellery import GermanChancelleryIngester
from .german_mfa import GermanMFAIngester
from .hungary_government import HungaryGovernmentIngester
from .latvian_mfa import LatvianMFAIngester
from .lithuanian_mfa import LithuanianMFAIngester
from .polish_mfa import PolishMFAIngester
from .polish_pm import PolishPMIngester
from .slovak_mfa import SlovakMFAIngester
from .uk_fcdo import UKFCDOIngester
from .us_state import USStateIngester

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
    # Visegrád Group (adds CZ/SK/HU; reuses PL)
    CzechMFAIngester,
    SlovakMFAIngester,
    HungaryGovernmentIngester,
    # Baltic Three (EE/LV/LT)
    EstonianMFAIngester,
    LatvianMFAIngester,
    LithuanianMFAIngester,
]
