"""How far a clinic's claim to exist has actually been checked.

This mirrors Provenance on the device side. A device fact carries where it
came from; a facility carries how hard anyone looked before believing it. In
both cases the point is that the interface can show the grounds rather than a
bare green tick, because a tick nobody can interrogate is worth nothing.

Nothing here is a judgement about a clinic's honesty. SELF_DECLARED is the
honest starting state for every real hospital that has simply not been checked
yet, and the overwhelming majority of them are exactly what they say they are.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verification(str, Enum):
    """The grounds on which a facility is believed to be real."""

    REVIEWER_APPROVED = "approved by a reviewer"
    REGISTRY_MATCHED = "matched in a public registry"
    DOCUMENT_ON_FILE = "registration document on file"
    SELF_DECLARED = "entered on the form"
    DISPUTED = "contradicted by a check"


# Tiers a donor can rely on without doing their own homework. SELF_DECLARED is
# deliberately outside it: it is not an accusation, it is an unchecked claim.
TRUSTED_TIERS = frozenset(
    {
        Verification.REVIEWER_APPROVED,
        Verification.REGISTRY_MATCHED,
        Verification.DOCUMENT_ON_FILE,
    }
)

# Ranked worst to best, so the weakest ground a facility stands on is the one
# that gets reported.
TIER_ORDER = (
    Verification.DISPUTED,
    Verification.SELF_DECLARED,
    Verification.DOCUMENT_ON_FILE,
    Verification.REGISTRY_MATCHED,
    Verification.REVIEWER_APPROVED,
)


@dataclass(frozen=True)
class RegistryMatch:
    """One candidate a public registry returned for a facility's name.

    `distance_km` is the gap between where the clinic says it is and where the
    registry puts this candidate. It is the whole value of the lookup: a name
    can be coincidence, a name within a few kilometres of the stated point is
    corroboration.
    """

    name: str
    display_name: str
    latitude: float
    longitude: float
    category: str = ""
    kind: str = ""
    distance_km: float | None = None

    @property
    def is_health_facility(self) -> bool:
        """True when the registry itself calls this a health facility."""
        return self.kind in {"hospital", "clinic", "doctors", "health_post"}


@dataclass(frozen=True)
class VerificationStatus:
    """Where a facility stands, and the evidence behind it."""

    tier: Verification = Verification.SELF_DECLARED
    note: str = ""
    candidates: tuple[RegistryMatch, ...] = ()

    @property
    def trusted(self) -> bool:
        return self.tier in TRUSTED_TIERS

    @property
    def label(self) -> str:
        return self.tier.value
