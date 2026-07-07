from .france_diplomatie import FranceDiplomatieIngester
from .german_mfa import GermanMFAIngester
from .polish_mfa import PolishMFAIngester

ALL_INGESTERS = [
    GermanMFAIngester,
    FranceDiplomatieIngester,
    PolishMFAIngester,
]
