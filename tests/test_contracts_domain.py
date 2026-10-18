"""The shared names the overnight round's builds read: values, labels, round trips."""

from __future__ import annotations

from datetime import date

from app.data.serialize import (
    device_from_dict,
    device_to_dict,
    intel_from_dict,
    intel_to_dict,
)
from app.domain.provenance import TRUSTED_SOURCES, Provenance, fact
from app.domain.requirement import (
    ADD_ONS,
    NOT_SHIPPABLE,
    POWER_ADD_ONS,
    SEARCH_TERMS,
    SITE_WORKS,
    Requirement,
    RequirementKind,
)
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict
from app.domain.supply_answer import SupplyAnswer
from app.web.presenters import TONE_BY_OUTCOME, TONE_BY_OUTCOME_VALUE
from tests.factories import device, intel

DOCUMENTS = [
    {
        "slug": "drive-devilbiss-525ks",
        "file": "drive-devilbiss-525ks/owners-manual.txt",
        "title": "525 owner's manual",
        "url": "https://example.org/525.pdf",
        "languages": ["English"],
    }
]


def asking(n: int) -> Verdict:
    results = tuple(
        CheckResult(key=f"k{i}", label="L", status=CheckStatus.NEEDS_INFO,
                    detail="", question=f"q{i}?")
        for i in range(n)
    )
    return Verdict(outcome=Outcome.NEEDS_INFO, results=results,
                   questions=tuple(r.question for r in results))


def test_needs_info_outcome_has_its_value_stamp_and_headline():
    assert Outcome.NEEDS_INFO.value == "needs info"
    assert asking(3).stamp == "Needs info"
    assert asking(3).headline == "3 questions before a verdict"
    assert asking(1).headline == "1 question before a verdict"


def test_both_tone_maps_cover_every_outcome():
    assert set(TONE_BY_OUTCOME) == set(Outcome)
    assert TONE_BY_OUTCOME_VALUE["needs info"] == TONE_BY_OUTCOME[Outcome.NEEDS_INFO]


def test_registry_and_recorded_sources_are_trusted():
    assert Provenance.REGISTRY.value == "FDA GUDID"
    assert Provenance.RECORDED.value == "recorded by the nonprofit"
    assert {Provenance.REGISTRY, Provenance.RECORDED} <= TRUSTED_SOURCES
    assert fact(True, Provenance.RECORDED).trusted


def test_device_round_trips_dated_items_and_documents():
    original = device(
        dated_items=fact(False, Provenance.DONOR_STATED),
        documents=fact(DOCUMENTS, Provenance.SPEC_SHEET, "525 owner's manual"),
        expiry_date=fact(date(2027, 3, 31), Provenance.DONOR_STATED),
    )

    back = device_from_dict(device_to_dict(original))

    assert back.dated_items == original.dated_items
    assert back.dated_items.known and back.dated_items.value is False
    assert back.documents == original.documents
    assert back == original


def test_a_stored_device_from_before_the_new_fields_reads_them_as_unknown():
    payload = device_to_dict(device())
    del payload["dated_items"], payload["documents"]

    back = device_from_dict(payload)

    assert not back.dated_items.known
    assert not back.documents.known


def test_intel_round_trips_sold_locally():
    original = intel(sold_locally=fact(False, Provenance.RECORDED, "field team"))

    back = intel_from_dict(intel_to_dict(original))

    assert back.sold_locally == original.sold_locally
    assert back == original
    assert not intel_from_dict({"country": "Uganda"}).sold_locally.known


def test_manual_is_the_handover_packet_and_is_never_searched_for():
    assert RequirementKind.MANUAL.value == "handover packet"
    assert RequirementKind.MANUAL in NOT_SHIPPABLE
    assert not Requirement(RequirementKind.MANUAL, "packet").shippable


def test_add_ons_and_site_works_partition_the_kinds_except_training():
    assert ADD_ONS == {
        RequirementKind.TRANSFORMER, RequirementKind.STABILISER,
        RequirementKind.BACKUP_POWER, RequirementKind.DEHUMIDIFIER,
        RequirementKind.CONSUMABLES, RequirementKind.SPARE_PARTS,
        RequirementKind.MANUAL,
    }
    assert SITE_WORKS == {
        RequirementKind.COOLING, RequirementKind.OXYGEN_SUPPLY,
        RequirementKind.WATER_SUPPLY, RequirementKind.AIR_COMPRESSOR,
    }
    assert POWER_ADD_ONS == {
        RequirementKind.TRANSFORMER, RequirementKind.STABILISER,
        RequirementKind.BACKUP_POWER, RequirementKind.DEHUMIDIFIER,
    }
    assert POWER_ADD_ONS <= ADD_ONS
    assert not ADD_ONS & SITE_WORKS
    assert set(RequirementKind) - ADD_ONS - SITE_WORKS == {RequirementKind.TRAINING}


def test_site_works_have_no_search_terms():
    assert not SITE_WORKS & set(SEARCH_TERMS)
    assert Requirement(RequirementKind.COOLING, "AC").search_terms == ()


def test_a_supply_answer_defaults_to_any_model_and_dont_know():
    answer = SupplyAnswer(device_type="oxygen concentrator", country="Uganda")

    assert answer.for_any_model
    assert answer.consumables_available is None
    assert answer.sold_locally is None
    assert answer.id is None
