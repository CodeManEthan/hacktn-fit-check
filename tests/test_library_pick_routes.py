"""The offer form's library pick: the dropdown, /offer/prefill and library_slug on submit."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.provenance import Provenance
from app.main import app
from app.web import marketplace_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'pick.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    monkeypatch.setattr(marketplace_routes, "categorise_support", lambda *a: None)

    def no_model(**_):
        raise AssertionError("a library pick must not call the extractor")

    monkeypatch.setattr(marketplace_routes, "extract_device", no_model)
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def _offer_form(**kw) -> dict:
    base = {"library_slug": "drive-devilbiss-525ks", "device_type": "", "quantity": "1",
            "country": "United Kingdom", "city": "Leeds", "donor_name": "Leeds Teaching Hospitals",
            "donor_email": "surplus@example.org", "year_of_manufacture": "2019", "expiry_date": ""}
    return {**base, **kw}


def test_the_offer_page_lists_the_twelve_entries(client):
    page = client.get("/offer").text
    assert 'name="library_slug"' in page
    assert page.count('data-device-type="') == 12
    assert "DeVilbiss Healthcare 525KS (oxygen concentrator)" in page


def test_prefill_returns_cited_rows(client):
    data = client.post("/offer/prefill", data={"slug": "drive-devilbiss-525ks"}).json()
    rows = {r["label"]: r for r in data["rows"]}
    assert data["device_type"] == "oxygen concentrator"
    assert rows["Voltage"]["value"] == "187–253 V"
    assert rows["Voltage"]["source"] == Provenance.SPEC_SHEET.value
    assert rows["Voltage"]["page"] == 11
    assert rows["Voltage"]["source_url"].endswith("#page=11")
    assert rows["Frequency"]["value"] == "50 Hz"
    assert rows["Needs oxygen"]["value"] == "no"
    assert rows["Consumables"]["value"].startswith("MC44D-605")
    assert all({"label", "value", "source", "source_url", "page"} <= set(r) for r in data["rows"])


def test_prefill_on_an_unknown_slug_is_a_message(client):
    data = client.post("/offer/prefill", data={"slug": "nope"}).json()
    assert data["rows"] == []
    assert "nope" in data["message"]


def test_submit_with_a_pick_rebuilds_facts_from_the_library(client):
    response = client.post("/offer", data=_offer_form(
        voltage_min_v="100", spec_text="220 V only, typed by hand"), follow_redirects=False)
    assert response.status_code == 303
    offer = repo.load_offer(int(response.headers["location"].rsplit("/", 1)[1]))
    assert offer.device_type == "oxygen concentrator"  # taken from the entry when blank
    assert offer.device.voltage_min_v.value == 187
    assert offer.device.voltage_min_v.source is Provenance.SPEC_SHEET
    assert offer.device.year_of_manufacture.value == 2019
    assert offer.device.year_of_manufacture.source is Provenance.DONOR_STATED
    assert offer.device.documents.known
    # apply_dated_items still runs last: a blank expiry is "nothing dated".
    assert offer.device.dated_items.value is False
    assert offer.origin.city == "Leeds"


def test_submit_with_a_pick_keeps_a_stated_expiry(client):
    response = client.post("/offer", data=_offer_form(expiry_date="2027-03-31"), follow_redirects=False)
    offer = repo.load_offer(int(response.headers["location"].rsplit("/", 1)[1]))
    assert offer.device.dated_items.value is True
    assert str(offer.device.expiry_date.value) == "2027-03-31"


@pytest.mark.parametrize("year", ["19x6", "1800", "3000"])
def test_a_bad_build_year_is_refused(client, year):
    response = client.post("/offer", data=_offer_form(year_of_manufacture=year))
    assert response.status_code == 400


def test_an_unknown_pick_is_refused(client):
    response = client.post("/offer", data=_offer_form(library_slug="nope"))
    assert response.status_code == 400
    assert "nope" in response.text
