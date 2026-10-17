"""Verification tiers, and the rule that stops a name match certifying anything.

The live registry is never called here. These tests fix the judgement applied
to whatever it returns, which is the part that must not drift.
"""

from __future__ import annotations

from app.domain.facility_trust import (
    TRUSTED_TIERS,
    RegistryMatch,
    Verification,
    VerificationStatus,
)
from app.reference.facility_registry import (
    CORROBORATION_RADIUS_KM,
    _search_terms,
    corroborates,
)

NEARBY_KM = 2.0
FAR_KM = 400.0


def match(**overrides) -> RegistryMatch:
    base = dict(
        name="Kayunga Hospital",
        display_name="Kayunga Hospital, Kayunga, Uganda",
        latitude=0.70,
        longitude=32.88,
        category="amenity",
        kind="hospital",
        distance_km=NEARBY_KM,
    )
    return RegistryMatch(**{**base, **overrides})


def test_a_nearby_hospital_corroborates_the_claim():
    assert corroborates(match())


def test_a_nursery_school_sharing_the_name_does_not_corroborate():
    # Arrange: the real false positive the live registry returned for Kayunga.
    nursery = match(name="Kayunga Hospital Nursery School", kind="school")

    # Assert
    assert not corroborates(nursery)


def test_a_hospital_too_far_away_is_a_different_place():
    assert not corroborates(match(distance_km=FAR_KM))


def test_a_hospital_at_exactly_the_radius_still_counts():
    assert corroborates(match(distance_km=CORROBORATION_RADIUS_KM))


def test_a_match_without_a_distance_cannot_corroborate():
    """No stated coordinates means a name match is the only signal, which is not enough."""
    assert not corroborates(match(distance_km=None))


def test_a_pharmacy_is_not_a_health_facility_for_this_purpose():
    assert not corroborates(match(kind="pharmacy"))


def test_search_terms_fall_back_from_the_official_name_to_a_shorter_one():
    # Arrange / Act: the live registry finds the second but not the first.
    terms = _search_terms("Kayunga District Hospital")

    # Assert
    assert terms[0] == "Kayunga District Hospital"
    assert "Kayunga Hospital" in terms


def test_a_two_word_name_is_not_shortened_into_nonsense():
    assert _search_terms("Kayunga Hospital") == ["Kayunga Hospital"]


def test_self_declared_is_the_honest_default_and_is_not_trusted():
    status = VerificationStatus()

    assert status.tier is Verification.SELF_DECLARED
    assert not status.trusted


def test_every_trusted_tier_reports_as_trusted():
    for tier in TRUSTED_TIERS:
        assert VerificationStatus(tier=tier).trusted


def test_a_disputed_facility_is_never_trusted():
    assert not VerificationStatus(tier=Verification.DISPUTED).trusted
