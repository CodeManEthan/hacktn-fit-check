"""An answer the nonprofit recorded about supporting one device in one country.

The supply checks ask four questions a device's documents cannot answer: are
its consumables and spare parts sold in the country, is it sold there at all,
and does the manufacturer still support it. Someone who knows the country
writes the answer down once; every later screening reads it.

Each answer is a bool or None. None is a recorded "don't know", which leaves
the fact unknown rather than turning it into a no.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: The model value meaning "any model of this device type".
ANY_MODEL = ""


@dataclass(frozen=True)
class SupplyAnswer:
    device_type: str
    country: str
    model: str = ANY_MODEL
    consumables_available: bool | None = None
    spare_parts_available: bool | None = None
    sold_locally: bool | None = None
    manufacturer_supported: bool | None = None
    recorded_by: str = ""
    note: str = ""
    recorded_at: datetime | None = None
    id: int | None = None

    @property
    def for_any_model(self) -> bool:
        return self.model == ANY_MODEL
