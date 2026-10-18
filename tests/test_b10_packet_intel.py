"""[B10] The packet's "Send with it" list for hero offer B at Kayunga is built
on the same intel the candidate page screens with (the supply order: registry,
then the nonprofit's recorded answer), so it names the stabiliser and the two
years of filters. A stored match's verdict still wins. Seeded demo in a fresh
SQLite file, a fake card model, no translator; GUDID is off.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.domain.device import SupplyIntel
from app.domain.marketplace import MatchRecord
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict
from app.main import app
from app.matching.pipeline import screen_offers
from app.matching.supply_order import supply_lookup
from app.web import marketplace_routes
from scripts import seed_demo

KAYUNGA = seed_demo.KAYUNGA
CARD = {
    "quick_start": [{"text": "The start up may take up to 15 minutes.", "page": 8}],
    "care_schedule": [], "consumables": [], "warnings": [],
}


class FakeCardChat:
    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt, **kwargs):
        self.calls += 1
        return CARD


@pytest.fixture
def seeded(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'b10.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setenv("GUDID_LOOKUP", "off")
    request_id = seed_demo.main()["request_id"]
    capsys.readouterr()
    chat = FakeCardChat()
    monkeypatch.setattr(marketplace_routes, "_packet_chat", lambda: chat)
    monkeypatch.setattr(marketplace_routes, "_packet_language", lambda: None)
    offer_b = next(o for o in repo.open_offers_for_type(seed_demo.HERO_TYPE)
                   if o.device.model.known and o.device.model.value == "525KS")
    yield TestClient(app), offer_b, request_id
    if db._engine is not None:
        db._engine.dispose()


def send_with(body: str) -> str:
    return body.split("<h2>Send with it</h2>", 1)[1].split("</section>", 1)[0]


def test_with_no_stored_match_the_packet_names_the_stabiliser_and_the_filters(seeded):
    client, offer_b, _ = seeded
    assert not repo.matches_for_request(seeded[2])

    section = send_with(client.get(f"/packet/{offer_b.id}?facility={KAYUNGA}").text)

    assert "<b>Grid reliability.</b> A voltage stabiliser" in section
    assert "<b>Consumables.</b> 2 years of" in section
    assert "MC44D-605 intake bacteria filter" in section


def test_the_packet_verdict_is_the_one_the_candidate_page_screens(seeded):
    client, offer_b, _ = seeded
    clinic = repo.load_facility(KAYUNGA)
    screened = screen_offers(clinic, [offer_b], intel_for=supply_lookup).ranked[0].verdict

    packet = marketplace_routes._packet_verdict(offer_b, clinic)

    assert packet.outcome is screened.outcome is Outcome.WORKS_IF
    assert [(r.key, r.fix) for r in packet.conditional] == \
        [(r.key, r.fix) for r in screened.conditional]
    assert {r.key for r in packet.conditional} == {"grid", "consumables"}


def test_a_stored_match_verdict_still_wins(seeded):
    client, offer_b, request_id = seeded
    stored = Verdict(Outcome.WORKS_IF, (CheckResult(
        key="grid", label="Grid reliability", status=CheckStatus.MITIGABLE, detail="d",
        fix="A stored 1 kVA stabiliser."),))
    repo.save_match(MatchRecord(request_id=request_id, offer_id=offer_b.id, verdict=stored,
                                intel=SupplyIntel()))

    section = send_with(client.get(f"/packet/{offer_b.id}?facility={KAYUNGA}").text)

    assert "A stored 1 kVA stabiliser." in section
    assert "Consumables." not in section
