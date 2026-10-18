"""Where the supply facts for one device in one country come from, in order.

The registry first, then what the nonprofit recorded, then unknown. The
model's memory is never one of the sources.
"""

from __future__ import annotations

from app.domain.device import DeviceFacts, SupplyIntel
from app.matching.pipeline import empty_intel


def supply_lookup(device: DeviceFacts, device_type: str, country: str) -> SupplyIntel:
    """The IntelLookup the routes use. States nothing until its sources are wired."""
    return empty_intel(device, device_type, country)
