"""/offer/extract and the offer form's confirm controls (design §6.3).

The real extraction pipeline runs against the B4 fixture library with a fake
model client; each test gets its own SQLite file.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai import extract as ex
from app.ai import fetch
from app.ai.client import GenAiError
from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.provenance import UNKNOWN, Fact, Provenance
from app.main import app
from app.reference import device_library as lib
from app.web import inspector, marketplace_routes
from tests.factories import device

LIBRARY = Path(__file__).parent / "fixtures" / "library"
OFFER_FIELDS = {
    "device_type": "oxygen concentrator", "quantity": "2", "country": "United States",
    "city": "Nashville", "donor_name": "Mercy Regional", "donor_email": "m@example.org",
}


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def complete_json(self, prompt, **kwargs):
        self.calls += 1
        return self.replies.pop(0)


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'b4.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    monkeypatch.setattr(marketplace_routes, "categorise_support", lambda *a: None)
    monkeypatch.setattr(lib, "LIBRARY_PATH", LIBRARY / "b4-index.json")
    monkeypatch.setattr(lib, "LIBRARY_DIR", LIBRARY)
    monkeypatch.delenv(fetch.SEARCH_PROVIDER_ENV, raising=False)
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def model_replies(monkeypatch, *replies):
    fake = FakeClient(*replies)
    monkeypatch.setattr(marketplace_routes, "extract",
                        lambda **kw: ex.extract(**kw, client=fake))
    return fake


def no_extract_device(monkeypatch):
    def refuse(**kw):
        raise AssertionError("the held extraction should have been used")
    monkeypatch.setattr(marketplace_routes, "extract_device", refuse)


def row(rows, label):
    return next(r for r in rows if r["label"] == label)


# ---- /offer/extract -------------------------------------------------------------

def test_extract_returns_cited_rows_and_holds_the_facts(client, monkeypatch):
    fake = model_replies(monkeypatch, {"manufacturer": "Drive DeVilbiss", "model": "525DS",
                                       "device_type": "oxygen concentrator", "plate_text": None})

    data = client.post("/offer/extract", data={"spec_text": "Specifications 525DS"}).json()

    assert fake.calls == 1
    assert re.fullmatch(r"[0-9a-f]{32}", data["extraction_id"])
    assert data["library_slug"] == "b4-525ds"
    assert data["device_type"] == "oxygen concentrator"
    voltage = row(data["rows"], "Voltage")
    assert voltage == {
        "label": "Voltage", "value": "97–127 V", "source": "spec sheet",
        "source_url": "https://example.org/525ds-manual.pdf#page=11", "page": 11,
        "document": "DeVilbiss 525 owner's manual", "field": "voltage_min_v,voltage_max_v",
        "known": True, "note": "DeVilbiss 525 owner's manual, p.11",
    }
    assert row(data["rows"], "Year made")["known"] is False
    assert row(data["rows"], "Year made")["source"] == "unverified"
    assert [r["label"] for r in data["rows"]] == list(inspector.ROW_LABELS)
    held = repo.load_extraction(data["extraction_id"])
    assert held.facts.voltage_min_v.value == 97
    assert held.document["library_slug"] == "b4-525ds"
    assert held.document["citations"]["frequencies_hz"]["page"] == 11


def test_extract_no_hit_rows_say_why_a_value_is_missing(client, monkeypatch):
    model_replies(
        monkeypatch,
        {"manufacturer": "DeVilbiss", "model": "525", "device_type": "oxygen concentrator"},
        {"voltage_min_v": 115, "voltage_max_v": 115, "power_watts": 999,
         "sources": {"voltage_min_v": "donor", "voltage_max_v": "donor", "power_watts": "donor"},
         "quotes": {}},
    )

    data = client.post("/offer/extract", data={"spec_text": "DeVilbiss 525, 115 V"}).json()

    assert data["library_slug"] == ""
    assert row(data["rows"], "Voltage")["value"] == "115 V"
    assert row(data["rows"], "Voltage")["source"] == "stated by the donor"
    assert row(data["rows"], "Voltage")["source_url"] == ""
    power = row(data["rows"], "Power")
    assert power["known"] is False and power["note"] == ex.NOT_FOUND
    assert "No library entry" in data["message"]


def test_extract_with_nothing_given_reads_nothing(client):
    data = client.post("/offer/extract", data={"spec_text": "  "}).json()
    assert data == {"rows": [], "extraction_id": "", "message": "Nothing to read yet."}


def test_extract_reports_a_model_failure_as_a_message(client, monkeypatch):
    def fail(**kw):
        raise GenAiError("The model service did not answer.")
    monkeypatch.setattr(marketplace_routes, "extract", fail)

    response = client.post("/offer/extract", data={"spec_text": "525DS"})

    assert response.status_code == 200
    assert response.json()["message"] == "The model service did not answer."


def test_extract_reports_an_oversized_photo_as_a_message(client, monkeypatch):
    monkeypatch.setattr(ex, "MAX_IMAGE_BYTES", 10)
    response = client.post("/offer/extract", files={"nameplate": ("p.jpg", b"x" * 20, "image/jpeg")})
    assert response.status_code == 200
    assert "Keep it under" in response.json()["message"]


# ---- offer_submit with an extraction id -----------------------------------------------

def held_extraction() -> str:
    facts = replace(
        device(),
        voltage_min_v=Fact(97, Provenance.SPEC_SHEET, "manual, p.11"),
        voltage_max_v=Fact(127, Provenance.SPEC_SHEET, "manual, p.11"),
        frequencies_hz=Fact([60], Provenance.SPEC_SHEET, "manual, p.11"),
        expiry_date=Fact("2030-01-01", Provenance.DONOR_STATED),
    )
    return repo.save_extraction(facts, {"library_slug": "b4-525ds"})


def posted_device(response):
    assert response.status_code == 303, response.text
    offer_id = int(response.headers["location"].rsplit("/", 1)[1])
    return repo.load_offer(offer_id).device


def test_submit_with_an_extraction_id_reuses_the_held_facts(client, monkeypatch):
    no_extract_device(monkeypatch)
    extraction_id = held_extraction()

    response = client.post("/offer", data={**OFFER_FIELDS, "extraction_id": extraction_id},
                           follow_redirects=False)

    posted = posted_device(response)
    assert posted.voltage_min_v == Fact(97, Provenance.SPEC_SHEET, "manual, p.11")
    assert posted.frequencies_hz.value == [60]
    # apply_dated_items still runs last: a blank expiry is "nothing dated".
    assert posted.dated_items.value is False
    assert posted.expiry_date is UNKNOWN


def test_rows_marked_wrong_are_posted_as_unknown(client, monkeypatch):
    no_extract_device(monkeypatch)
    extraction_id = held_extraction()

    response = client.post("/offer", data={
        **OFFER_FIELDS, "extraction_id": extraction_id,
        "cleared_fields": "voltage_min_v,voltage_max_v,raw_input,not_a_field",
        "expiry_date": "2027-03-31",
        # A fact posted by the client is not a form field the route reads.
        "voltage_min_v": "230", "frequencies_hz": "50",
    }, follow_redirects=False)

    posted = posted_device(response)
    assert posted.voltage_min_v is UNKNOWN and posted.voltage_max_v is UNKNOWN
    assert posted.frequencies_hz.value == [60]
    assert posted.dated_items.value is True
    assert str(posted.expiry_date.value) == "2027-03-31"


def test_an_expired_extraction_is_refused_with_a_way_forward(client, monkeypatch):
    no_extract_device(monkeypatch)
    old = repo.save_extraction(device(), now=datetime.now() - timedelta(hours=2))

    response = client.post("/offer", data={**OFFER_FIELDS, "extraction_id": old})

    assert response.status_code == 400
    assert "expired" in response.text and "Read these facts now" in response.text


def test_submit_without_an_extraction_id_still_reads_the_source(client, monkeypatch):
    seen = {}

    def read(**kw):
        seen.update(kw)
        return device()
    monkeypatch.setattr(marketplace_routes, "extract_device", read)

    response = client.post("/offer", data={**OFFER_FIELDS, "spec_text": "525DS"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert seen["typed_text"] == "525DS"


def test_submit_with_no_source_and_no_extraction_is_refused(client, monkeypatch):
    no_extract_device(monkeypatch)
    response = client.post("/offer", data=OFFER_FIELDS)
    assert response.status_code == 400 and "nameplate photo" in response.text


# ---- the form -------------------------------------------------------------------

def test_the_offer_form_carries_the_confirm_controls_and_every_row(client):
    page = client.get("/offer").text

    assert 'name="extraction_id"' in page and 'name="cleared_fields"' in page
    for label in inspector.ROW_LABELS:
        assert f'<td class="audit-name">{label}</td>' in page
