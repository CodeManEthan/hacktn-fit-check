"""Matching: which compatible donor is nearest, and what happens next.

Compatibility is decided in app.checks and nothing here second-guesses it.
No model is called in this package.
"""

from app.matching.distance import EARTH_RADIUS_KM, facility_point, haversine_km
from app.matching.pipeline import (
    Candidate,
    IntelLookup,
    Screening,
    empty_intel,
    propose_match,
    rank_candidates,
    screen_offers,
)
from app.matching.verification import (
    LEGAL_TRANSITIONS,
    InvalidTransition,
    confirm,
    has_timed_out,
    mark_shipped,
    request_confirmation,
    sweep_timeouts,
    transition,
    withdraw,
)

__all__ = [
    "Bundle",
    "BundleItem",
    "build_bundle",
    "EARTH_RADIUS_KM",
    "Candidate",
    "IntelLookup",
    "InvalidTransition",
    "LEGAL_TRANSITIONS",
    "confirm",
    "empty_intel",
    "facility_point",
    "has_timed_out",
    "haversine_km",
    "mark_shipped",
    "propose_match",
    "rank_candidates",
    "Screening",
    "screen_offers",
    "request_confirmation",
    "sweep_timeouts",
    "transition",
    "withdraw",
]

from app.matching.bundle import Bundle, BundleItem, build_bundle  # noqa: E402
