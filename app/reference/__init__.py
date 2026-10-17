"""Reference data: facts about countries and device classes, not about one device.

Everything here is a lookup over data committed to the repo or fetched from a
public registry. A miss returns None rather than a guess, because a donation
decision made on a guessed fact is worse than one made on a known gap.
"""

from app.reference.country_power import (
    CountryPower,
    CountryPowerDataError,
    country_centroid,
    lookup,
)
from app.reference.device_class import (
    DeviceClass,
    DeviceClassDataError,
    lookup_class,
)
from app.reference.device_registry import (
    DeviceRegistryRecord,
    is_still_supported,
    lookup_model,
)

__all__ = [
    "CountryPower",
    "CountryPowerDataError",
    "DeviceClass",
    "DeviceClassDataError",
    "DeviceRegistryRecord",
    "country_centroid",
    "is_still_supported",
    "lookup",
    "lookup_class",
    "lookup_model",
]
