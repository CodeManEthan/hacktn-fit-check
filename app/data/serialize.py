"""Convert domain objects to and from plain dicts for storage and templates."""

from __future__ import annotations

from dataclasses import fields
from datetime import date
from typing import Any

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.provenance import UNKNOWN, Fact, Provenance, fact
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict

_SOURCE_BY_VALUE = {p.value: p for p in Provenance}


# JSON has no date type. A date is written as {"date": "2027-03-31"} so it can
# be told apart from a string that merely looks like one, and comes back as a
# date object: the shelf-life check does arithmetic on it, and a string there
# would fail at request time rather than in a test.
_DATE_KEY = "date"


def _encode(value: Any) -> Any:
    return {_DATE_KEY: value.isoformat()} if isinstance(value, date) else value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {_DATE_KEY}:
        return date.fromisoformat(value[_DATE_KEY])
    return value


def fact_to_dict(value: Fact) -> dict[str, Any]:
    return {
        "value": _encode(value.value),
        "source": value.source.value,
        "note": value.note,
    }


def fact_from_dict(payload: dict[str, Any] | None) -> Fact:
    if not payload or payload.get("value") is None:
        return UNKNOWN
    source = _SOURCE_BY_VALUE.get(payload.get("source", ""), Provenance.UNKNOWN)
    return fact(_decode(payload["value"]), source, payload.get("note", ""))


def device_to_dict(device: DeviceFacts) -> dict[str, Any]:
    out: dict[str, Any] = {"raw_input": device.raw_input}
    for f in fields(device):
        if f.name == "raw_input":
            continue
        out[f.name] = fact_to_dict(getattr(device, f.name))
    return out


def device_from_dict(payload: dict[str, Any]) -> DeviceFacts:
    values = {
        f.name: fact_from_dict(payload.get(f.name))
        for f in fields(DeviceFacts)
        if f.name != "raw_input"
    }
    return DeviceFacts(raw_input=payload.get("raw_input", ""), **values)


def intel_to_dict(intel: SupplyIntel) -> dict[str, Any]:
    return {
        "country": intel.country,
        "consumables_available": fact_to_dict(intel.consumables_available),
        "spare_parts_available": fact_to_dict(intel.spare_parts_available),
        "manufacturer_supported": fact_to_dict(intel.manufacturer_supported),
        "reasoning": intel.reasoning,
    }


def intel_from_dict(payload: dict[str, Any]) -> SupplyIntel:
    return SupplyIntel(
        country=payload.get("country", ""),
        consumables_available=fact_from_dict(payload.get("consumables_available")),
        spare_parts_available=fact_from_dict(payload.get("spare_parts_available")),
        manufacturer_supported=fact_from_dict(payload.get("manufacturer_supported")),
        reasoning=payload.get("reasoning", ""),
    )


def result_to_dict(result: CheckResult) -> dict[str, Any]:
    return {
        "key": result.key,
        "label": result.label,
        "status": result.status.value,
        "detail": result.detail,
        "fix": result.fix,
        "source": result.source.value,
        "question": result.question,
    }


def result_from_dict(payload: dict[str, Any]) -> CheckResult:
    return CheckResult(
        key=payload["key"],
        label=payload["label"],
        status=CheckStatus(payload["status"]),
        detail=payload.get("detail", ""),
        fix=payload.get("fix", ""),
        source=_SOURCE_BY_VALUE.get(payload.get("source", ""), Provenance.UNKNOWN),
        question=payload.get("question", ""),
    )


def verdict_to_dict(verdict: Verdict) -> dict[str, Any]:
    return {
        "outcome": verdict.outcome.value,
        "results": [result_to_dict(r) for r in verdict.results],
        "requirements": list(verdict.requirements),
        "questions": list(verdict.questions),
    }


def verdict_from_dict(payload: dict[str, Any]) -> Verdict:
    return Verdict(
        outcome=Outcome(payload["outcome"]),
        results=tuple(result_from_dict(r) for r in payload.get("results", [])),
        requirements=tuple(payload.get("requirements", [])),
        questions=tuple(payload.get("questions", [])),
    )
