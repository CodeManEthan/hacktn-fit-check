"""[B6] The four demo screens of [F4], walked with TestClient against the
seeded demo (scripts/seed_demo.py into a fresh SQLite file). Fake model
clients only; GUDID is off under pytest.

1. /offer: the 525DS from the library (picked, or pasted and named) fills the
   inspector with cited rows, each linked to its page.
2. /request/<id>/matches: B first with a packet link, A under "Won't work"
   with the advisory, C under needs-info.
3. B's candidate page: fifteen checks, Sollatek covering, Houston AVR rejected.
4. /packet/<B>?facility=uganda-district: "English, as requested" and the card.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.ai import extract as ex
from app.ai import fetch
from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.main import app
from app.reference import device_library
from app.web import marketplace_routes
from scripts import seed_demo

KAYUNGA = seed_demo.KAYUNGA
CARD = {
    "quick_start": [{"text": "The start up may take up to 15 minutes.", "page": 8}],
    "care_schedule": [], "consumables": [], "warnings": [],
}


class FakeNamer:
    """The naming step's reply for a pasted 525DS listing; nothing else is asked."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt, **kwargs):
        self.calls += 1
        return {"manufacturer": "Drive DeVilbiss", "model": "525DS",
                "device_type": "oxygen concentrator", "plate_text": None}


class FakeCardChat:
    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt, **kwargs):
        self.calls += 1
        return CARD


@pytest.fixture
def walk(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=f"sqlite:///{tmp_path / 'walk.db'}"))
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setenv("GUDID_LOOKUP", "off")
    seeded = seed_demo.main()
    capsys.readouterr()
    offers = repo.open_offers_for_type(seed_demo.HERO_TYPE)
    by_model = {o.device.model.value: o.id for o in offers if o.device.model.known}
    ids = {"request": seeded["request_id"], "A": by_model["525DS"], "B": by_model["525KS"],
           "C": next(o.id for o in offers if not o.device.model.known)}
    yield TestClient(app), ids
    if db._engine is not None:
        db._engine.dispose()


def test_screen_1_the_525ds_fills_the_inspector_with_page_links(walk, monkeypatch):
    client, _ = walk
    assert "DeVilbiss Healthcare 525DS (oxygen concentrator)" in client.get("/offer").text
    page = device_library.entry(seed_demo.HERO_A_SLUG).facts["voltage_min_v"]["page"]

    picked = client.post("/offer/prefill", data={"slug": seed_demo.HERO_A_SLUG}).json()
    namer = FakeNamer()
    monkeypatch.setattr(marketplace_routes, "extract",
                        lambda **kw: ex.extract(**kw, client=namer))
    monkeypatch.delenv(fetch.SEARCH_PROVIDER_ENV, raising=False)
    pasted = client.post("/offer/extract", data={
        "spec_text": "Drive DeVilbiss 525DS oxygen concentrator, 115 V 60 Hz, from Nashville"}).json()

    for data in (picked, pasted):
        rows = {r["label"]: r for r in data["rows"]}
        assert rows["Voltage"]["value"] == "97–127 V"
        assert rows["Voltage"]["source_url"].endswith(f"#page={page}")
        assert rows["Frequency"]["value"] == "60 Hz"
        known = [r for r in data["rows"] if r["known"] and r["label"] != "Model"]
        assert known and all(r["source_url"] for r in known)
    assert namer.calls == 1


def test_screen_2_matches_reads_b_then_a_with_the_advisory_then_c(walk):
    client, ids = walk
    body = client.get(f"/request/{ids['request']}/matches").text
    link = lambda w: f'href="/request/{ids["request"]}/offer/{ids[w]}"'  # noqa: E731
    at_b, at_a, at_c = body.index(link("B")), body.index(link("A")), body.index(link("C"))
    won_t = body.index("Won't work at Kayunga District Hospital")
    assert at_b < won_t < at_a < at_c
    b_part, a_part, c_part = body[at_b:won_t], body[at_a:at_c], body[at_c:]
    assert f"/packet/{ids['B']}?facility={KAYUNGA}" in b_part and "Ask this donor" in b_part
    assert "Supply frequency" in a_part and "Buy locally instead?" in a_part
    assert "about $290" in a_part and "$1,319 to $2,273" in a_part
    assert "Needs info" in c_part and "questions first" in c_part


def test_screen_3_b_shows_fifteen_checks_and_the_send_with_list(walk):
    client, ids = walk
    body = client.get(f"/request/{ids['request']}/offer/{ids['B']}").text
    assert len(re.findall(r'data-check="', body)) == 15
    assert re.search(r'pill good">covers</span> Sollatek AVS30', body)
    assert re.search(r"won't work here itself</span> automatic voltage regulator \(Houston", body)
    assert "Generate handover packet" in body


def test_screen_4_the_packet_for_b_at_kayunga_is_english_as_requested(walk, monkeypatch):
    client, ids = walk
    chat = FakeCardChat()
    monkeypatch.setattr(marketplace_routes, "_packet_chat", lambda: chat)
    monkeypatch.setattr(marketplace_routes, "_packet_language", lambda: None)
    body = client.get(f"/packet/{ids['B']}?facility={KAYUNGA}").text
    assert "English, as requested" in body
    assert "The start up may take up to 15 minutes." in body
    assert "SE-525KS-1 Rev D" in body                    # an English edition, linked
    assert "FDA GUDID record" not in body.split("own editions", 1)[1].split("</section>", 1)[0]
    assert chat.calls == 1
