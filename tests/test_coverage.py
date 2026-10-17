"""How much of one request line the compatible offers can actually cover.

Coverage is counted only across offers that already passed the screening, so
an incompatible donor with a full warehouse contributes nothing.
"""

from __future__ import annotations

from app.domain.marketplace import GeoPoint, Offer, RequestLine
from app.matching.coverage import LineCoverage
from app.matching.pipeline import Candidate
from app.domain.result import Outcome, Verdict
from tests.factories import device

SOMEWHERE = GeoPoint(country="Testland", city="Town", latitude=0.0, longitude=0.0)


def candidate(available: int, offer_id: int = 1) -> Candidate:
    offer = Offer(
        donor_name=f"Donor {offer_id}",
        donor_email="d@example.org",
        device=device(),
        device_type="ventilator",
        origin=SOMEWHERE,
        quantity_offered=available,
        id=offer_id,
    )
    return Candidate(offer=offer, verdict=Verdict(outcome=Outcome.WORKS, results=()))


def line(quantity: int = 5) -> RequestLine:
    return RequestLine(device_type="ventilator", quantity=quantity)


def test_a_single_donor_holding_enough_covers_the_line_outright():
    # Arrange / Act
    found = LineCoverage(line=line(5), candidates=[candidate(5)], screened=1)

    # Assert
    assert found.units_available == 5
    assert found.complete
    assert not found.needs_split
    assert found.shortfall == 0


def test_two_part_donors_together_cover_the_line_but_need_a_split():
    # Arrange: nobody has five, but three and two together do.
    found = LineCoverage(
        line=line(5), candidates=[candidate(3, 1), candidate(2, 2)], screened=2
    )

    # Assert
    assert found.units_available == 5
    assert found.complete
    assert found.needs_split
    assert found.donors_needed == 2


def test_a_short_line_reports_what_is_missing():
    found = LineCoverage(line=line(5), candidates=[candidate(2)], screened=1)

    assert found.units_available == 2
    assert not found.complete
    assert found.shortfall == 3


def test_surplus_stock_never_counts_past_what_was_asked_for():
    # Arrange: plenty available, but the clinic asked for five.
    found = LineCoverage(line=line(5), candidates=[candidate(40)], screened=1)

    # Assert
    assert found.units_available == 5
    assert found.shortfall == 0


def test_a_line_nobody_can_supply_is_a_total_gap():
    found = LineCoverage(line=line(5), candidates=[], screened=0)

    assert found.units_available == 0
    assert found.shortfall == 5
    assert not found.complete
    assert found.donors_needed == 0


def test_donors_needed_counts_only_as_many_as_it_takes():
    # Arrange: the largest donor first, so one suffices and the second is spare.
    found = LineCoverage(
        line=line(3), candidates=[candidate(5, 1), candidate(5, 2)], screened=2
    )

    # Assert
    assert found.donors_needed == 1
    assert not found.needs_split
