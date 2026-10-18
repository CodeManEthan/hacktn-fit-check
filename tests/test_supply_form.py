"""The /supply/<country> form: four yes/no/don't-know answers, written once."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.supply_answer import SupplyAnswer
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'supply_form.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def answer_form(**kw) -> dict:
    base = {"device_type": "oxygen concentrator", "model": "",
            "consumables_available": "no", "spare_parts_available": "yes",
            "sold_locally": "unknown", "manufacturer_supported": "unknown",
            "recorded_by": "Asha N.", "note": "Called two suppliers in Kampala."}
    return {**base, **kw}


def test_the_form_asks_four_questions_with_three_answers_each(client):
    page = client.get("/supply/Uganda?device_type=oxygen+concentrator&model=525KS")

    assert page.status_code == 200
    assert 'value="oxygen concentrator"' in page.text and 'value="525KS"' in page.text
    for name in ("consumables_available", "spare_parts_available", "sold_locally",
                 "manufacturer_supported"):
        assert page.text.count(f'name="{name}"') == 3
    assert "Don't know" in page.text
    assert 'action="/supply/Uganda"' in page.text


def test_saving_writes_one_answer_for_the_type(client):
    reply = client.post("/supply/Uganda", data=answer_form(), follow_redirects=False)

    assert reply.status_code == 303
    assert reply.headers["location"].startswith("/supply/Uganda?")
    stored = repo.load_supply_answer("oxygen concentrator", "", "Uganda")
    assert stored.model == ""
    assert stored.consumables_available is False
    assert stored.spare_parts_available is True
    assert stored.sold_locally is None and stored.manufacturer_supported is None
    assert stored.recorded_by == "Asha N." and stored.note == "Called two suppliers in Kampala."
    assert stored.recorded_at is not None


def test_saving_for_a_model_and_returning_to_the_page_that_asked(client):
    reply = client.post("/supply/Uganda",
                        data=answer_form(model="525KS", back="/request/4/matches"),
                        follow_redirects=False)

    assert reply.status_code == 303
    assert reply.headers["location"] == "/request/4/matches"
    assert repo.load_supply_answer("", "525ks", "uganda").model == "525KS"


@pytest.mark.parametrize("back", ["https://evil.example/", "//evil.example/x", "/\\evil"])
def test_back_never_leaves_the_site(client, back):
    reply = client.post("/supply/Uganda", data=answer_form(back=back), follow_redirects=False)

    assert reply.headers["location"].startswith("/supply/Uganda?")


def test_saving_again_replaces_the_answer(client):
    client.post("/supply/Uganda", data=answer_form())
    client.post("/supply/Uganda", data=answer_form(consumables_available="yes"))

    assert repo.load_supply_answer("oxygen concentrator", "", "Uganda").consumables_available is True


def test_the_form_reopens_with_the_stored_answer(client):
    client.post("/supply/Uganda", data=answer_form())

    page = client.get("/supply/Uganda?device_type=oxygen+concentrator&saved=1").text

    assert "Saved." in page
    assert 'name="consumables_available" value="no" checked' in page
    assert 'name="spare_parts_available" value="yes" checked' in page
    assert 'name="sold_locally" value="unknown" checked' in page
    assert "Called two suppliers in Kampala." in page


def test_a_model_form_is_not_filled_from_the_type_answer(client):
    repo.save_supply_answer(SupplyAnswer(device_type="oxygen concentrator", country="Uganda",
                                         consumables_available=False, recorded_by="Asha N."))

    page = client.get("/supply/Uganda?device_type=oxygen+concentrator&model=525KS").text

    assert 'name="consumables_available" value="no" checked' not in page
    assert 'name="consumables_available" value="unknown" checked' in page


@pytest.mark.parametrize("change, message", [
    ({"device_type": " "}, "Kind of equipment is needed."),
    ({"recorded_by": ""}, "Who is answering is needed."),
    ({"sold_locally": "maybe"}, "yes, no or don't know"),
])
def test_a_bad_answer_is_refused_and_nothing_is_written(client, change, message):
    reply = client.post("/supply/Uganda", data=answer_form(**change), follow_redirects=False)

    assert reply.status_code == 400
    assert message in reply.text.replace("&#39;", "'")
    assert repo.load_supply_answer("oxygen concentrator", "", "Uganda") is None


def test_a_country_with_a_space_round_trips(client):
    reply = client.post("/supply/South Sudan", data=answer_form(), follow_redirects=False)

    assert reply.headers["location"].startswith("/supply/South%20Sudan?")
    assert repo.load_supply_answer("oxygen concentrator", "", "South Sudan") is not None
