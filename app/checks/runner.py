"""Runs every check and rolls the results into one verdict.

No model is called here. Given the same facts this returns the same verdict
every time, which is the point: the inputs can be wrong, the judgement cannot
be invented.
"""

from __future__ import annotations

from typing import Callable

from app.checks.documentation import check_manual_language, check_operation_and_repair
from app.checks.environment import (
    check_grid_reliability,
    check_humidity,
    check_temperature,
)
from app.checks.power import check_frequency, check_voltage
from app.checks.supply import check_consumables, check_spare_parts, check_support_window
from app.checks.utilities import check_compressed_air, check_distilled_water, check_oxygen
from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict

Check = Callable[[DeviceFacts, FacilityProfile, SupplyIntel], CheckResult]

ALL_CHECKS: tuple[Check, ...] = (
    check_frequency,
    check_voltage,
    check_temperature,
    check_humidity,
    check_grid_reliability,
    check_oxygen,
    check_distilled_water,
    check_compressed_air,
    check_consumables,
    check_spare_parts,
    check_support_window,
    check_manual_language,
    check_operation_and_repair,
)

STATUS_ORDER = {
    CheckStatus.FAIL: 0,
    CheckStatus.MITIGABLE: 1,
    CheckStatus.NEEDS_INFO: 2,
    CheckStatus.PASS: 3,
}


def run_checks(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> tuple[CheckResult, ...]:
    """Run every check against one device and one facility."""
    prepared = device.with_defaults()
    results = tuple(check(prepared, facility, intel) for check in ALL_CHECKS)
    return tuple(sorted(results, key=lambda r: STATUS_ORDER[r.status]))


def build_verdict(results: tuple[CheckResult, ...]) -> Verdict:
    """Roll results up. One blocking failure outranks everything else."""
    if any(r.blocking for r in results):
        outcome = Outcome.WONT_WORK
    elif any(r.status is CheckStatus.MITIGABLE for r in results):
        outcome = Outcome.WORKS_IF
    else:
        outcome = Outcome.WORKS

    requirements = tuple(
        r.fix for r in results if r.status is CheckStatus.MITIGABLE and r.fix
    )
    questions = tuple(
        r.question for r in results if r.status is CheckStatus.NEEDS_INFO and r.question
    )
    return Verdict(
        outcome=outcome,
        results=results,
        requirements=requirements,
        questions=questions,
    )


def check_fit(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> Verdict:
    """The whole deterministic half of the product, in one call."""
    return build_verdict(run_checks(device, facility, intel))
