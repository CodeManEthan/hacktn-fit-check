"""Checks that depend on what can be bought and serviced in the country.

These are the only checks where the model's world knowledge decides the
outcome. The arithmetic checks never call a model.
"""

from __future__ import annotations

from datetime import date

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.result import CheckResult, CheckStatus

# WHO Annex 1 asks donors to confirm the manufacturer or distributor will
# support the device for at least three years from the date of purchase.
MIN_SUPPORT_YEARS = 3
AGEING_YEARS = 10
END_OF_LIFE_YEARS = 15
CONSUMABLE_YEARS_TO_SHIP = 2


def check_consumables(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    items = device.consumables
    if not items.known:
        return CheckResult(
            key="consumables",
            label="Consumables",
            status=CheckStatus.NEEDS_INFO,
            detail="Not known what this device consumes in normal use.",
            question="What does the device consume in use - reagents, cartridges, "
            "circuits, electrodes, filters?",
            source=items.source,
        )

    listed = tuple(items.value)
    if not listed:
        return CheckResult(
            key="consumables",
            label="Consumables",
            status=CheckStatus.PASS,
            detail="The device uses no consumables.",
            source=items.source,
        )

    named = ", ".join(listed)
    available = intel.consumables_available
    if not available.known:
        return CheckResult(
            key="consumables",
            label="Consumables",
            status=CheckStatus.NEEDS_INFO,
            detail=f"Needs {named}. Whether these sell in {intel.country} is not known.",
            question=f"Is there a supplier for {named} in {intel.country}?",
            source=available.source,
        )

    if available.value:
        return CheckResult(
            key="consumables",
            label="Consumables",
            status=CheckStatus.PASS,
            detail=f"Needs {named}, and these are sold in {intel.country}.",
            source=available.source,
        )

    return CheckResult(
        key="consumables",
        label="Consumables",
        status=CheckStatus.MITIGABLE,
        detail=f"Needs {named}, which are not sold in {intel.country}. "
        "The device works until the first box runs out, then stops.",
        fix=f"{CONSUMABLE_YEARS_TO_SHIP} years of {named}, shipped with the device, "
        "and a named supplier for after that.",
        source=available.source,
    )


def check_spare_parts(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    available = intel.spare_parts_available
    if not available.known:
        return CheckResult(
            key="spare_parts",
            label="Spare parts",
            status=CheckStatus.NEEDS_INFO,
            detail=f"Whether parts for {device.label} can be sourced in "
            f"{intel.country} is not known.",
            question=f"Can spare parts for {device.label} be bought in {intel.country}?",
            source=available.source,
        )

    if available.value:
        return CheckResult(
            key="spare_parts",
            label="Spare parts",
            status=CheckStatus.PASS,
            detail=f"Parts can be sourced in {intel.country}.",
            source=available.source,
        )

    return CheckResult(
        key="spare_parts",
        label="Spare parts",
        status=CheckStatus.MITIGABLE,
        detail=f"Parts are not sold in {intel.country}. One failed board and the "
        "device becomes furniture.",
        fix="A spare parts kit for the usual failures, and a named service contact.",
        source=available.source,
    )


def check_support_window(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    supported = intel.manufacturer_supported
    year = device.year_of_manufacture

    if supported.known and supported.value is False:
        return CheckResult(
            key="support",
            label="Manufacturer support",
            status=CheckStatus.FAIL,
            detail=f"{device.label} is past manufacturer support. No service, no "
            "software, no parts at any price.",
            fix="None. WHO asks for at least "
            f"{MIN_SUPPORT_YEARS} years of support from the date of transfer.",
            source=supported.source,
        )

    if not year.known:
        return CheckResult(
            key="support",
            label="Age and support",
            status=CheckStatus.NEEDS_INFO,
            detail="The device's age is not known.",
            question="What year was the device made? The nameplate usually prints it "
            "next to the serial number.",
            source=year.source,
        )

    age = date.today().year - int(year.value)
    if age >= END_OF_LIFE_YEARS:
        return CheckResult(
            key="support",
            label="Age and support",
            status=CheckStatus.FAIL,
            detail=f"Made in {year.value}, so it is {age} years old. Too little "
            "working life left to justify the shipping.",
            fix="None. Send a younger device.",
            source=year.source,
        )

    if age >= AGEING_YEARS:
        return CheckResult(
            key="support",
            label="Age and support",
            status=CheckStatus.MITIGABLE,
            detail=f"Made in {year.value}, so it is {age} years old.",
            fix=f"Written confirmation that the maker or its distributor will support "
            f"it for {MIN_SUPPORT_YEARS} more years.",
            source=year.source,
        )

    return CheckResult(
        key="support",
        label="Age and support",
        status=CheckStatus.PASS,
        detail=f"Made in {year.value}, so it is {age} years old with working life left.",
        source=year.source,
    )
