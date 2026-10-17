"""Distance is arithmetic, and an unlocated point stays unlocated."""

import math

import pytest

from app.matching.distance import EARTH_RADIUS_KM, facility_point, haversine_km
from app.domain.marketplace import GeoPoint
from tests.factories import facility

LONDON = GeoPoint(country="United Kingdom", city="London", latitude=51.5074, longitude=-0.1278)
PARIS = GeoPoint(country="France", city="Paris", latitude=48.8566, longitude=2.3522)
LONDON_TO_PARIS_KM = 344.0
TOLERANCE = 0.02


def test_london_to_paris_is_about_344_km():
    # Arrange
    expected = LONDON_TO_PARIS_KM

    # Act
    measured = haversine_km(LONDON, PARIS)

    # Assert
    assert measured == pytest.approx(expected, rel=TOLERANCE)


def test_distance_is_symmetric():
    there = haversine_km(LONDON, PARIS)
    back = haversine_km(PARIS, LONDON)

    assert there == pytest.approx(back)


def test_a_point_is_zero_km_from_itself():
    assert haversine_km(LONDON, LONDON) == pytest.approx(0.0, abs=1e-9)


def test_antipodes_are_half_the_circumference_apart():
    north = GeoPoint(country="Nowhere", latitude=0.0, longitude=0.0)
    south = GeoPoint(country="Nowhere", latitude=0.0, longitude=180.0)

    measured = haversine_km(north, south)

    assert measured == pytest.approx(math.pi * EARTH_RADIUS_KM)


def test_distance_is_none_when_the_origin_has_no_coordinates():
    unlocated = GeoPoint(country="United Kingdom", city="London")

    assert haversine_km(unlocated, PARIS) is None


def test_distance_is_none_when_the_destination_has_no_coordinates():
    unlocated = GeoPoint(country="France", city="Paris", latitude=48.8566)

    assert haversine_km(LONDON, unlocated) is None


def test_distance_is_none_when_either_point_is_missing_entirely():
    assert haversine_km(None, PARIS) is None
    assert haversine_km(LONDON, None) is None


def test_an_impossible_latitude_is_rejected_rather_than_measured():
    broken = GeoPoint(country="Nowhere", latitude=91.0, longitude=0.0)

    with pytest.raises(ValueError, match="Latitude"):
        haversine_km(broken, PARIS)


def test_an_impossible_longitude_is_rejected_rather_than_measured():
    broken = GeoPoint(country="Nowhere", latitude=0.0, longitude=181.0)

    with pytest.raises(ValueError, match="Longitude"):
        haversine_km(LONDON, broken)


def test_facility_point_carries_the_clinic_coordinates():
    site = facility(latitude=51.5074, longitude=-0.1278, country="United Kingdom")

    point = facility_point(site)

    assert point.located
    assert point.country == "United Kingdom"
    assert haversine_km(point, PARIS) == pytest.approx(LONDON_TO_PARIS_KM, rel=TOLERANCE)


def test_facility_point_is_unlocated_when_the_clinic_has_no_coordinates():
    point = facility_point(facility())

    assert not point.located
