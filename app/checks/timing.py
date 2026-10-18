"""Does the consignment still have useful life when it reaches the ward?

Shelf life is the one property that is spent by the journey itself. A box of
sterile circuits with four months on it is stock in a storeroom and waste on a
container that takes three, so this check judges the expiry date against the
arrival date, never against today.

No model is called here. Given the same dates this returns the same result.
"""

from __future__ import annotations

from datetime import date

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.result import CheckResult, CheckStatus
from app.domain.shipment import Shipment

# WHO's *Guidelines for medicine donations* asks for at least a year of shelf
# life remaining on arrival. The medical device guidance sets no equivalent
# number, so this borrows the medicines figure rather than inventing one, and
# a short-dated consignment is held for a decision rather than refused.
MIN_USABLE_DAYS_ON_ARRIVAL = 365

LABEL = "Shelf life on arrival"
NOTHING_DATED = "Nothing in this consignment carries an expiry date (donor)."
KEY = "shelf_life"


def check_shelf_life(
    device: DeviceFacts,
    facility: FacilityProfile,
    intel: SupplyIntel,
    shipment: Shipment | None = None,
    *,
    today: date | None = None,
) -> CheckResult:
    """Compare the expiry date with the date the consignment would land."""
    now = today or date.today()
    dated = device.expiry_date
    answered = device.dated_items

    # The donor's "nothing dated" is an answer, not a gap (design §2.2).
    if answered.known and answered.value is False:
        return CheckResult(
            key=KEY,
            label=LABEL,
            status=CheckStatus.PASS,
            detail=NOTHING_DATED,
            source=answered.source,
        )

    if not dated.known:
        return CheckResult(
            key=KEY,
            label=LABEL,
            status=CheckStatus.NEEDS_INFO,
            detail="No expiry date recorded for this consignment.",
            question="Does anything being sent carry an expiry date - sterile "
            "circuits, filters, reagents, batteries? If so, what is the earliest?",
            source=dated.source,
        )

    expiry = dated.value
    if expiry <= now:
        return CheckResult(
            key=KEY,
            label=LABEL,
            status=CheckStatus.FAIL,
            detail=f"Expired on {expiry}. It is out of date before it leaves.",
            fix="None. Send stock that is in date.",
            source=dated.source,
        )

    if shipment is None:
        return CheckResult(
            key=KEY,
            label=LABEL,
            status=CheckStatus.NEEDS_INFO,
            detail=f"In date until {expiry}, but the shipping route is not known, "
            "so how much life is left on arrival cannot be worked out.",
            question="Where does the consignment ship from? Transit and customs "
            "decide how much shelf life actually reaches the ward.",
            source=dated.source,
        )

    arrival = shipment.arrival_date(now)
    days_left = (expiry - arrival).days
    journey = f"About {shipment.transit_days} days in transit, landing around {arrival}."

    if days_left <= 0:
        return CheckResult(
            key=KEY,
            label=LABEL,
            status=CheckStatus.FAIL,
            detail=f"{journey} It expires on {expiry}, {abs(days_left)} days before "
            "it lands. The clinic would be unpacking waste.",
            fix="None. Send longer-dated stock, or ship it faster than freight.",
            source=dated.source,
        )

    if days_left < MIN_USABLE_DAYS_ON_ARRIVAL:
        return CheckResult(
            key=KEY,
            label=LABEL,
            status=CheckStatus.MITIGABLE,
            detail=f"{journey} That leaves {days_left} days of shelf life on "
            f"arrival, short of the {MIN_USABLE_DAYS_ON_ARRIVAL} days a donation "
            "should land with.",
            fix=f"Written confirmation the clinic can use it within {days_left} "
            "days of delivery, or send longer-dated stock.",
            source=dated.source,
        )

    return CheckResult(
        key=KEY,
        label=LABEL,
        status=CheckStatus.PASS,
        detail=f"{journey} It expires on {expiry}, leaving {days_left} days of "
        "shelf life on arrival.",
        source=dated.source,
    )
