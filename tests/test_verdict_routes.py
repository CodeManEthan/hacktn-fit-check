"""The matches page and create_match on real screening data (design §2.3, §2.4).

Same harness as tests/test_contract_routes.py: per-test SQLite, no lifespan,
no model calls. The screening here is the real one.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.device import DeviceFacts
from app.domain.marketplace import GeoPoint, Offer, Request, RequestLine
from app.domain.provenance import UNKNOWN, Provenance, fact
from app.main import app
from app.web import marketplace_routes
from tests.factories import device, facility, intel

DONOR = Provenance.DONOR_STATED
CLINIC = facility(slug="kayunga-test", name="Kayunga Test", country="Testland",
                  latitude=0.0, longitude=0.0, outages_per_week=6,
                  has_voltage_stabilizer=False, has_generator=True)


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'routes.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    monkeypatch.setattr(marketplace_routes, "categorise_support", lambda *a: None)
    monkeypatch.setattr(marketplace_routes, "supply_lookup",
                        lambda d, t, country: intel(country=country))
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def an_offer(donor: str, dev: DeviceFacts, **kw) -> Offer:
    base = dict(donor_name=donor, donor_email="d@example.org", device=dev,
                device_type="oxygen concentrator",
                origin=GeoPoint(country="Testland", city="Here", latitude=0.1, longitude=0.1),
                quantity_offered=1)
    return Offer(**{**base, **kw})


def a_request() -> int:
    repo.save_facility(CLINIC)
    return repo.save_request(Request(
        facility_slug=CLINIC.slug,
        lines=(RequestLine(device_type="oxygen concentrator", quantity=2),),
    ))


def stabiliser(donor: str, low: int, high: int, hz: list[int]) -> Offer:
    dev = DeviceFacts(manufacturer=fact(donor, DONOR), model=fact("AVR", DONOR),
                      voltage_min_v=fact(low, DONOR), voltage_max_v=fact(high, DONOR),
                      frequencies_hz=fact(hz, DONOR))
    return an_offer(donor, dev, device_type="voltage stabiliser",
                    support_kind="voltage stabiliser")


def test_the_page_shows_known_then_rejected_with_its_blocker_then_asking(client):
    request_id = a_request()
    repo.save_offer(an_offer("Known Donor", device()))
    repo.save_offer(an_offer("Sixty Donor", device(frequencies_hz=fact([60], DONOR))))
    asking_id = repo.save_offer(an_offer("Asking Donor", device(frequencies_hz=UNKNOWN,
                                                                 voltage_min_v=UNKNOWN), quantity_offered=5))
    repo.save_offer(stabiliser("Good Stabiliser", 160, 260, [50]))
    repo.save_offer(stabiliser("Houston Surplus", 110, 120, [60]))

    text = client.get(f"/request/{request_id}/matches").text

    assert text.index("Known Donor") < text.index("Won't work at Kayunga Test")
    assert text.index("Won't work at Kayunga Test") < text.index("Sixty Donor")
    assert text.index("Sixty Donor") < text.index("Asking Donor")
    assert "Supply frequency" in text and "A motor built for one runs slow" in text
    assert "Answer 2 questions first" in text
    assert f'href="/request/{request_id}/offer/{asking_id}"' in text
    assert text.count("Ask this donor") == 1
    # Coverage counts the known offer only, not the asking one's five units.
    assert "1 of 2 available" in text
    flat = " ".join(text.split())
    assert "1 is known to work at Kayunga Test, 1 won't, and 1 needs answers first" in flat
    # The stabiliser self-check, under the known offer's requirement.
    assert "Good Stabiliser" in flat
    assert "Won't work here itself: Houston Surplus AVR (Houston Surplus)" in flat


def test_create_match_refuses_an_asking_offer_and_a_rejected_one(client):
    request_id = a_request()
    asking = repo.save_offer(an_offer("Asking Donor", device(frequencies_hz=UNKNOWN)))
    rejected = repo.save_offer(an_offer("Sixty Donor", device(frequencies_hz=fact([60], DONOR))))

    for offer_id in (asking, rejected):
        reply = client.post("/match", data={"request_id": request_id, "offer_id": offer_id},
                            follow_redirects=False)
        assert reply.status_code == 400

    assert repo.matches_for_request(request_id) == []
