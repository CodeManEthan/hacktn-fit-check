"""Checks for the supporting utilities a device needs from the building."""

from __future__ import annotations

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.provenance import Fact
from app.domain.result import CheckResult, CheckStatus


def _utility_check(
    key: str,
    label: str,
    need: Fact,
    available: bool,
    facility_name: str,
    fix: str,
    question: str,
) -> CheckResult:
    if not need.known:
        return CheckResult(
            key=key,
            label=label,
            status=CheckStatus.NEEDS_INFO,
            detail=f"Not known whether the device needs {label.lower()}.",
            question=question,
            source=need.source,
        )

    if not need.value:
        return CheckResult(
            key=key,
            label=label,
            status=CheckStatus.PASS,
            detail=f"The device does not need {label.lower()}.",
            source=need.source,
        )

    if available:
        return CheckResult(
            key=key,
            label=label,
            status=CheckStatus.PASS,
            detail=f"Needed, and {facility_name} has it.",
            source=need.source,
        )

    # A supply the building lacks is site works, not an add-on (design §2.4):
    # it fails, the fix says what the clinic would have to put in, and
    # nothing searches the inventory for it.
    return CheckResult(
        key=key,
        label=label,
        status=CheckStatus.FAIL,
        detail=f"The device needs {label.lower()}, which {facility_name} does not have.",
        fix=fix.format(facility=facility_name),
        source=need.source,
    )


def check_oxygen(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    return _utility_check(
        key="oxygen",
        label="Medical oxygen",
        need=device.needs_oxygen,
        available=facility.has_oxygen_supply,
        facility_name=facility.name,
        fix="{facility} would need a medical oxygen supply of its own (a plant, or a "
        "cylinder contract funded for the device's life) before this could run.",
        question="Does the device need a medical oxygen supply to operate?",
    )


def check_distilled_water(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    return _utility_check(
        key="water",
        label="Distilled water",
        need=device.needs_distilled_water,
        available=facility.has_distilled_water,
        facility_name=facility.name,
        fix="{facility} would need a distilled water supply (a still, or a standing "
        "order) before this could run.",
        question="Does the device need distilled or deionised water?",
    )


def check_compressed_air(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    return _utility_check(
        key="air",
        label="Compressed air",
        need=device.needs_compressed_air,
        available=facility.has_compressed_air,
        facility_name=facility.name,
        fix="{facility} would need a piped or plant medical air supply sized for the "
        "device before this could run.",
        question="Does the device need a medical compressed air supply?",
    )
