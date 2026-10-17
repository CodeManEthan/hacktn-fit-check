"""How long a consignment takes to reach the clinic, and when it lands.

Transit is a property of the route, not of the device or the facility, so it
is carried separately from both. Every number here is a planning estimate
labelled STANDARD_DEFAULT: a real freight quote replaces it, and until one
arrives the estimate is honest about being one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.provenance import Provenance

# Decommission the unit, crate it, book the freight. Donated capital equipment
# does not leave the building the week it is offered.
PREPARATION_DAYS = 14

# Importation and port clearance. WHO's donation guidance asks donors to agree
# the importation framework in advance precisely because this step strands
# consignments; three weeks is a mild assumption, not a worst case.
CUSTOMS_CLEARANCE_DAYS = 21

# Road haulage for a regional move, sea freight once the leg is long enough
# that nobody is trucking a container that far. A ship covers more ground per
# day than a lorry, but it is not waiting at the donor's gate: consolidation,
# sailing schedules and discharge are the reason sea freight is slower overall
# despite the better daily rate.
ROAD_KM_PER_DAY = 500
SEA_KM_PER_DAY = 800
SEA_PORT_HANDLING_DAYS = 18
SEA_FREIGHT_THRESHOLD_KM = 1500


@dataclass(frozen=True)
class Shipment:
    """The estimated journey from a donor's loading bay to a clinic's door."""

    transit_days: int
    source: Provenance = Provenance.STANDARD_DEFAULT
    crosses_border: bool = True

    def arrival_date(self, today: date | None = None) -> date:
        """The date the consignment is expected to land."""
        return (today or date.today()) + timedelta(days=self.transit_days)


def estimate_transit_days(
    distance_km: float | None, *, crosses_border: bool = True
) -> int | None:
    """Days from offer accepted to equipment delivered, or None without a route.

    Preparation always applies. Customs only applies across a border. The
    carriage term is whichever mode the distance implies.
    """
    if distance_km is None or distance_km < 0:
        return None

    by_sea = distance_km >= SEA_FREIGHT_THRESHOLD_KM
    per_day = SEA_KM_PER_DAY if by_sea else ROAD_KM_PER_DAY
    carriage = round(distance_km / per_day) + (SEA_PORT_HANDLING_DAYS if by_sea else 0)
    clearance = CUSTOMS_CLEARANCE_DAYS if crosses_border else 0
    return PREPARATION_DAYS + carriage + clearance


def estimate_shipment(
    distance_km: float | None, *, crosses_border: bool = True
) -> Shipment | None:
    """A Shipment for this route, or None when the route is unknown."""
    days = estimate_transit_days(distance_km, crosses_border=crosses_border)
    if days is None:
        return None
    return Shipment(transit_days=days, crosses_border=crosses_border)
