"""Checks for the supporting utilities a device needs from the building."""

from __future__ import annotations

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.provenance import Fact
from app.domain.requirement import Requirement, RequirementKind
from app.domain.result import CheckResult, CheckStatus


def _utility_check(
    kind: RequirementKind,
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

    return CheckResult(
        key=key,
        label=label,
        status=CheckStatus.MITIGABLE,
        detail=f"The device needs {label.lower()}, which {facility_name} does not have.",
        fix=fix,
        source=need.source,
        requirement=Requirement(kind, fix, key),
    )


def check_oxygen(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    return _utility_check(
        kind=RequirementKind.OXYGEN_SUPPLY,
        key="oxygen",
        label="Medical oxygen",
        need=device.needs_oxygen,
        available=facility.has_oxygen_supply,
        facility_name=facility.name,
        fix="A concentrator or a cylinder supply contract, funded for the device's life.",
        question="Does the device need a medical oxygen supply to operate?",
    )


def check_distilled_water(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    return _utility_check(
        kind=RequirementKind.WATER_SUPPLY,
        key="water",
        label="Distilled water",
        need=device.needs_distilled_water,
        available=facility.has_distilled_water,
        facility_name=facility.name,
        fix="A small still, or a standing order for distilled water.",
        question="Does the device need distilled or deionised water?",
    )


def check_compressed_air(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    return _utility_check(
        kind=RequirementKind.AIR_COMPRESSOR,
        key="air",
        label="Compressed air",
        need=device.needs_compressed_air,
        available=facility.has_compressed_air,
        facility_name=facility.name,
        fix="A medical air compressor sized for the device.",
        question="Does the device need a medical compressed air supply?",
    )
