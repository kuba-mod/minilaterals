from .elysee import ElyseeIngester
from .france_diplomatie import FranceDiplomatieIngester
from .german_chancellery import GermanChancelleryIngester
from .german_mfa import GermanMFAIngester
from .polish_mfa import PolishMFAIngester
from .polish_pm import PolishPMIngester

ALL_INGESTERS = [
    GermanMFAIngester,
    FranceDiplomatieIngester,
    PolishMFAIngester,
    GermanChancelleryIngester,
    ElyseeIngester,
    PolishPMIngester,
]
