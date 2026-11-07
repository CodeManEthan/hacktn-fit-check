"""DEMO_PROTECT_SEED: one visitor can't change the shared demo for the next.

With the setting on, a write to a row the seed wrote, or any supply-desk save,
answers 403 with the seeded-data page and writes nothing; the pages show the
button disabled with the same sentence. Visitors' own rows work as usual.
With it off (the default) nothing is refused.

Every write route is classed in WRITE_ROUTES, so a route added later fails
test_every_write_route_is_classed until someone says whether it is refused.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS, demo_protect_seed
from app.data import db
from app.data import marketplace_repo as repo
from app.main import app
from app.web.demo_guard import SEEDED_MESSAGE
from scripts import seed_demo

# Every route that writes, with whether DEMO_PROTECT_SEED refuses it and why.
WRITE_ROUTES = {
    ("POST", "/role"): ("open", "sets the viewer's own cookie; no row is written"),
    ("POST", "/offer"): ("open", "a new offer; a seeded row is never overwritten"),
    ("POST", "/clinic"): ("open", "a new clinic; save_facility(new=True) never takes a seeded slug"),
    ("POST", "/request"): ("open", "a new request; the seeded request is found by age, oldest first"),
    ("POST", "/match"): ("refused", "on a seeded offer or the seeded request"),
    ("POST", "/confirm/{token}"): ("refused", "on a hold of a seeded offer, which moves its stock"),
    ("POST", "/offer/extract"): ("open", "caches the visitor's own extraction"),
    ("POST", "/review/{slug}"): ("refused", "on a seeded clinic: its tier is part of the verdict"),
    ("POST", "/supply/{country}"): (
        "refused", "always: the lookup falls back from model to type, so any answer can move a seeded verdict"),
    ("POST", "/offer/prefill"): ("open", "reads the library; writes nothing"),
    ("POST", "/check"): ("open", "adds a history row; changes no seeded row or seeded verdict"),
    ("POST", "/request/upload"): ("open", "a new request, as POST /request"),
    ("POST", "/offer/csv"): ("open", "screens the visitor's file; holds it in memory"),
    ("POST", "/offer/csv/{token}/post"): ("open", "new offers, as POST /offer"),
    ("GET", "/packet/{offer_id}?rebuild=1"): ("refused", "on a seeded offer: drops its stored card"),
}

PAGE_HEADING = "This part of the demo is read-only"


def _write_routes(routes) -> set[tuple[str, str]]:
    """Each (method, path) that is not a plain read, through included routers."""
    found: set[tuple[str, str]] = set()
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            found |= _write_routes(inner.routes)
            continue
        for method in getattr(route, "methods", None) or ():
            if method not in {"GET", "HEAD", "OPTIONS"}:
                found.add((method, route.path))
    return found


def test_every_write_route_is_classed():
    classed = {key for key in WRITE_ROUTES if "?" not in key[1]}
    assert _write_routes(app.router.routes) == classed


def test_the_setting_is_off_unless_it_says_on(monkeypatch):
    assert not demo_protect_seed()
    for value in ("off", "", "no", "0"):
        monkeypatch.setenv("DEMO_PROTECT_SEED", value)
        assert not demo_protect_seed()
    for value in ("on", "ON", " true "):
        monkeypatch.setenv("DEMO_PROTECT_SEED", value)
        assert demo_protect_seed()


# ---- a seeded database ---------------------------------------------------------

@pytest.fixture
def demo(tmp_path, monkeypatch, capsys):
    path = tmp_path / "demo.db"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=f"sqlite:///{path}"))
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setenv("GUDID_LOOKUP", "off")
    request_id = seed_demo.main()["request_id"]
    capsys.readouterr()
    offers = repo.open_offers_for_type(seed_demo.HERO_TYPE)
    by_model = {o.device.model.value: o for o in offers if o.device.model.known}
    yield {"client": TestClient(app), "path": path, "request_id": request_id,
           "A": by_model["525DS"], "B": by_model["525KS"],
           "C": next(o for o in offers if not o.device.model.known)}
    if db._engine is not None:
        db._engine.dispose()


def _protect(monkeypatch, on: bool = True) -> None:
    monkeypatch.setenv("DEMO_PROTECT_SEED", "on" if on else "off")


def _snapshot(path) -> str:
    connection = sqlite3.connect(path)
    try:
        return "\n".join(connection.iterdump())
    finally:
        connection.close()


def _refused(reply) -> None:
    assert reply.status_code == 403
    assert PAGE_HEADING in reply.text
    assert SEEDED_MESSAGE in reply.text


def _visitor_offer(demo) -> int:
    """A visitor's copy of the 525KS, under the visitor's own name."""
    return repo.save_offer(replace(demo["B"], id=None, donor_name="A visitor",
                                   donor_email="visitor@example.org", quantity_reserved=0))


def _visitor_request(demo) -> int:
    """A visitor's own request at Kayunga for one oxygen concentrator."""
    reply = demo["client"].post("/request", data={
        "facility_slug": seed_demo.KAYUNGA, "device_type": seed_demo.HERO_TYPE, "quantity": "1",
    }, follow_redirects=False)
    assert reply.status_code == 303
    return int(reply.headers["location"].split("/")[2])


