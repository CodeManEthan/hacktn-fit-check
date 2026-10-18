"""scripts/seed_demo.py: what it writes, and that a second run replaces rather than adds."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.data.marketplace_schema import offers, requests, supply_answers
from app.domain.marketplace import GeoPoint, Offer, OfferStatus
from app.domain.provenance import Provenance
from scripts import seed_demo


@pytest.fixture
def fresh_db(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'seed.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    yield
    capsys.readouterr()
    if db._engine is not None:
        db._engine.dispose()


def _count(table, *where) -> int:
    with db.get_engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(table).where(*where)).scalar()


def _all_offers() -> list[Offer]:
    with db.get_engine().connect() as conn:
        ids = [r[0] for r in conn.execute(select(offers.c.id))]
    return [repo.load_offer(i) for i in ids]


def _offer(prefix: str, model: str | None = None) -> Offer:
    return next(o for o in _all_offers() if o.donor_name.startswith(prefix)
                and (model is None or o.device.model.value == model)
                and o.status is OfferStatus.OPEN)


def test_running_twice_leaves_one_of_each(fresh_db):
    seed_demo.main()
    first = (_count(offers), _count(supply_answers), _count(requests), _count(db.verdicts),
             _count(db.devices))
    seed_demo.main()
    second = (_count(offers), _count(supply_answers), _count(requests), _count(db.verdicts),
              _count(db.devices))

    assert first == second == (len(seed_demo.all_offers()), 1, 1, 20, 20)
    assert _count(offers, offers.c.status == OfferStatus.OPEN.value) == len(seed_demo.all_offers())


def test_a_database_seeded_by_the_old_script_is_replaced_in_place(fresh_db):
    seed_demo.repository.setup()
    old = Offer(
        donor_name="Kampala Power Trust", donor_email="trust@example.org",
        device_type="voltage stabiliser", support_kind="voltage stabiliser",
        origin=GeoPoint(country="Uganda", city="Kampala"), quantity_offered=4,
        device=seed_demo._device(manufacturer="Sollatek", model="AVS30", year_of_manufacture=2022),
    )
    kept, duplicate = repo.save_offer(old), repo.save_offer(old)

    seed_demo.main()

    sollatek = repo.load_offer(kept)
    assert sollatek.device.voltage_min_v.value == 160
    assert sollatek.status is OfferStatus.OPEN
    assert repo.load_offer(duplicate).status is OfferStatus.WITHDRAWN


def test_the_hero_offers_carry_library_facts_and_donor_facts(fresh_db):
    seed_demo.main()
    a = _offer("Nashville General")
    b = _offer("Leeds Teaching")
    c = _offer("Harris County")

    assert a.device.model.value == "525DS" and a.device.frequencies_hz.value == [60]
    assert a.device.frequencies_hz.source is Provenance.SPEC_SHEET
    assert (a.device.year_of_manufacture.value, a.device.year_of_manufacture.source) == (
        2016, Provenance.DONOR_STATED)
    assert (a.origin.city, a.origin.latitude, a.origin.longitude) == ("Nashville", 36.1627, -86.7816)
    assert b.device.model.value == "525KS" and b.device.year_of_manufacture.value == 2019
    assert (b.origin.city, b.origin.latitude, b.origin.longitude) == ("Leeds", 53.8008, -1.5491)
    assert a.device.dated_items.value is False and b.device.dated_items.value is False
    assert b.device.documents.known
    assert c.quantity_offered == 2
    assert c.device.raw_input == seed_demo.OFFER_C_TEXT
    assert not c.device.dated_items.known
    assert not c.device.model.known
    assert (c.origin.city, c.origin.latitude, c.origin.longitude) == ("Houston", 29.7604, -95.3698)


def test_every_durable_offer_says_nothing_is_dated(fresh_db):
    seed_demo.main()
    for offer in _all_offers():
        if offer.donor_name.startswith("Harris County"):
            continue
        assert offer.device.dated_items.value is False, offer.donor_name
        assert offer.device.dated_items.source is Provenance.DONOR_STATED


def test_the_support_offers_carry_their_plate_facts(fresh_db):
    seed_demo.main()
    sollatek = _offer("Kampala Power Trust", "AVS30")
    assert (sollatek.device.voltage_min_v.value, sollatek.device.voltage_max_v.value) == (160, 260)
    assert sollatek.device.frequencies_hz.value == [50]
    assert sollatek.device.voltage_min_v.source is Provenance.DONOR_STATED
    assert "UNICEF S0002632" in sollatek.device.voltage_min_v.note

    avr = _offer("Houston surplus AVR")
    assert avr.support_kind == "voltage stabiliser"
    assert (avr.device.voltage_min_v.value, avr.device.voltage_max_v.value) == (110, 120)
    assert avr.device.frequencies_hz.value == [60]
    assert avr.origin.city == "Houston"

    ups = _offer("Kampala Power Trust", "Smart-UPS 3000")
    assert not ups.device.voltage_min_v.known  # unchanged from the earlier seed


def test_the_recorded_answer_and_the_request(fresh_db):
    seed_demo.main()
    answer = repo.load_supply_answer("oxygen concentrator", "525KS", "Uganda")
    assert (answer.consumables_available, answer.spare_parts_available) == (False, True)
    assert answer.sold_locally is None and answer.manufacturer_supported is None
    assert answer.recorded_by == "Fit-Check field team"
    # No answer for the type as a whole, so Offer C's supply checks stay unknown.
    assert repo.load_supply_answer("oxygen concentrator", "", "Uganda") is None

    request = next(r for r in repo.list_open_requests())
    assert request.facility_slug == "uganda-district"
    assert [(l.device_type, l.quantity) for l in request.lines] == [("oxygen concentrator", 1)]


def test_recorded_intel_reads_the_answer_as_recorded_facts(fresh_db):
    seed_demo.main()
    b = _offer("Leeds Teaching")
    intel = seed_demo.recorded_intel(b.device, b.device_type, "Uganda")
    assert intel.consumables_available.value is False
    assert intel.consumables_available.source is Provenance.RECORDED
    assert intel.spare_parts_available.value is True
    assert not intel.sold_locally.known
    assert not seed_demo.recorded_intel(b.device, b.device_type, "Haiti").consumables_available.known


def test_the_history_is_twenty_real_verdicts_across_the_three_clinics(fresh_db):
    seed_demo.main()
    with db.get_engine().connect() as conn:
        rows = conn.execute(select(db.verdicts.c.facility_slug, db.verdicts.c.outcome)).all()
    assert len(rows) == 20
    assert {slug for slug, _ in rows} == {"uganda-district", "haiti-community", "nepal-phc"}
    assert len({outcome for _, outcome in rows}) >= 2
