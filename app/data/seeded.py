"""Which rows the demo seed writes: its donors, its clinics and its request.

scripts/seed_demo.py takes its donor names from here, so the seed and anything
that asks "is this the seed's row?" read the same list.

The donors are fictional. The seed once used real institutions' names; it
renames offers stored under those names in place (DONOR_RENAMES), so the
offers keep their ids. An offer under either name counts as seeded, which also
covers the moment between a restart and the seed's rename. A visitor who types
a seeded donor's name gets an offer that counts as seeded too; that is accepted.

The clinics keep their real names: the public-registry check looks them up.
"""

from __future__ import annotations

from app.data import marketplace_repo as repo
from app.data.seed import FACILITIES
from app.domain.marketplace import Offer

# The three oxygen concentrators the worked example screens at Kayunga.
HERO_A_DONOR = "Riverside General biomedical department"  # the 525DS, Nashville
HERO_B_DONOR = "Midland Teaching Hospital, surplus"  # the 525KS, Leeds
HERO_C_DONOR = "Bayou community clinic network"  # the older unit, Houston

# The other demo offers and the support equipment.
VENTILATOR_DONOR = "Westbrook Hospital biomedical department"  # Puritan Bennett 840
ANAESTHESIA_DONOR = "Lakefront Hospital surplus"  # Drager Fabius Tiro
MONITOR_DONOR = "Nairobi Equipment Trust"  # Philips SureSigns VM4
MOMBASA_VENTILATOR_DONOR = "Coral Coast Medical Centre"  # Hamilton C1
POWER_DONOR = "Kampala Power Trust"  # Sollatek AVS30 and APC Smart-UPS
GASES_DONOR = "Mombasa Medical Gases"  # Bambi VT200
AVR_DONOR = "Houston surplus AVR"  # the US regulator that fails at Kayunga

SEEDED_DONORS: tuple[str, ...] = (
    HERO_A_DONOR, HERO_B_DONOR, HERO_C_DONOR,
    VENTILATOR_DONOR, ANAESTHESIA_DONOR, MONITOR_DONOR, MOMBASA_VENTILATOR_DONOR,
    POWER_DONOR, GASES_DONOR, AVR_DONOR,
)

# The real names the seed used before, and the fictional name each became.
# deploy/demo_ids.py carries its own copy of the three hero lines, because it
# runs against whichever head is live; the two copies must agree, and
# tests/test_seeded.py checks that they do.
DONOR_RENAMES: dict[str, str] = {
    "Nashville General Hospital biomedical department": HERO_A_DONOR,
    "Leeds Teaching Hospitals NHS Trust, surplus": HERO_B_DONOR,
    "Harris County clinic network": HERO_C_DONOR,
    "Baptist Health biomedical department": VENTILATOR_DONOR,
    "St Mary's Hospital surplus": ANAESTHESIA_DONOR,
    "Gulf Coast Medical Center": MOMBASA_VENTILATOR_DONOR,
}

HERO_DONORS: tuple[str, ...] = (HERO_A_DONOR, HERO_B_DONOR, HERO_C_DONOR)


def _plain(name: str) -> str:
    return name.strip().casefold()


_SEEDED_NAMES = frozenset(_plain(name) for name in (*SEEDED_DONORS, *DONOR_RENAMES))


def is_seeded_donor(name: str) -> bool:
    """True for a donor name the seed writes, now or under its old name."""
    return _plain(name or "") in _SEEDED_NAMES


def is_seeded_offer(offer: Offer) -> bool:
    return is_seeded_donor(offer.donor_name)


# ---- clinics -------------------------------------------------------------------

SEEDED_CLINIC_SLUGS = frozenset(profile.slug for profile in FACILITIES)


def is_seeded_clinic(slug: str) -> bool:
    return slug in SEEDED_CLINIC_SLUGS


# ---- the Kayunga request -------------------------------------------------------

KAYUNGA = "uganda-district"
KAYUNGA_REQUEST_TYPE = "oxygen concentrator"


def kayunga_request_id() -> int | None:
    """The seeded Kayunga request's id: the open request there for an oxygen concentrator.

    The seed makes it once and finds it again this way; its id differs from
    one database to the next. Raises repo.RepositoryError when the requests
    can't be read.
    """
    for row in repo.list_open_requests():
        if row.facility_slug == KAYUNGA and any(
            line.device_type.strip().casefold() == KAYUNGA_REQUEST_TYPE for line in row.lines
        ):
            return row.id
    return None


def is_seeded_request(request_id: int) -> bool:
    found = kayunga_request_id()
    return found is not None and found == request_id
