"""Transit estimates: preparation, carriage and customs, kept separable.

The numbers are planning estimates, so these tests pin the shape of the
estimate rather than exact day counts a freight quote would replace.
"""

from __future__ import annotations

from datetime import date

from app.domain.provenance import Provenance
from app.domain.shipment import (
    CUSTOMS_CLEARANCE_DAYS,
    PREPARATION_DAYS,
    SEA_FREIGHT_THRESHOLD_KM,
    Shipment,
    estimate_shipment,
    estimate_transit_days,
)

A_SHORT_DOMESTIC_HOP_KM = 300.0
AN_OCEAN_AWAY_KM = 12000.0


def test_an_unknown_route_produces_no_estimate():
    assert estimate_transit_days(None) is None
    assert estimate_shipment(None) is None


def test_a_domestic_move_skips_customs():
    # Arrange / Act
    crossing = estimate_transit_days(A_SHORT_DOMESTIC_HOP_KM, crosses_border=True)
    domestic = estimate_transit_days(A_SHORT_DOMESTIC_HOP_KM, crosses_border=False)

    # Assert
    assert crossing - domestic == CUSTOMS_CLEARANCE_DAYS


def test_every_shipment_carries_the_preparation_time():
    # Arrange: the shortest possible journey still has to be crated and booked.
    days = estimate_transit_days(0.0, crosses_border=False)

    # Assert
    assert days == PREPARATION_DAYS


def test_a_longer_journey_never_takes_fewer_days():
    near = estimate_transit_days(A_SHORT_DOMESTIC_HOP_KM)
    far = estimate_transit_days(AN_OCEAN_AWAY_KM)

    assert far > near


def test_sea_freight_applies_beyond_the_threshold():
    # Arrange: either side of the point where nobody trucks a container.
    road = estimate_transit_days(SEA_FREIGHT_THRESHOLD_KM - 1, crosses_border=False)
    sea = estimate_transit_days(SEA_FREIGHT_THRESHOLD_KM, crosses_border=False)

    # Assert: the slower mode cannot arrive sooner over a longer distance.
    assert sea >= road


def test_a_negative_distance_is_rejected_rather_than_guessed():
    assert estimate_transit_days(-5.0) is None


def test_arrival_date_is_today_plus_the_transit():
    # Arrange
    leg = Shipment(transit_days=55)

    # Act
    landing = leg.arrival_date(date(2026, 1, 1))

    # Assert
    assert landing == date(2026, 2, 25)


def test_an_estimate_is_labelled_as_a_standard_default_not_a_quote():
    leg = estimate_shipment(AN_OCEAN_AWAY_KM)

    assert leg.source is Provenance.STANDARD_DEFAULT


def test_an_expiry_date_survives_the_round_trip_as_a_date():
    """A date stored as JSON must come back as a date, not a string.

    The shelf-life check subtracts it from the arrival date, so a string here
    would be a TypeError on a live verdict rather than a failing test.
    """
    # Arrange
    import json

    from app.data.serialize import device_from_dict, device_to_dict
    from app.domain.device import DeviceFacts
    from app.domain.provenance import fact

    expiry = date(2027, 3, 31)
    original = DeviceFacts(expiry_date=fact(expiry, Provenance.DONOR_STATED))

    # Act
    restored = device_from_dict(json.loads(json.dumps(device_to_dict(original))))

    # Assert
    assert restored.expiry_date.value == expiry
    assert isinstance(restored.expiry_date.value, date)
    assert restored.expiry_date.source is Provenance.DONOR_STATED
