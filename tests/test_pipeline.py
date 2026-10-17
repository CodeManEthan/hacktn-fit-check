"""The pipeline's one rule: compatibility filters, distance only orders."""

from dataclasses import replace
from datetime import date

import pytest

from app.domain.device import SupplyIntel
from app.domain.marketplace import (
    GeoPoint,
    MatchState,
    Offer,
    OfferStatus,
    Request,
    RequestLine,
)
from app.domain.provenance import Provenance, fact
from app.domain.result import Outcome
from app.matching.pipeline import Candidate, empty_intel, propose_match, rank_candidates
from tests.factories import device, facility, intel

NAMEPLATE = Provenance.NAMEPLATE

HERE = GeoPoint(country="Testland", city="Same street", latitude=0.0, longitude=0.0)
NEARBY = GeoPoint(country="Testland", city="Next town", latitude=0.0, longitude=1.0)
FAR = GeoPoint(country="Testland", city="Far side", latitude=0.0, longitude=10.0)
UNLOCATED = GeoPoint(country="Testland", city="Address only")

TODAY = date(2026, 3, 1)
YESTERDAY = date(2026, 2, 28)
TOMORROW = date(2026, 3, 2)


def site(**overrides):
    """The receiving clinic, at the origin of the test map."""
    return facility(latitude=0.0, longitude=0.0, **overrides)


def offer(**overrides) -> Offer:
    """A donor holding one unremarkable device next door."""
    base = Offer(
        donor_name="Donor",
        donor_email="donor@example.org",
        device=device(),
        origin=HERE,
        quantity_offered=1,
        id=1,
    )
    return replace(base, **overrides)


def request(**overrides) -> Request:
    base = Request(
        facility_slug="test-clinic",
        lines=(RequestLine(device_type="ventilator", quantity=1),),
        id=7,
    )
    return replace(base, **overrides)


def wrong_frequency_device():
    """A device whose motor would burn out on this grid: a hard fail."""
    return device(frequencies_hz=fact([60], NAMEPLATE))


def needs_a_transformer_device():
    """A device that works here only if a transformer is sent with it."""
    return device(
        voltage_min_v=fact(110, NAMEPLATE),
        voltage_max_v=fact(120, NAMEPLATE),
    )


def full_intel(device_facts, country):
    return intel(country=country)


def test_a_device_blocked_by_the_fit_check_never_appears_however_near_it_is():
    # Arrange: the blocked device is at zero distance, the working one is far away.
    blocked = offer(id=1, device=wrong_frequency_device(), origin=HERE)
    workable = offer(id=2, device=device(), origin=FAR)

    # Act
    ranked = rank_candidates(site(mains_frequency_hz=50), [blocked, workable], full_intel)

    # Assert
    assert [c.offer.id for c in ranked] == [2]
    assert all(c.verdict.outcome is not Outcome.WONT_WORK for c in ranked)


def test_an_offer_with_no_available_stock_is_excluded():
    reserved_out = offer(id=1, quantity_offered=2, quantity_reserved=1, quantity_shipped=1)
    in_stock = offer(id=2, origin=FAR)

    ranked = rank_candidates(site(), [reserved_out, in_stock], full_intel)

    assert [c.offer.id for c in ranked] == [2]


def test_an_expired_offer_is_excluded():
    expired = offer(id=1, available_until=YESTERDAY)
    still_open = offer(id=2, available_until=TOMORROW, origin=FAR)

    ranked = rank_candidates(site(), [expired, still_open], full_intel, today=TODAY)

    assert [c.offer.id for c in ranked] == [2]


def test_an_offer_that_is_not_open_is_excluded():
    withdrawn = offer(id=1, status=OfferStatus.WITHDRAWN)
    exhausted = offer(id=2, status=OfferStatus.EXHAUSTED)
    open_offer = offer(id=3, origin=FAR)

    ranked = rank_candidates(site(), [withdrawn, exhausted, open_offer], full_intel)

    assert [c.offer.id for c in ranked] == [3]


def test_the_nearest_donor_wins_among_equally_compatible_offers():
    far = offer(id=1, origin=FAR)
    nearby = offer(id=2, origin=NEARBY)
    here = offer(id=3, origin=HERE)

    ranked = rank_candidates(site(), [far, nearby, here], full_intel)

    assert [c.offer.id for c in ranked] == [3, 2, 1]
    assert ranked[0].distance_km == pytest.approx(0.0, abs=1e-9)


