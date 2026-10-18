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
    """Can the staff read the manual? Manuals never fail ([F9], design §5.1).

    A shared language passes. Otherwise Fit-Check builds a handover packet
    from the manufacturer's manual, so the check is MITIGABLE with the MANUAL
    requirement, both when the languages differ and when the languages are
    unknown but a manufacturer document is on file. Only with no document at
    all does the check ask.
    """
    languages = device.manual_languages
    staff = normalise_languages(facility.staff_languages)
    staff_text = ", ".join(staff) if staff else "the local language"
    wanted = staff[0] if staff else "the local language"

    if not languages.known:
        documents = _documents(device)
        if not documents:
            return CheckResult(
                key="manual",
                label="Manual language",
                status=CheckStatus.NEEDS_INFO,
                detail=f"Staff at {facility.name} read {staff_text}. "
                "No manual could be found and the manual's language is not known.",
                question="What languages is the manual available in, and is one included?",
                source=languages.source,
            )
        title = str(documents[0].get("title") or "the manufacturer's manual")
        return _packet_result(
            f"The manual's language is not on file; staff read {staff_text}. "
            f"Fit-Check generates a handover packet in {wanted} from the "
            f"manufacturer's manual ({title}).",
            wanted, device.documents.source,
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

    manual_text = ", ".join(manual) if manual else "no stated language"
    return _packet_result(
        f"The manual is in {manual_text}; staff read {staff_text}. Fit-Check "
        f"generates a handover packet in {wanted} from the manufacturer's manual.",
        wanted, languages.source,
    )


def _documents(device: DeviceFacts) -> list:
    documents = device.documents
    if not documents.known or not isinstance(documents.value, (list, tuple)):
        return []
    return [d for d in documents.value if isinstance(d, dict)]


def _packet_result(detail: str, wanted: str, source) -> CheckResult:
    return CheckResult(
        key="manual",
        label="Manual language",
        status=CheckStatus.MITIGABLE,
        detail=detail,
        fix=f"A handover packet in {wanted}, built from the manufacturer's manual.",
        source=source,
        requirement=Requirement(
            RequirementKind.MANUAL,
            f"A handover packet in {wanted}, built from the manufacturer's manual.",
            "manual",
        ),
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
