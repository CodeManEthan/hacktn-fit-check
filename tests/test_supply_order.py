"""The supply order: registry, then the recorded answer, then unknown.

Fakes stand in for the GUDID client and the repo loader, except in the last
tests, which read a real answer back from SQLite.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.provenance import UNKNOWN, Provenance
from app.domain.supply_answer import SupplyAnswer
from app.matching import supply_order
from app.matching.supply_order import supply_lookup
from app.reference.gudid import RegistryResult
from tests.factories import device

RECORD = "https://accessgudid.nlm.nih.gov/devices/00885304000846"
WHEN = datetime(2026, 10, 17, 21, 5)


def registry_says(supported, calls=None):
    def check(manufacturer, model):
        if calls is not None:
            calls.append((manufacturer, model))
        return RegistryResult(status="In Commercial Distribution" if supported else "no record",
                              record_url=RECORD if supported else "", supported=supported)
    return check


def recorded(answer=None, calls=None):
    def load(device_type, model, country):
        if calls is not None:
            calls.append((device_type, model, country))
        return answer
    return load


def an_answer(**kw) -> SupplyAnswer:
    base = dict(device_type="oxygen concentrator", country="Uganda", model="",
                consumables_available=False, spare_parts_available=True,
                sold_locally=None, manufacturer_supported=False,
                recorded_by="Asha N.", recorded_at=WHEN)
    return SupplyAnswer(**{**base, **kw})


def test_nothing_known_anywhere_leaves_every_field_unknown():
    intel = supply_lookup(device(), "oxygen concentrator", "Uganda",
                          registry=registry_says(None), recorded=recorded(None))

    assert intel.country == "Uganda"
    for name in supply_order.RECORDED_FIELDS:
        assert getattr(intel, name) is UNKNOWN


def test_the_registry_affirms_support_with_the_record_url():
    calls = []
    intel = supply_lookup(device(), "oxygen concentrator", "Uganda",
                          registry=registry_says(True, calls), recorded=recorded(None))

    assert calls == [("Acme", "V100")]
    assert intel.manufacturer_supported.value is True
    assert intel.manufacturer_supported.source is Provenance.REGISTRY
    assert intel.manufacturer_supported.note == RECORD


def test_the_registry_comes_first_and_the_answer_fills_the_rest():
    intel = supply_lookup(device(), "oxygen concentrator", "Uganda",
                          registry=registry_says(True), recorded=recorded(an_answer()))

    assert intel.manufacturer_supported.value is True
    assert intel.manufacturer_supported.source is Provenance.REGISTRY
    assert intel.consumables_available.value is False
    assert intel.consumables_available.source is Provenance.RECORDED
    assert intel.consumables_available.note == "recorded by Asha N., 2026-10-17"
    assert intel.spare_parts_available.value is True


def test_without_the_registry_the_answer_fills_support_too():
    intel = supply_lookup(device(), "oxygen concentrator", "Uganda",
                          registry=registry_says(None), recorded=recorded(an_answer()))

    assert intel.manufacturer_supported.value is False
    assert intel.manufacturer_supported.source is Provenance.RECORDED


def test_a_recorded_dont_know_stays_unknown():
    intel = supply_lookup(device(), "oxygen concentrator", "Uganda",
                          registry=registry_says(None), recorded=recorded(an_answer()))

    assert intel.sold_locally is UNKNOWN


def test_the_loader_gets_type_model_and_country():
    calls = []
    supply_lookup(device(), "oxygen concentrator", "Uganda",
                  registry=registry_says(None), recorded=recorded(None, calls))

    assert calls == [("oxygen concentrator", "V100", "Uganda")]


def test_no_model_skips_the_registry_and_asks_by_type():
    reg_calls, load_calls = [], []
    supply_lookup(device(model=UNKNOWN), "oxygen concentrator", "Uganda",
                  registry=registry_says(True, reg_calls), recorded=recorded(None, load_calls))

    assert reg_calls == []
    assert load_calls == [("oxygen concentrator", "", "Uganda")]


def test_a_note_without_a_name_or_date_still_says_who():
    answer = an_answer(recorded_by="", recorded_at=None)

    assert supply_order.recorded_note(answer) == "recorded by the nonprofit"


def test_a_storage_failure_reads_as_no_answer():
    def broken(*a):
        raise repo.RepositoryError("database is down")

    intel = supply_lookup(device(), "oxygen concentrator", "Uganda",
                          registry=registry_says(None), recorded=broken)

    assert intel.consumables_available is UNKNOWN


@pytest.fixture
def store(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'supply.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    yield
    if db._engine is not None:
        db._engine.dispose()


def test_a_model_answer_beats_the_type_answer_from_the_real_table(store):
    repo.save_supply_answer(an_answer(consumables_available=True))
    repo.save_supply_answer(an_answer(model="V100", consumables_available=False,
                                      recorded_by="Field team"))

    intel = supply_lookup(device(), "oxygen concentrator", "uganda", registry=registry_says(None))

    assert intel.consumables_available.value is False
    assert intel.consumables_available.note.startswith("recorded by Field team")


def test_the_legacy_check_without_a_type_finds_only_a_model_answer(store):
    repo.save_supply_answer(an_answer())

    assert supply_lookup(device(), "", "Uganda", registry=registry_says(None)).consumables_available is UNKNOWN

    repo.save_supply_answer(an_answer(model="V100"))
    assert supply_lookup(device(), "", "Uganda", registry=registry_says(None)).consumables_available.value is False


def test_the_default_registry_is_the_gudid_client_and_stays_offline(store):
    # Under pytest the live call is off, so the default path reads as unknown.
    intel = supply_lookup(device(), "oxygen concentrator", "Uganda")

    assert intel.manufacturer_supported is UNKNOWN


def test_a_model_answer_leaves_open_questions_to_the_type_answer():
    calls = []

    def load(device_type, model, country):
        calls.append(model)
        if model:
            return an_answer(model="V100", sold_locally=None, consumables_available=False)
        return an_answer(sold_locally=True, consumables_available=True, recorded_by="Type desk")

    intel = supply_lookup(device(), "oxygen concentrator", "Uganda",
                          registry=registry_says(None), recorded=load)

    assert calls == ["V100", ""]
    assert intel.consumables_available.value is False
    assert intel.sold_locally.value is True
    assert intel.sold_locally.note.startswith("recorded by Type desk")


def test_without_a_type_no_second_lookup_is_made():
    calls = []

    def load(device_type, model, country):
        calls.append(model)
        return an_answer(model="V100")

    supply_lookup(device(), "", "Uganda", registry=registry_says(None), recorded=load)

    assert calls == ["V100"]
