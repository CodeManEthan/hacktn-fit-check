"""Holding an offer's units when a clinic asks a donor, and giving them back.

The races are made deterministic by running the second request's handler from
inside the first request, at the point where the first has read the database and not yet
written to it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request as HttpRequest

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.marketplace import GeoPoint, MatchState, Offer, Request, RequestLine
from app.main import app
from app.web import marketplace_routes
from tests.factories import device, facility, intel

CLINIC = facility(slug="test-clinic", name="Test Clinic", country="Testland",
                  latitude=0.0, longitude=0.0)


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'reserve.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    monkeypatch.setattr(marketplace_routes, "categorise_support", lambda *a: None)
    monkeypatch.setattr(marketplace_routes, "supply_lookup",
                        lambda device_, type_, country: intel(country=country))
    repo.save_facility(CLINIC)
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def an_offer(**kw) -> int:
    base = dict(
        donor_name="Mercy Regional", donor_email="m@example.org", device=device(),
        device_type="oxygen concentrator",
        origin=GeoPoint(country="Testland", city="Here", latitude=0.1, longitude=0.1),
        quantity_offered=1,
    )
    return repo.save_offer(Offer(**{**base, **kw}))


def a_request(device_type: str = "oxygen concentrator", quantity: int = 1) -> int:
    return repo.save_request(Request(
        facility_slug=CLINIC.slug,
        lines=(RequestLine(device_type=device_type, quantity=quantity),),
    ))


def bare_request(path: str) -> HttpRequest:
    """A request object for calling a handler directly, inside another request."""
    return HttpRequest({"type": "http", "method": "POST", "path": path,
                        "headers": [], "query_string": b""})


def ask(client, request_id: int, offer_id: int):
    return client.post("/match", data={"request_id": request_id, "offer_id": offer_id},
                       follow_redirects=False)


def test_ask_holds_the_line_quantity_when_the_offer_type_is_typed_in_capitals(client):
    request_id = a_request(quantity=1)
    offer_id = an_offer(device_type="OXYGEN CONCENTRATOR", quantity_offered=5)

    reply = ask(client, request_id, offer_id)

    assert reply.status_code == 303
    [match] = repo.matches_for_request(request_id)
    assert match.quantity == 1
    assert repo.load_offer(offer_id).quantity_reserved == 1


def test_ask_refuses_an_offer_for_a_kind_not_on_the_request(client):
    request_id = a_request()
    offer_id = an_offer(device_type="suction pump", quantity_offered=7)

    reply = ask(client, request_id, offer_id)

    assert reply.status_code == 400
    assert "not for any of the device types" in reply.text
    assert repo.matches_for_request(request_id) == []
    assert repo.load_offer(offer_id).quantity_reserved == 0


def test_a_second_ask_on_a_held_unit_says_it_is_held_not_that_it_fails(client):
    first, second = a_request(), a_request()
    offer_id = an_offer(quantity_offered=1)
    assert ask(client, first, offer_id).status_code == 303

    reply = ask(client, second, offer_id)

    assert reply.status_code == 400
    assert "already held for another request" in reply.text
    assert "no longer passes the screening" not in reply.text


def test_two_asks_racing_for_the_last_unit_make_one_match(client, monkeypatch):
    first, second = a_request(), a_request()
    offer_id = an_offer(quantity_offered=1)
    real_screen = marketplace_routes.screen_offers
    rival, entered = [], []

    def screen_then_let_the_rival_in(*args, **kwargs):
        result = real_screen(*args, **kwargs)
        if not entered:
            entered.append(True)
            rival.append(marketplace_routes.create_match(
                bare_request("/match"), request_id=second, offer_id=offer_id))
        return result

    monkeypatch.setattr(marketplace_routes, "screen_offers", screen_then_let_the_rival_in)

    late = ask(client, first, offer_id)

    assert rival[0].status_code == 303
    assert late.status_code == 400
    assert "already held for another request" in late.text
    assert len(repo.matches_for_request(second)) == 1
    assert repo.matches_for_request(first) == []
    assert repo.load_offer(offer_id).quantity_reserved == 1


def test_the_repo_holds_the_last_unit_once(client):
    first, second = a_request(), a_request()
    offer_id = an_offer(quantity_offered=1)
    ask(client, first, offer_id)
    [held] = repo.matches_for_request(first)
    copy = replace(held, id=None, request_id=second, token="another-token")

    with pytest.raises(repo.StockHeld):
        repo.save_match_reserving(copy)

    assert repo.matches_for_request(second) == []
    assert repo.load_offer(offer_id).quantity_reserved == 1


def test_two_no_answers_on_one_link_release_its_unit_once(client, monkeypatch):
    first, second = a_request(), a_request()
    offer_id = an_offer(quantity_offered=2)
    assert ask(client, first, offer_id).status_code == 303
    assert ask(client, second, offer_id).status_code == 303
    assert repo.load_offer(offer_id).quantity_reserved == 2
    [declined] = repo.matches_for_request(first)
    real_load = repo.load_match_by_token
    rival, entered = [], []

    def load_then_let_the_rival_in(token):
        match = real_load(token)
        if not entered:
            entered.append(True)
            rival.append(marketplace_routes.confirm_submit(
                bare_request(f"/confirm/{token}"), token=token, answer="no"))
        return match

    monkeypatch.setattr(repo, "load_match_by_token", load_then_let_the_rival_in)

    late = client.post(f"/confirm/{declined.token}", data={"answer": "no"},
                       follow_redirects=False)

    assert rival[0].status_code == 303
    assert late.status_code == 400
    assert repo.load_match(declined.id).state is MatchState.WITHDRAWN
    # The other request's match still waits on one unit, so one stays held.
    assert repo.load_offer(offer_id).quantity_reserved == 1


def test_a_yes_holds_the_unit_and_a_no_gives_it_back(client):
    yes_req, no_req = a_request(), a_request()
    offer_id = an_offer(quantity_offered=2)
    ask(client, yes_req, offer_id)
    ask(client, no_req, offer_id)
    [kept] = repo.matches_for_request(yes_req)
    [dropped] = repo.matches_for_request(no_req)

    client.post(f"/confirm/{kept.token}", data={"answer": "yes"}, follow_redirects=False)
    client.post(f"/confirm/{dropped.token}", data={"answer": "no"}, follow_redirects=False)

    assert repo.load_match(kept.id).state is MatchState.CONFIRMED
    assert repo.load_match(dropped.id).state is MatchState.WITHDRAWN
    assert repo.load_offer(offer_id).quantity_reserved == 1