def _hold(demo, offer_id: int, request_id: int) -> str:
    """Ask for an offer, with the setting off, and return the hold's token."""
    reply = demo["client"].post("/match", data={"request_id": request_id, "offer_id": offer_id},
                                follow_redirects=False)
    assert reply.status_code == 303, reply.text
    return repo.load_match(reply.headers["location"].rsplit("/", 1)[1]).token


# ---- refused, with the setting on ----------------------------------------------

def _refused_cases(demo) -> list[tuple[str, str, dict]]:
    visitor_request = _visitor_request(demo)
    visitor_offer = _visitor_offer(demo)
    token = _hold(demo, demo["B"].id, demo["request_id"])
    seeded_request, b = demo["request_id"], demo["B"].id
    return [
        ("POST", "/match", {"request_id": seeded_request, "offer_id": b}),
        ("POST", "/match", {"request_id": visitor_request, "offer_id": b}),
        ("POST", "/match", {"request_id": seeded_request, "offer_id": visitor_offer}),
        ("POST", f"/confirm/{token}", {"answer": "yes"}),
        ("POST", f"/confirm/{token}", {"answer": "no"}),
        ("POST", f"/review/{seed_demo.KAYUNGA}", {"decision": "contradicted by a check"}),
        ("POST", "/supply/Uganda", {"device_type": seed_demo.HERO_TYPE, "model": "525KS",
                                    "consumables_available": "yes", "recorded_by": "visitor"}),
        ("POST", "/supply/Kenya", {"device_type": "ventilator", "recorded_by": "visitor"}),
        ("GET", f"/packet/{b}?facility={seed_demo.KAYUNGA}&rebuild=1", {}),
    ]


def test_each_refused_write_answers_403_and_writes_nothing(demo, monkeypatch):
    cases = _refused_cases(demo)
    _protect(monkeypatch)
    client = demo["client"]
    for method, url, data in cases:
        before = _snapshot(demo["path"])
        reply = (client.post(url, data=data, follow_redirects=False) if method == "POST"
                 else client.get(url, follow_redirects=False))
        _refused(reply)
        assert _snapshot(demo["path"]) == before, (method, url)


def test_every_route_classed_refused_has_a_case(demo):
    refused = {(method, path.split("{")[0]) for (method, path), (kind, _) in WRITE_ROUTES.items()
               if kind == "refused"}
    cases = {(method, url) for method, url, _ in _refused_cases(demo)}
    for method, prefix in refused:
        assert any(m == method and u.startswith(prefix) for m, u in cases), (method, prefix)


