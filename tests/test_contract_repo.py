"""Storage for the round's new tables: supply answers, registry cache, packets, extractions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.domain.provenance import Provenance, fact
from app.domain.supply_answer import SupplyAnswer
from tests.factories import device

NOW = datetime(2026, 10, 18, 9, 0, 0)


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'contracts.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    db.init_db()
    yield
    if db._engine is not None:
        db._engine.dispose()


def an_answer(**kw) -> SupplyAnswer:
    base = dict(device_type="oxygen concentrator", model="525KS", country="Uganda",
                consumables_available=False, spare_parts_available=True,
                recorded_by="Fit-Check field team", note="Filters not stocked")
    return SupplyAnswer(**{**base, **kw})


def test_a_supply_answer_round_trips_with_its_dont_knows(database):
    answer_id = repo.save_supply_answer(an_answer())

    loaded = repo.load_supply_answer("oxygen concentrator", "525KS", "Uganda")

    assert loaded.id == answer_id
    assert loaded.consumables_available is False
    assert loaded.spare_parts_available is True
    assert loaded.sold_locally is None and loaded.manufacturer_supported is None
    assert loaded.recorded_by == "Fit-Check field team"
    assert loaded.recorded_at is not None


def test_the_exact_model_wins_over_the_type_answer(database):
    repo.save_supply_answer(an_answer(model="", consumables_available=True))
    repo.save_supply_answer(an_answer())

    assert repo.load_supply_answer("oxygen concentrator", "525KS", "Uganda").model == "525KS"


def test_an_unknown_model_falls_back_to_the_any_model_answer(database):
    repo.save_supply_answer(an_answer(model="", sold_locally=True))

    loaded = repo.load_supply_answer("Oxygen Concentrator", "older one", "uganda")

    assert loaded.for_any_model
    assert loaded.sold_locally is True


def test_a_model_answer_is_found_without_a_device_type(database):
    repo.save_supply_answer(an_answer())

    assert repo.load_supply_answer("", "525ks", "Uganda").model == "525KS"


def test_no_answer_for_the_type_or_country_is_none(database):
    repo.save_supply_answer(an_answer())

    assert repo.load_supply_answer("oxygen concentrator", "", "Uganda") is None
    assert repo.load_supply_answer("oxygen concentrator", "525KS", "Nepal") is None
    assert repo.load_supply_answer("", "", "Uganda") is None


def test_saving_the_same_key_replaces_the_answer(database):
    first = repo.save_supply_answer(an_answer())
    second = repo.save_supply_answer(an_answer(consumables_available=True, note="now stocked"))

    loaded = repo.load_supply_answer("oxygen concentrator", "525KS", "Uganda")
    assert first == second
    assert loaded.consumables_available is True and loaded.note == "now stocked"


def test_a_registry_lookup_is_cached_for_seven_days(database):
    repo.store_registry_lookup("525KS", "In Commercial Distribution",
                               "https://accessgudid.nlm.nih.gov/devices/x", {"results": []},
                               now=NOW)

    fresh = repo.cached_registry_lookup("525ks", now=NOW + timedelta(days=6))
    assert fresh.status == "In Commercial Distribution"
    assert fresh.record_url.endswith("/x")
    assert fresh.raw_json == '{"results": []}'
    assert repo.cached_registry_lookup("525KS", now=NOW + timedelta(days=8)) is None
    assert repo.cached_registry_lookup("525KS", max_age_days=30, now=NOW + timedelta(days=8))
    assert repo.cached_registry_lookup("other") is None


def test_storing_a_registry_lookup_again_replaces_it(database):
    repo.store_registry_lookup("525KS", "error", "", "{}", now=NOW)
    repo.store_registry_lookup("525KS", "no record", "", "{}", now=NOW)

    assert repo.cached_registry_lookup("525KS", now=NOW).status == "no record"


def test_a_packet_saves_loads_replaces_and_clears(database):
    assert repo.load_packet(7, "uganda-district") is None

    repo.save_packet(7, "uganda-district", "English", {"quick_start": [{"text": "On", "page": 4}]})
    loaded = repo.load_packet(7, "uganda-district")
    assert loaded.language == "English"
    assert loaded.card["quick_start"][0]["page"] == 4
    assert loaded.translated is None

    repo.save_packet(7, "uganda-district", "French", {"a": 1}, {"b": 2})
    assert repo.load_packet(7, "uganda-district").translated == {"b": 2}
    assert repo.load_packet(7, "haiti-community") is None

    repo.clear_packet(7, "uganda-district")
    assert repo.load_packet(7, "uganda-district") is None


def test_an_extraction_is_held_for_an_hour(database):
    facts = device(dated_items=fact(False, Provenance.DONOR_STATED))
    extraction_id = repo.save_extraction(facts, {"title": "manual", "url": "u"}, now=NOW)

    held = repo.load_extraction(extraction_id, now=NOW + timedelta(minutes=59))
    assert held.facts == facts
    assert held.document == {"title": "manual", "url": "u"}
    assert repo.load_extraction(extraction_id, now=NOW + timedelta(minutes=61)) is None
    assert repo.load_extraction("nope") is None


def test_extraction_ids_are_distinct(database):
    first = repo.save_extraction(device(), now=NOW)
    second = repo.save_extraction(device(), now=NOW)

    assert first != second
    assert repo.load_extraction(first, now=NOW).document == {}
