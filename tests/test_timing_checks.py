"""Shelf life against the date the consignment actually lands.

A sterile circuit with four months on it is fine in a storeroom and useless
on a container that takes three. Every test here fixes `today` so the
arithmetic is stable whenever the suite runs.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.checks.timing import MIN_USABLE_DAYS_ON_ARRIVAL, check_shelf_life
from app.domain.provenance import Provenance, fact
from app.domain.result import CheckStatus
from app.domain.shipment import Shipment
from tests.factories import device, facility, intel

TODAY = date(2026, 1, 1)
TRANSIT_DAYS = 55


def shipment(days: int = TRANSIT_DAYS) -> Shipment:
    return Shipment(transit_days=days)


def expiring_in(days: int):
    return fact(TODAY + timedelta(days=days), Provenance.DONOR_STATED)


def test_an_unknown_expiry_date_is_asked_about_never_assumed():
    # Arrange: a device whose expiry nobody recorded.
    subject = device()

    # Act
    result = check_shelf_life(subject, facility(), intel(), shipment(), today=TODAY)

    # Assert
    assert result.status is CheckStatus.NEEDS_INFO
    assert result.question


def test_stock_that_expires_before_it_lands_is_blocked():
    # Arrange: 30 days of life, 55 days in transit.
    subject = device(expiry_date=expiring_in(30))

    # Act
    result = check_shelf_life(subject, facility(), intel(), shipment(), today=TODAY)

    # Assert
    assert result.status is CheckStatus.FAIL
    assert "before" in result.detail


def test_stock_already_expired_is_blocked_even_without_a_route():
    # Arrange
    subject = device(expiry_date=expiring_in(-1))

    # Act
    result = check_shelf_life(subject, facility(), intel(), None, today=TODAY)

    # Assert
    assert result.status is CheckStatus.FAIL


def test_a_short_dated_arrival_is_shippable_only_on_a_condition():
    # Arrange: lands with less than the minimum usable window, but not expired.
    subject = device(expiry_date=expiring_in(TRANSIT_DAYS + 60))

    # Act
    result = check_shelf_life(subject, facility(), intel(), shipment(), today=TODAY)

    # Assert
    assert result.status is CheckStatus.MITIGABLE
    assert result.fix


def test_ample_remaining_life_on_arrival_passes():
    # Arrange
    subject = device(expiry_date=expiring_in(TRANSIT_DAYS + MIN_USABLE_DAYS_ON_ARRIVAL + 1))

    # Act
    result = check_shelf_life(subject, facility(), intel(), shipment(), today=TODAY)

    # Assert
    assert result.status is CheckStatus.PASS


def test_a_dated_item_with_no_known_route_is_asked_about():
    # Arrange: in date today, but nobody knows how long it spends in transit.
    subject = device(expiry_date=expiring_in(400))

    # Act
    result = check_shelf_life(subject, facility(), intel(), None, today=TODAY)

    # Assert
    assert result.status is CheckStatus.NEEDS_INFO


def test_the_detail_tells_the_clinic_when_it_would_land():
    # Arrange
    subject = device(expiry_date=expiring_in(800))

    # Act
    result = check_shelf_life(subject, facility(), intel(), shipment(), today=TODAY)

    # Assert
    assert str(TRANSIT_DAYS) in result.detail