@pytest.mark.parametrize("answer", [
    # Ways round the seeded key: another type with the 525KS model, the type with no
    # model or another model, and the 525DS. Each one can move a seeded verdict.
    {"device_type": "pulse oximeter", "model": "525KS"},
    {"device_type": seed_demo.HERO_TYPE, "model": ""},
    {"device_type": seed_demo.HERO_TYPE, "model": "Another model"},
    {"device_type": seed_demo.HERO_TYPE, "model": "525DS"},
])
def test_no_supply_answer_gets_past_the_desk(demo, monkeypatch, answer):
    client = demo["client"]
    page = f"/request/{demo['request_id']}/matches"
    _protect(monkeypatch)
    before = client.get(page).text
    reply = client.post("/supply/Uganda", data={
        **answer, "consumables_available": "no", "spare_parts_available": "no",
        "sold_locally": "no", "manufacturer_supported": "no", "recorded_by": "visitor",
    }, follow_redirects=False)
    _refused(reply)
    assert client.get(page).text == before


def test_with_the_setting_off_the_same_answer_does_change_the_page(demo):
    """The bypass test above has teeth: unprotected, a type-wide answer moves a verdict."""
    client = demo["client"]
    page = f"/request/{demo['request_id']}/matches"
    before = client.get(page).text
    reply = client.post("/supply/Uganda", data={
        "device_type": seed_demo.HERO_TYPE, "model": "", "consumables_available": "no",
        "spare_parts_available": "no", "sold_locally": "no", "manufacturer_supported": "no",
        "recorded_by": "visitor",
    }, follow_redirects=False)
    assert reply.status_code == 303
    assert client.get(page).text != before


# ---- the pages show the button disabled ------------------------------------------

LIVE_ASK = '<button class="go" type="submit">Ask this donor</button>'


def _pages(demo) -> list[str]:
    request_id, b = demo["request_id"], demo["B"].id
    return [
        f"/request/{request_id}/matches",
        f"/request/{request_id}/offer/{b}",
        f"/review/{seed_demo.KAYUNGA}",
        "/supply/Uganda?device_type=oxygen+concentrator&model=525KS",
        f"/packet/{b}?facility={seed_demo.KAYUNGA}",
    ]


def _pages_then_confirm(demo) -> list[str]:
    """Each page's body, then the confirm page of a hold of the seeded 525KS."""
    client = demo["client"]
    bodies = [client.get(url).text for url in _pages(demo)]
    with pytest.MonkeyPatch.context() as mp:
        _protect(mp, on=False)
        token = _hold(demo, demo["B"].id, _visitor_request(demo))
    return [*bodies, client.get(f"/confirm/{token}").text]


def test_the_pages_show_a_disabled_button_and_the_reason(demo, monkeypatch):
    _protect(monkeypatch)
    matches = demo["client"].get(f"/request/{demo['request_id']}/matches").text
    assert LIVE_ASK not in matches and "disabled" in matches
    for url, body in zip([*_pages(demo), "/confirm"], _pages_then_confirm(demo)):
        assert SEEDED_MESSAGE in body, url
        assert "rebuild=1" not in body, url


def test_with_the_setting_off_the_pages_keep_their_live_buttons(demo):
    matches = demo["client"].get(f"/request/{demo['request_id']}/matches").text
    assert LIVE_ASK in matches
    for url, body in zip([*_pages(demo), "/confirm"], _pages_then_confirm(demo)):
        assert SEEDED_MESSAGE not in body, url


# ---- open, with the setting on ---------------------------------------------------

def test_a_visitors_own_offer_can_still_be_asked_for_and_answered(demo, monkeypatch):
    visitor_request = _visitor_request(demo)
    visitor_offer = _visitor_offer(demo)
    _protect(monkeypatch)
    client = demo["client"]
    page = client.get(f"/request/{visitor_request}/matches").text
    assert page.count(LIVE_ASK) == 1

    token = _hold(demo, visitor_offer, visitor_request)
    assert repo.load_offer(visitor_offer).quantity_available == 0
    assert SEEDED_MESSAGE not in client.get(f"/confirm/{token}").text
    reply = client.post(f"/confirm/{token}", data={"answer": "no"}, follow_redirects=False)
    assert reply.status_code == 303
    assert repo.load_offer(visitor_offer).quantity_available == 1


