"""Checks on whether the staff can actually read and run the thing."""

from __future__ import annotations

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.language import normalise_languages
from app.domain.requirement import Requirement, RequirementKind
from app.domain.result import CheckResult, CheckStatus

HIGH_COMPLEXITY = "high"


def check_manual_language(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    languages = device.manual_languages
    staff = normalise_languages(facility.staff_languages)

    if not languages.known:
        return CheckResult(
            key="manual",
            label="Manual language",
            status=CheckStatus.NEEDS_INFO,
            detail=f"Staff at {facility.name} read {', '.join(staff)}. "
            "The manual's language is not known.",
            question="What languages is the manual available in, and is one included?",
            source=languages.source,
        )

    manual = normalise_languages(languages.value)
    shared = set(manual) & set(staff)
    if shared:
        return CheckResult(
            key="manual",
            label="Manual language",
            status=CheckStatus.PASS,
            detail=f"The manual is available in {', '.join(sorted(shared))}.",
            source=languages.source,
        )

    wanted = staff[0] if staff else "the local language"
    return CheckResult(
        key="manual",
        label="Manual language",
        status=CheckStatus.MITIGABLE,
        detail=f"The manual is in {', '.join(manual)}, but staff read "
        f"{', '.join(staff)}.",
        fix=f"A service and user manual translated into {wanted}.",
        source=languages.source,
        requirement=Requirement(RequirementKind.MANUAL,
                                 f"A manual translated into {wanted}.", "manual"),
    )


def check_operation_and_repair(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    complexity = device.operating_complexity
    if not complexity.known:
        return CheckResult(
            key="training",
            label="Training and repair",
            status=CheckStatus.NEEDS_INFO,
            detail="How much skill the device needs is not known.",
            question="Does running this device need specialist training, and does "
            "servicing it need a biomedical technician?",
            source=complexity.source,
        )

    gaps = []
    if not facility.trained_operators:
        gaps.append("nobody trained to run it")
    if not facility.has_biomed_technician:
        gaps.append("no biomedical technician on site")

    if not gaps:
        return CheckResult(
            key="training",
            label="Training and repair",
            status=CheckStatus.PASS,
            detail=f"{facility.name} has trained operators and a biomedical technician.",
            source=complexity.source,
        )

    if str(complexity.value).lower() != HIGH_COMPLEXITY:
        return CheckResult(
            key="training",
            label="Training and repair",
            status=CheckStatus.PASS,
            detail=f"Simple enough to run with {' and '.join(gaps)}.",
            source=complexity.source,
        )

    return CheckResult(
        key="training",
        label="Training and repair",
        status=CheckStatus.MITIGABLE,
        detail=f"A high-skill device, and there is {' and '.join(gaps)}.",
        fix="Operator training on commissioning, and a service contract or a named "
        "regional technician.",
        source=complexity.source,
        requirement=Requirement(RequirementKind.TRAINING,
                                 "Operator training and a service contract.", "training"),
    )
