"""Can the device get into the country at all? The import and registration check.

Reads the country's row from data/import_rules.json through
app/reference/import_rules.py. Three rules, in this order (design §2.5):

- Country not on file → NEEDS_INFO. The table is filled from documents; a
  country nobody has read the rules for is a question, never a pass.
- Remaining life: END_OF_LIFE_YEARS (the one working-life basis every device
  gets, from app/checks/supply.py) less the device's age, against the
  country's minimum. Short → FAIL. An unknown year skips this rule here,
  because the support check already asks for the year.
- Label language: no label-language fact exists, and a manufacturer prints
  its labels in the languages of its manual, so `manual_languages` stands in
  for the label ([R26]). No shared language → FAIL. Manual languages unknown
  but a document known → skipped (the handover packet reads the language
  off the document). Both unknown → NEEDS_INFO.

A life FAIL wins over anything the label rule says. When neither rule fails,
the check passes and lists the paperwork; the paperwork never fails and is
never a Requirement. No model is called here.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.checks.supply import END_OF_LIFE_YEARS
from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.language import normalise_languages
from app.domain.provenance import Provenance
from app.domain.result import CheckResult, CheckStatus
from app.reference.import_rules import rule_for

KEY = "import"
LABEL = "Import and registration"
LABEL_PROXY = "manual languages used as the label proxy"
# The same words as the manual check's question, so a verdict that asks both
# checks about the manual's language asks once (build_verdict drops repeats).
MANUAL_QUESTION = "What languages is the manual available in, and is one included?"


def check_import_rules(
    device: DeviceFacts,
    facility: FacilityProfile,
    intel: SupplyIntel,
    *,
    today: date | None = None,
) -> CheckResult:
    country = facility.country
    rule = rule_for(country)
    if rule is None:
        return CheckResult(
            key=KEY,
            label=LABEL,
            status=CheckStatus.NEEDS_INFO,
            detail=f"No import and registration rules for {country} are on file.",
            question=f"What are {country}'s import and registration rules for donated "
            "medical equipment?",
        )

    cited = _citation(rule)
    life = _life_failure(device, country, rule, cited, today or date.today())
    if life is not None:
        return life

    labels = normalise_languages(rule.get("label_languages") or ())
    label_note = ""
    label_source = Provenance.UNKNOWN
    if labels:
        manual = device.manual_languages
        if manual.known:
            in_manual = normalise_languages(manual.value)
            shared = sorted(set(in_manual) & set(labels))
            if not shared:
                return CheckResult(
                    key=KEY,
                    label=LABEL,
                    status=CheckStatus.FAIL,
                    detail=f"{country} requires labels in {_either(labels)}; the manual is "
                    f"in {', '.join(in_manual) or 'no stated language'} ({LABEL_PROXY}). "
                    f"{_quote(rule, 'label_languages')}{cited}",
                    fix=f"Only a unit labelled in {_either(labels)} can be imported. A label "
                    "is on the device itself: the handover packet translates the manual, "
                    "not the label.",
                    source=manual.source,
                )
            label_note = f" Labels: {', '.join(shared)} ({LABEL_PROXY})."
            label_source = manual.source
        elif _has_document(device):
            label_note = (" Label language is read from the manufacturer's document in "
                          "the handover packet.")
            label_source = device.documents.source
        else:
            return CheckResult(
                key=KEY,
                label=LABEL,
                status=CheckStatus.NEEDS_INFO,
                detail=f"{country} requires labels in {_either(labels)}, and the manual's "
                f"language is not known ({LABEL_PROXY}).{cited}",
                question=MANUAL_QUESTION,
                source=manual.source,
            )

    paperwork = [str(item) for item in rule.get("documents_required") or ()]
    papers = f" Paperwork to send: {'; '.join(paperwork)}." if paperwork else ""
    return CheckResult(
        key=KEY,
        label=LABEL,
        status=CheckStatus.PASS,
        detail=f"{country}'s rules are on file.{label_note}{papers}{cited}",
        source=label_source,
    )


def _life_failure(
    device: DeviceFacts, country: str, rule: dict[str, Any], cited: str, today: date
) -> CheckResult | None:
    minimum = rule.get("min_remaining_life_years")
    year = device.year_of_manufacture
    if minimum is None or not year.known:
        return None
    age = today.year - int(year.value)
    left = END_OF_LIFE_YEARS - age
    if left >= int(minimum):
        return None
    return CheckResult(
        key=KEY,
        label=LABEL,
        status=CheckStatus.FAIL,
        detail=f"Made in {year.value}, so about {max(left, 0)} of the {END_OF_LIFE_YEARS} "
        f"working years Fit-Check counts for any device are left; {country} asks for at "
        f"least {minimum}. {_quote(rule, 'min_remaining_life_years')}{cited}",
        fix=f"None for this unit. Send one made in "
        f"{today.year - END_OF_LIFE_YEARS + int(minimum)} or later.",
        source=year.source,
    )


def _has_document(device: DeviceFacts) -> bool:
    documents = device.documents
    return documents.known and bool(documents.value)


def _either(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _quote(rule: dict[str, Any], key: str) -> str:
    text = str((rule.get("quotes") or {}).get(key) or "").strip()
    return f'"{text}"' if text else ""


def _citation(rule: dict[str, Any]) -> str:
    source = rule.get("source") or {}
    title = str(source.get("title") or "").strip()
    if not title:
        return ""
    pages = str(source.get("pages") or "").strip()
    return f" ({title}{', p.' + pages if pages else ''})"
