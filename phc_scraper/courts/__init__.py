"""Court registry. Set COURT_ID=phc (default)."""
import os

from .base import CourtProfile
from .phc import PHC

_REGISTRY: dict[str, CourtProfile] = {
    "phc": PHC,
}


def get_court() -> CourtProfile:
    court_id = os.environ.get("COURT_ID", "phc").strip().lower()
    if court_id not in _REGISTRY:
        raise ValueError(
            f"Unknown COURT_ID={court_id!r}. "
            f"Available: {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[court_id]