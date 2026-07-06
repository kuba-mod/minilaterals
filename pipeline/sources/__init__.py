from .german_mfa import GermanMFAIngester
from .france_diplomatie import FranceDiplomatieIngester
from .polish_mfa import PolishMFAIngester

ALL_INGESTERS = [
    GermanMFAIngester,
    FranceDiplomatieIngester,
    PolishMFAIngester,
]
