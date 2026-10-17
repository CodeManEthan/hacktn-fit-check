"""How far apart two points are, in kilometres.

Pure arithmetic: no model, no network, no geocoding service. Distance is the
only input allocation is allowed to optimise on, and it is a formula, so it
is computed here and nowhere else.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from app.domain.facility import FacilityProfile
from app.domain.marketplace import GeoPoint

# Mean Earth radius (IUGG), in kilometres.
EARTH_RADIUS_KM = 6371.0088

MAX_LATITUDE_DEG = 90.0
MAX_LONGITUDE_DEG = 180.0


def facility_point(facility: FacilityProfile) -> GeoPoint:
    """A clinic as a GeoPoint, so both sides of a distance have one type."""
    return GeoPoint(
        country=facility.country,
        city=facility.name,
        latitude=facility.latitude,
        longitude=facility.longitude,
    )


def haversine_km(origin: GeoPoint | None, destination: GeoPoint | None) -> float | None:
    """Great-circle kilometres, or None when either side lacks coordinates.

    None means "not known". It is never zero and never an estimate: a donor
    whose address was never geocoded must not be mistaken for the nearest one.
    """
    if origin is None or destination is None:
        return None
    if not (origin.located and destination.located):
        return None

    _reject_impossible(origin)
    _reject_impossible(destination)

    from_lat, from_lon = radians(origin.latitude), radians(origin.longitude)
    to_lat, to_lon = radians(destination.latitude), radians(destination.longitude)

    half_chord = (
        sin((to_lat - from_lat) / 2) ** 2
        + cos(from_lat) * cos(to_lat) * sin((to_lon - from_lon) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * asin(sqrt(half_chord))


def _reject_impossible(point: GeoPoint) -> None:
    """Fail loudly on coordinates no point on Earth has."""
    if not -MAX_LATITUDE_DEG <= point.latitude <= MAX_LATITUDE_DEG:
        raise ValueError(
            f"Latitude {point.latitude} for {point.city or point.country} is outside "
            f"-{MAX_LATITUDE_DEG}..{MAX_LATITUDE_DEG}."
        )
    if not -MAX_LONGITUDE_DEG <= point.longitude <= MAX_LONGITUDE_DEG:
        raise ValueError(
            f"Longitude {point.longitude} for {point.city or point.country} is outside "
            f"-{MAX_LONGITUDE_DEG}..{MAX_LONGITUDE_DEG}."
        )