def test_a_visitors_clinic_request_and_review_still_work(demo, monkeypatch):
    _protect(monkeypatch)
    client = demo["client"]
    reply = client.post("/clinic", data={"name": seed_demo.FACILITIES[0].name, "country": "Uganda"},
                        follow_redirects=False)
    assert reply.status_code == 303
    slug = reply.headers["location"].split("facility=")[1]
    assert slug not in {f.slug for f in seed_demo.FACILITIES}
    assert client.post(f"/review/{slug}", data={"decision": "approved by a reviewer"},
                       follow_redirects=False).status_code == 303
    assert SEEDED_MESSAGE not in client.get(f"/review/{slug}").text
    assert _visitor_request(demo) != demo["request_id"]


def test_the_seeded_request_stays_the_example_after_a_visitor_asks_for_the_same(demo, monkeypatch):
    visitor_request = _visitor_request(demo)
    _protect(monkeypatch)
    reply = demo["client"].get("/example", follow_redirects=False)
    assert reply.headers["location"] == f"/request/{demo['request_id']}/matches"
    assert visitor_request != demo["request_id"]


def test_with_the_setting_off_seeded_rows_take_writes_as_before(demo):
    client = demo["client"]
    assert client.post(f"/review/{seed_demo.KAYUNGA}", data={"decision": "approved by a reviewer"},
                       follow_redirects=False).status_code == 303
    token = _hold(demo, demo["B"].id, demo["request_id"])
    assert client.post(f"/confirm/{token}", data={"answer": "no"},
                       follow_redirects=False).status_code == 303
    reply = client.get(f"/packet/{demo['B'].id}?facility={seed_demo.KAYUNGA}&rebuild=1")
    assert reply.status_code == 200


# ---- the list for the operator ---------------------------------------------------

def test_visitor_rows_lists_what_moves_the_seeded_demo_and_writes_nothing(demo, capsys):
    from tests.test_deploy_scripts import _load

    client = demo["client"]
    _hold(demo, demo["B"].id, demo["request_id"])
    for data in ({"device_type": seed_demo.HERO_TYPE, "model": "", "consumables_available": "no",
                  "spare_parts_available": "no", "sold_locally": "no",
                  "manufacturer_supported": "no", "recorded_by": "visitor one"},
                 {"device_type": "ventilator", "model": "", "consumables_available": "yes",
                  "recorded_by": "visitor two"}):
        assert client.post("/supply/Uganda", data=data, follow_redirects=False).status_code == 303
    capsys.readouterr()

    before = _snapshot(demo["path"])
    assert _load("visitor_rows").main() == 0
    out = capsys.readouterr().out
    assert _snapshot(demo["path"]) == before

    assert "supply answers the seed didn't write: 2" in out
    lines = out.splitlines()
    one = next(line for line in lines if "by visitor one" in line)
    two = next(line for line in lines if "by visitor two" in line)
    assert "CHANGES" in one and f"offer {demo['C'].id} needs info" in one
    assert "changes no seeded verdict" in two
    assert "holds on a seeded offer or the seeded request: 1" in out
    assert f"offer {demo['B'].id} for request {demo['request_id']}" in out and "HOLDS STOCK" in out


def test_visitor_rows_on_the_seed_alone_lists_nothing(demo, capsys):
    from tests.test_deploy_scripts import _load

    assert _load("visitor_rows").main() == 0
    out = capsys.readouterr().out
    assert "changed from the seed's answers alone: nothing" in out
    assert "supply answers the seed didn't write: 0" in out
    assert "holds on a seeded offer or the seeded request: 0" in out
