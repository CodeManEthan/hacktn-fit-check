"""Climate and grid-stability checks against the facility's stated conditions."""

from __future__ import annotations

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.provenance import Provenance
from app.domain.requirement import Requirement, RequirementKind
from app.domain.result import CheckResult, CheckStatus


def check_temperature(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    rated = device.temp_max_c
    room = facility.effective_indoor_temp_max_c

    if not rated.known:
        return CheckResult(
            key="temperature",
            label="Operating temperature",
            status=CheckStatus.NEEDS_INFO,
            detail=f"Rooms at {facility.name} reach {room:g} C.",
            question="What operating temperature range does the manual give?",
            source=rated.source,
        )

    if room <= rated.value:
        return CheckResult(
            key="temperature",
            label="Operating temperature",
            status=CheckStatus.PASS,
            detail=f"Rated to {rated.value:g} C, and rooms reach {room:g} C.",
            source=rated.source,
        )

    if facility.has_air_conditioning:
        return CheckResult(
            key="temperature",
            label="Operating temperature",
            status=CheckStatus.FAIL,
            detail=f"Rated to {rated.value:g} C, but rooms reach {room:g} C even "
            "with the air conditioning running.",
            fix="No practical fix at this facility. Send a device rated for the heat.",
            source=rated.source,
        )

    # Cooling a room is building works, not an add-on (design §2.4): nothing
    # ships in the crate that fixes it, so it fails and says what is missing.
    return CheckResult(
        key="temperature",
        label="Operating temperature",
        status=CheckStatus.FAIL,
        detail=f"Rated to {rated.value:g} C, but rooms reach {room:g} C. It needs "
        f"air conditioning the ward at {facility.name} does not have.",
        fix=f"{facility.name} would have to air-condition the room it lives in, "
        "or be sent a device rated for the heat.",
        source=rated.source,
    )


def check_humidity(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    rated = device.humidity_max_pct
    if not rated.known:
        return CheckResult(
            key="humidity",
            label="Humidity",
            status=CheckStatus.NEEDS_INFO,
            detail=f"Humidity at {facility.name} reaches {facility.humidity_max_pct:g} %.",
            question="What humidity range does the manual give?",
            source=rated.source,
        )

    if facility.humidity_max_pct <= rated.value:
        return CheckResult(
            key="humidity",
            label="Humidity",
            status=CheckStatus.PASS,
            detail=f"Rated to {rated.value:g} % and the site reaches "
            f"{facility.humidity_max_pct:g} %.",
            source=rated.source,
        )

    return CheckResult(
        key="humidity",
        label="Humidity",
        status=CheckStatus.MITIGABLE,
        detail=f"Rated to {rated.value:g} % but the site reaches "
        f"{facility.humidity_max_pct:g} %. Boards corrode.",
        fix="A dehumidifier, or a sealed and conditioned room.",
        source=rated.source,
        requirement=Requirement(RequirementKind.DEHUMIDIFIER,
                                 "A dehumidifier for the room.", "humidity"),
    )


def check_grid_reliability(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    if not facility.power_is_unstable:
        return CheckResult(
            key="grid",
            label="Grid reliability",
            status=CheckStatus.PASS,
            detail=f"{facility.outages_per_week} outages a week, with backup in place.",
            source=Provenance.FACILITY_STATED,
        )

    protection = []
    if not facility.has_voltage_stabilizer:
        protection.append("A voltage stabiliser")
    if not facility.has_generator:
        protection.append("a UPS sized to ride through an outage")

    return CheckResult(
        key="grid",
        label="Grid reliability",
        status=CheckStatus.MITIGABLE,
        detail=f"{facility.outages_per_week} outages a week. Switching transients "
        "kill power supplies in equipment that assumes a steady feed.",
        fix=" and ".join(protection) if protection else "Surge protection",
        source=Provenance.FACILITY_STATED,
        requirement=Requirement(
            RequirementKind.BACKUP_POWER if not facility.has_generator else RequirementKind.STABILISER,
            " and ".join(protection) if protection else "Surge protection", "grid"),
    )
