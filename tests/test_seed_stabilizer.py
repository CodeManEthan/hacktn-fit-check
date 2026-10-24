"""The seeded stabilizers show the American spelling and still cover the requirement.

Users see an offer's device type. The requirement's stored kind keeps its
original spelling, so the offers keep that as their support kind.
"""

from __future__ import annotations

from app.data import marketplace_repo as repo
from app.domain.requirement import SEARCH_TERMS, RequirementKind
from scripts.seed_demo import SUPPORT_OFFERS
from tests.test_marketplace_repo import database  # noqa: F401

KIND = RequirementKind.STABILISER


def stabilizers():
    return [o for o in SUPPORT_OFFERS if o.support_kind == KIND.value]


def test_the_seeded_stabilizers_show_stabilizer_to_users():
    shown = [o.device_type for o in stabilizers()]

    assert len(shown) == 2
    assert all("stabilizer" in name and "stabiliser" not in name for name in shown)


def test_the_seeded_stabilizers_still_cover_the_stabilizer_requirement(database):  # noqa: F811
    for offer in SUPPORT_OFFERS:
        repo.save_offer(offer)

    found = repo.offers_for_support(KIND.value, SEARCH_TERMS[KIND])

    assert sorted(o.donor_name for o in found) == sorted(o.donor_name for o in stabilizers())
    assert all(o.device_type == "voltage stabilizer" for o in found)
