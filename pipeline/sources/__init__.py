from .german_mfa import GermanMFAIngester
from .france_diplomatie import FranceDiplomatieIngester
from .polish_mfa import PolishMFAIngester
from .council_eu import CouncilEUIngester
from .gdelt import GDELTIngester

ALL_INGESTERS = [
    GermanMFAIngester,
    FranceDiplomatieIngester,
    PolishMFAIngester,
    CouncilEUIngester,
    GDELTIngester,
]