def test_fewer_conditions_outrank_a_nearer_offer_that_needs_more_sent_with_it():
    conditional_next_door = offer(id=1, device=needs_a_transformer_device(), origin=HERE)
    clean_but_far = offer(id=2, device=device(), origin=FAR)

    ranked = rank_candidates(site(mains_voltage_v=230), [conditional_next_door, clean_but_far], full_intel)

    assert [c.offer.id for c in ranked] == [2, 1]
    assert len(ranked[1].verdict.conditional) == 1


def test_an_offer_without_coordinates_sorts_last():
    unlocated = offer(id=1, origin=UNLOCATED)
    far = offer(id=2, origin=FAR)

    ranked = rank_candidates(site(), [unlocated, far], full_intel)

    assert [c.offer.id for c in ranked] == [2, 1]
    assert ranked[-1].distance_km is None


def test_an_unlocated_offer_still_sorts_ahead_of_a_located_one_with_more_conditions():
    unlocated_clean = offer(id=1, origin=UNLOCATED)
    near_conditional = offer(id=2, device=needs_a_transformer_device(), origin=HERE)

    ranked = rank_candidates(site(), [near_conditional, unlocated_clean], full_intel)

    assert [c.offer.id for c in ranked] == [1, 2]


def test_the_default_lookup_needs_no_model_and_still_ranks():
    offers = [offer(id=1, origin=FAR), offer(id=2, origin=HERE)]

    ranked = rank_candidates(site(), offers)

    assert [c.offer.id for c in ranked] == [2, 1]
    assert all(c.intel == SupplyIntel(country="Testland") for c in ranked)


def test_the_default_lookup_states_nothing_about_the_country():
    stated = empty_intel(device(), "Testland")

    assert stated.country == "Testland"
    assert not stated.consumables_available.known
    assert not stated.spare_parts_available.known


def test_the_intel_lookup_sees_each_offer_device_and_the_facility_country():
    seen = []

    def recording_lookup(device_facts, country):
        seen.append((device_facts.model.value, country))
        return intel(country=country)

    rank_candidates(site(country="Malawi"), [offer(id=1), offer(id=2)], recording_lookup)

    assert seen == [("V100", "Malawi"), ("V100", "Malawi")]


def test_a_lookup_that_returns_the_wrong_type_is_rejected_loudly():
    def broken_lookup(device_facts, country):
        return {"country": country}

    with pytest.raises(TypeError, match="SupplyIntel"):
        rank_candidates(site(), [offer()], broken_lookup)


def test_ranking_never_mutates_the_offers_it_was_given():
    offers = (offer(id=1, origin=FAR), offer(id=2, origin=HERE))
    before = tuple(replace(o) for o in offers)

    rank_candidates(site(), offers, full_intel)

    assert offers == before


def test_an_empty_offer_list_ranks_to_nothing():
    assert rank_candidates(site(), [], full_intel) == []


def test_propose_match_builds_a_proposed_record_carrying_the_verdict_and_distance():
    ranked = rank_candidates(site(), [offer(id=4, origin=NEARBY, quantity_offered=3)], full_intel)
    candidate = ranked[0]

    record = propose_match(request(id=7), candidate, quantity=2)

    assert record.state is MatchState.PROPOSED
    assert record.request_id == 7
    assert record.offer_id == 4
    assert record.verdict is candidate.verdict
    assert record.intel is candidate.intel
    assert record.distance_km == candidate.distance_km
    assert record.quantity == 2
    assert record.token


def test_propose_match_refuses_more_than_the_donor_has_available():
    candidate = Candidate(offer=offer(id=1, quantity_offered=1), verdict=_clean_verdict(), distance_km=0.0)

    with pytest.raises(ValueError, match="available"):
        propose_match(request(), candidate, quantity=2)


def test_propose_match_refuses_a_non_positive_quantity():
    candidate = Candidate(offer=offer(id=1), verdict=_clean_verdict(), distance_km=0.0)

    with pytest.raises(ValueError, match="at least 1"):
        propose_match(request(), candidate, quantity=0)


def test_propose_match_refuses_an_unsaved_request_or_offer():
    candidate = Candidate(offer=offer(id=None), verdict=_clean_verdict(), distance_km=0.0)

    with pytest.raises(ValueError, match="unsaved"):
        propose_match(request(id=7), candidate, quantity=1)

    with pytest.raises(ValueError, match="unsaved"):
        propose_match(request(id=None), Candidate(offer=offer(id=1), verdict=_clean_verdict()), quantity=1)


def _clean_verdict():
    ranked = rank_candidates(site(), [offer()], full_intel)
    return ranked[0].verdict
