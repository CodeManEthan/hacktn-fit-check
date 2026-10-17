"""Does a public map registry know this clinic by name, near where it claims?

Backed by OpenStreetMap's Nominatim service, which is free and needs no key.
Like app/reference/device_registry.py, this module never raises and never
blocks: every failure path returns no candidates, so a clinic registering on a
dead link sees "not checked", never a crash and never a false rejection.

What this is NOT: a verdict. Testing against the seeded clinics showed exactly
why. Searching Uganda for the full name "Kayunga District Hospital" returns
nothing, while the looser "Kayunga Hospital" returns the real facility - and
also "Kayunga Hospital Nursery School". A registry lookup that auto-approved
on a name match would certify a nursery school as a hospital. So this returns
candidates with their distance from the stated point and lets a person decide.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from math import asin, cos, radians, sin, sqrt

from app.domain.facility_trust import RegistryMatch

LOG = logging.getLogger(__name__)

ENDPOINT = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy requires a genuine identifying User-Agent.
USER_AGENT = "fit-check/0.1 (medical equipment donation screening)"
TIMEOUT_SECONDS = 12
MAX_CANDIDATES = 5

EARTH_RADIUS_KM = 6371.0

# Beyond this, a same-named facility is a different place, not corroboration.
CORROBORATION_RADIUS_KM = 25.0

# Words that make a name a hospital's rather than something standing next door.
HEALTH_WORDS = ("hospital", "clinic", "health", "medical", "dispensary", "centre", "center")


def _km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def _fetch(params: dict[str, str]) -> list[dict]:
    """One request. Returns an empty list on any failure, and says why in the log."""
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        LOG.warning("facility registry lookup failed: %s", exc)
        return []
    return payload if isinstance(payload, list) else []


def _search_terms(name: str) -> list[str]:
    """The full name first, then a shortened form.

    Official names carry qualifiers ("District", "Primary Health Centre") that
    the map data often omits, so a single exact query misses real hospitals.
    """
    cleaned = " ".join(name.split())
    terms = [cleaned]
    words = cleaned.split()
    if len(words) > 2:
        head = words[0]
        for word in words[1:]:
            if word.lower() in HEALTH_WORDS:
                terms.append(f"{head} {word}")
                break
    return terms


def find_candidates(
    name: str, country_code: str = "", latitude: float | None = None,
    longitude: float | None = None,
) -> tuple[RegistryMatch, ...]:
    """Candidate registry entries for this facility name, nearest first.

    Returns an empty tuple when nothing matched or the service was unreachable.
    The caller cannot tell those apart on purpose: neither is evidence against
    the clinic, and treating a timeout as a red flag would punish clinics for
    this service's downtime.
    """
    if not name.strip():
        return ()

    seen: dict[str, RegistryMatch] = {}
    for term in _search_terms(name):
        params = {"q": term, "format": "jsonv2", "limit": str(MAX_CANDIDATES),
                  "addressdetails": "1"}
        if country_code:
            params["countrycodes"] = country_code.lower()

        for row in _fetch(params):
            try:
                found_lat = float(row["lat"])
                found_lon = float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue

            display = str(row.get("display_name", ""))
            if display in seen:
                continue

            distance = None
            if latitude is not None and longitude is not None:
                distance = _km_between(latitude, longitude, found_lat, found_lon)

            seen[display] = RegistryMatch(
                name=str(row.get("name") or display.split(",")[0]),
                display_name=display,
                latitude=found_lat,
                longitude=found_lon,
                category=str(row.get("category", "")),
                kind=str(row.get("type", "")),
                distance_km=distance,
            )
        if seen:
            break

    ordered = sorted(
        seen.values(),
        key=lambda m: (m.distance_km is None, m.distance_km or 0.0),
    )
    return tuple(ordered[:MAX_CANDIDATES])


def corroborates(match: RegistryMatch) -> bool:
    """True when this candidate actually supports the clinic's claim.

    It must be a health facility by the registry's own classification and be
    close enough to the stated location to be the same place. A name alone is
    never enough: that is what would certify the nursery school.
    """
    if not match.is_health_facility:
        return False
    if match.distance_km is None:
        return False
    return match.distance_km <= CORROBORATION_RADIUS_KM
