"""Design §2.3: the sort key, the screened-out list, and what coverage counts."""

from __future__ import annotations

from dataclasses import replace

from app.domain.marketplace import GeoPoint, Offer, RequestLine
from app.domain.provenance import UNKNOWN, Provenance, fact
from app.domain.result import CheckStatus, Outcome
from app.matching.coverage import LineCoverage
from app.matching.pipeline import rank_candidates, screen_offers
from tests.factories import device, facility, intel

NAMEPLATE = Provenance.NAMEPLATE
HERE = GeoPoint(country="Testland", latitude=0.0, longitude=0.0)
NEAR = GeoPoint(country="Testland", latitude=0.0, longitude=0.5)
FAR = GeoPoint(country="Testland", latitude=0.0, longitude=20.0)


def site():
    return facility(latitude=0.0, longitude=0.0)


def offer(id: int, dev=None, origin=FAR, quantity=1) -> Offer:
    return Offer(donor_name=f"Donor {id}", donor_email="d@example.org",
                 device=dev or device(), origin=origin, quantity_offered=quantity, id=id,
                 device_type="oxygen concentrator")


def full_intel(d, t, country):
    return intel(country=country)


def three_add_ons():
    """Known to work, with a transformer, consumables and spare parts to send."""
    return device(
        voltage_min_v=fact(110, NAMEPLATE), voltage_max_v=fact(120, NAMEPLATE),
        consumables=fact(["filters"], Provenance.SPEC_SHEET),
    )


def lookup_without_supply(d, t, country):
    return intel(country=country, consumables_available=fact(False, Provenance.RECORDED),
                 spare_parts_available=fact(False, Provenance.RECORDED))


def unknown_device():
    return device(frequencies_hz=UNKNOWN)


def test_a_known_offer_with_three_add_ons_ranks_above_a_nearer_unknown_one_with_none():
    known = offer(1, three_add_ons(), origin=FAR)
    unknown = offer(2, unknown_device(), origin=HERE)

    ranked = rank_candidates(site(), [unknown, known], lookup_without_supply)

    assert [c.offer.id for c in ranked] == [1, 2]
    assert ranked[0].verdict.outcome is Outcome.WORKS_IF
    assert ranked[0].condition_count == 3
    assert ranked[1].verdict.outcome is Outcome.NEEDS_INFO


def test_among_unknowns_fewer_conditions_then_distance_still_order():
    near = offer(1, unknown_device(), origin=NEAR)
    here = offer(2, unknown_device(), origin=HERE)
    conditioned = offer(3, replace(three_add_ons(), frequencies_hz=UNKNOWN), origin=HERE)

    ranked = rank_candidates(site(), [near, conditioned, here], full_intel)

    assert [c.offer.id for c in ranked] == [2, 1, 3]


def test_rejected_offers_come_back_in_offer_order_with_their_verdicts():
    sixty = device(frequencies_hz=fact([60], NAMEPLATE))
    offers = [offer(5, sixty), offer(1), offer(3, sixty, origin=HERE)]

    screening = screen_offers(site(), offers, full_intel)

    assert [c.offer.id for c in screening.rejected] == [5, 3]
    for c in screening.rejected:
        assert c.verdict.outcome is Outcome.WONT_WORK
        assert [r.key for r in c.verdict.failures] == ["frequency"]
    assert [c.offer.id for c in screening.ranked] == [1]


def test_rejected_never_includes_offers_that_are_not_matchable():
    sixty = device(frequencies_hz=fact([60], NAMEPLATE))
    empty = replace(offer(1, sixty), quantity_offered=0)

    screening = screen_offers(site(), [empty], full_intel)

    assert screening.rejected == () and screening.ranked == []


def test_needs_info_survivors_do_not_count_toward_coverage():
    screening = screen_offers(
        site(),
        [offer(1, quantity=1), offer(2, unknown_device(), quantity=5)],
        full_intel,
    )
    cover = LineCoverage(line=RequestLine(device_type="oxygen concentrator", quantity=3),
                         candidates=screening.ranked, screened=2)

    assert cover.units_available == 1
    assert cover.shortfall == 2
    assert cover.donors_needed == 1
    assert [c.offer.id for c in cover.proposable] == [1]
    assert [c.offer.id for c in cover.asking] == [2]


def test_an_unknown_survivor_is_asked_about_on_the_unknown_check():
    screening = screen_offers(site(), [offer(1, unknown_device())], full_intel)

    (c,) = screening.ranked
    assert [r.key for r in c.verdict.unknowns] == ["frequency"]
    assert c.verdict.unknowns[0].status is CheckStatus.NEEDS_INFO
