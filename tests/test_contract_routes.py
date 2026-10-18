"""The routes wired once to the contracts: matches, create_match, offer, /check.

Each test gets its own SQLite file and a TestClient without the lifespan, so
no seed runs; the test saves exactly the rows it needs. No model is called:
extraction and categorisation are replaced with fakes.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.device import SupplyIntel
from app.domain.marketplace import GeoPoint, Offer, Request, RequestLine
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict
from app.main import app
from app.matching.pipeline import Candidate, Screening
from app.web import marketplace_routes, routes
from app.web.presenters import Advisory
from tests.factories import device, facility

CLINIC = facility(slug="test-clinic", name="Test Clinic", country="Testland",
                  latitude=0.0, longitude=0.0)


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'routes.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    monkeypatch.setattr(marketplace_routes, "categorise_support", lambda *a: None)
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def an_offer(**kw) -> Offer:
    base = dict(
        donor_name="Mercy Regional", donor_email="m@example.org", device=device(),
        device_type="oxygen concentrator",
        origin=GeoPoint(country="Testland", city="Here", latitude=0.1, longitude=0.1),
        quantity_offered=2,
    )
    return Offer(**{**base, **kw})


def a_request() -> int:
    repo.save_facility(CLINIC)
    return repo.save_request(Request(
        facility_slug=CLINIC.slug,
        lines=(RequestLine(device_type="oxygen concentrator", quantity=1),),
    ))


def verdict_of(outcome: Outcome, status: CheckStatus) -> Verdict:
    result = CheckResult(key="frequency", label="Mains frequency", status=status,
                         detail="Runs on 60 Hz only; the grid is 50 Hz.",
                         question="What frequency?" if status is CheckStatus.NEEDS_INFO else "")
    return Verdict(outcome=outcome, results=(result,),
                   questions=(result.question,) if result.question else ())


def fake_screening(monkeypatch, *, ranked=(), rejected=()):
    calls = []

    def screen(facility_, offers, intel_for=None, *, today=None):
        calls.append(intel_for)
        return Screening(ranked=list(ranked), rejected=list(rejected))

    monkeypatch.setattr(marketplace_routes, "screen_offers", screen)
    return calls


def test_matches_screens_with_the_supply_lookup_and_lists_known_survivors(client, monkeypatch):
    request_id = a_request()
    repo.save_offer(an_offer())
    seen = []
    real = marketplace_routes.screen_offers

    def spy(facility_, offers, intel_for=None, *, today=None):
        seen.append(intel_for)
        return real(facility_, offers, intel_for=intel_for, today=today)

    monkeypatch.setattr(marketplace_routes, "screen_offers", spy)

    page = client.get(f"/request/{request_id}/matches")

    assert page.status_code == 200
    assert seen == [marketplace_routes.supply_lookup]
    assert "Ask this donor" in page.text
    assert "Won't work at" not in page.text


def test_matches_orders_known_then_rejected_then_needs_info(client, monkeypatch):
    request_id = a_request()
    known = Candidate(offer=an_offer(id=1, donor_name="Known Donor"),
                      verdict=Verdict(outcome=Outcome.WORKS, results=()))
    asking = Candidate(offer=an_offer(id=3, donor_name="Asking Donor"),
                       verdict=verdict_of(Outcome.NEEDS_INFO, CheckStatus.NEEDS_INFO))
    rejected = Candidate(offer=an_offer(id=2, donor_name="Rejected Donor"),
                         verdict=verdict_of(Outcome.WONT_WORK, CheckStatus.FAIL))
    fake_screening(monkeypatch, ranked=[known, asking], rejected=[rejected])

    text = client.get(f"/request/{request_id}/matches").text

    assert text.index("Known Donor") < text.index("Won't work at Test Clinic")
    assert text.index("Won't work at Test Clinic") < text.index("Rejected Donor")
    assert text.index("Rejected Donor") < text.index("Asking Donor")
    assert "Mains frequency" in text and "Runs on 60 Hz only" in text
    assert f'href="/request/{request_id}/offer/3"' in text
    assert "Answer 1 question first" in text
    assert text.count("Ask this donor") == 1
    assert "Buying locally instead" not in text


def test_an_advisory_renders_under_its_rejected_offer(client, monkeypatch):
    request_id = a_request()
    rejected = Candidate(offer=an_offer(id=2), verdict=verdict_of(Outcome.WONT_WORK, CheckStatus.FAIL))
    fake_screening(monkeypatch, rejected=[rejected])
    advice = Advisory(rows=[{"what": "5 LPM concentrator", "price_text": "about $290", "source": "UNICEF",
                             "url": "https://example.org/p"}],
                      as_of="2026-10-17", sold_locally_text="sold in Testland: yes")
    monkeypatch.setattr(marketplace_routes, "advisory_for", lambda c: advice)

    text = client.get(f"/request/{request_id}/matches").text

    assert "Buying locally instead" in text
    assert "about $290" in text and "sold in Testland: yes" in text


@pytest.mark.parametrize("outcome, status", [
    (Outcome.NEEDS_INFO, CheckStatus.NEEDS_INFO),
    (Outcome.WONT_WORK, CheckStatus.FAIL),
])
def test_create_match_refuses_a_candidate_that_is_not_known_to_work(client, monkeypatch, outcome, status):
    request_id = a_request()
    offer_id = repo.save_offer(an_offer())
    candidate = Candidate(offer=repo.load_offer(offer_id), verdict=verdict_of(outcome, status))
    calls = fake_screening(monkeypatch, ranked=[candidate])

    reply = client.post("/match", data={"request_id": request_id, "offer_id": offer_id},
                        follow_redirects=False)

    assert reply.status_code == 400
    assert calls == [marketplace_routes.supply_lookup]
    assert repo.matches_for_request(request_id) == []


def test_create_match_proposes_a_working_candidate(client):
    request_id = a_request()
    offer_id = repo.save_offer(an_offer())

    reply = client.post("/match", data={"request_id": request_id, "offer_id": offer_id},
                        follow_redirects=False)

    assert reply.status_code == 303
    assert len(repo.matches_for_request(request_id)) == 1


@pytest.mark.parametrize("expiry, dated", [("", False), ("2027-03-31", True)])
def test_offer_submit_writes_the_donors_expiry_answer_last(client, monkeypatch, expiry, dated):
    monkeypatch.setattr(marketplace_routes, "extract_device", lambda **kw: device())

    reply = client.post("/offer", data={
        "model_name": "Acme V100", "device_type": "oxygen concentrator",
        "country": "Testland", "donor_name": "Mercy", "donor_email": "m@example.org",
        "expiry_date": expiry,
    }, follow_redirects=False)

    assert reply.status_code == 303
    stored = repo.load_offer(int(reply.headers["location"].rsplit("/", 1)[1]))
    assert stored.device.dated_items.value is dated
    assert stored.device.expiry_date.known is dated


def test_the_facility_form_stores_staff_languages_as_names(client):
    reply = client.post("/clinic", data={
        "name": "Lang Clinic", "country": "Haiti", "staff_languages": "fr, ht",
    }, follow_redirects=False)

    assert reply.status_code == 303
    assert repo.load_facility("lang-clinic").staff_languages == ("French", "Haitian Creole")


def test_the_legacy_check_uses_the_supply_lookup_without_a_device_type(client, monkeypatch):
    device_id = repository.save_device(device())
    seen = []

    def lookup(d, device_type, country):
        seen.append((device_type, country))
        return SupplyIntel(country=country)

    monkeypatch.setattr(routes, "supply_lookup", lookup)

    reply = client.post("/check", data={"device_id": device_id, "facility_slug": "uganda-district"},
                        follow_redirects=False)

    assert reply.status_code == 303
    assert seen == [("", "Uganda")]
    page = client.get(reply.headers["location"])
    assert 'href="/offer"' in page.text and "Screen another device" in page.text
