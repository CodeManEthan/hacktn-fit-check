"""[B15] The matches, candidate and offer pages after the UI merge (design §13):
miles beside kilometres, a genuine 0 km shown, a double confirm answering a
page, and the offer form keeping "Blank if nothing dated." with its divs
balanced. Seeded demo data in a fresh SQLite file; no model is called.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.domain.marketplace import MatchState
from app.main import app
from app.matching import verification
from app.matching.pipeline import MILES_PER_KM, Candidate
from app.web import marketplace_routes
from scripts import seed_demo

from tests.test_pipeline import _clean_verdict, offer


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    path = tmp_path_factory.mktemp("b15") / "demo.db"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db, "SETTINGS", replace(SETTINGS, database_url=f"sqlite:///{path}"))
        mp.setattr(db, "_engine", None)
        mp.setenv("GUDID_LOOKUP", "off")
        seeded = seed_demo.main()
        offers = repo.open_offers_for_type(seed_demo.HERO_TYPE)
        by_model = {o.device.model.value: o.id for o in offers if o.device.model.known}
        unnamed = next(o.id for o in offers if not o.device.model.known)
        ids = {"request": seeded["request_id"], "A": by_model["525DS"], "B": by_model["525KS"],
               "C": unnamed}
        yield {"client": TestClient(app), "ids": ids}
        if db._engine is not None:
            db._engine.dispose()


def matches_page(demo) -> str:
    response = demo["client"].get(f"/request/{demo['ids']['request']}/matches")
    assert response.status_code == 200
    return response.text


# ---- miles, display only --------------------------------------------------------

def test_distance_miles_is_the_same_distance_and_none_stays_none():
    located = Candidate(offer=offer(id=1), verdict=_clean_verdict(), distance_km=100.0)
    assert located.distance_miles == pytest.approx(100.0 * MILES_PER_KM)
    assert Candidate(offer=offer(id=1), verdict=_clean_verdict()).distance_miles is None
    zero = Candidate(offer=offer(id=1), verdict=_clean_verdict(), distance_km=0.0)
    assert zero.distance_miles == 0.0


def test_matches_shows_km_and_mi_for_every_unit_in_the_order_b_a_c(demo):
    body = matches_page(demo)
    ids = demo["ids"]
    # Leeds to Kayunga, as docs/demo.md reads it, now with its miles.
    assert re.search(r"<b>6694 km</b><span>41\d\d mi</span>", body)
    assert body.count(" mi</span>") == 3
    at = {w: body.index(f'/request/{ids["request"]}/offer/{ids[w]}"') for w in "ABC"}
    assert at["B"] < at["A"] < at["C"]
    # The option-row look, with nothing to tick and nothing to submit but the
    # one donor's "Ask this donor".
    assert 'type="radio"' not in body
    assert body.count('class="option unit') == 3
    assert "Won't work at Kayunga District Hospital" in body
    assert "Answer 11 questions first" in body
    assert "Buy locally instead?" in body


def test_a_genuine_zero_km_still_shows(demo, monkeypatch):
    real = marketplace_routes.screen_offers

    def at_the_door(*args, **kwargs):
        found = real(*args, **kwargs)
        found.ranked[:] = [replace(c, distance_km=0.0) for c in found.ranked]
        return found

    monkeypatch.setattr(marketplace_routes, "screen_offers", at_the_door)
    body = matches_page(demo)
    assert "<b>0 km</b><span>0 mi</span>" in body


def test_the_candidate_page_shows_km_and_mi(demo):
    ids = demo["ids"]
    body = demo["client"].get(f"/request/{ids['request']}/offer/{ids['B']}").text
    assert re.search(r'class="ident-far">6694 km &middot; 41\d\d mi to Kayunga District Hospital',
                     body)
    assert 'href="/static/matches.css"' in body


def test_the_page_stylesheet_is_served(demo):
    response = demo["client"].get("/static/matches.css")
    assert response.status_code == 200 and ".option.unit" in response.text


# ---- the offer form -----------------------------------------------------------------

def test_offer_form_keeps_its_notes_and_balances_its_divs(demo):
    body = demo["client"].get("/offer").text
    assert "Blank if nothing dated." in body
    assert "After this date the offer stops being matched." in body
    assert "Used only to ask whether the stock still exists" in body
    assert 'name="extraction_id"' in body and 'name="library_slug"' in body
    markup = re.sub(r"<script>.*?</script>", "", body, flags=re.S)
    form = markup[markup.index('<form method="post" action="/offer"'):markup.index("</form>")]
    assert form.count("<div") == form.count("</div>")


# ---- a double confirm answers a page ---------------------------------------------

def test_invalid_transition_is_a_value_error():
    assert issubclass(verification.InvalidTransition, ValueError)


def test_a_second_confirm_is_an_error_page_not_a_500(demo):
    client, ids = demo["client"], demo["ids"]
    proposed = client.post("/match", data={"request_id": ids["request"], "offer_id": ids["B"]},
                           follow_redirects=False)
    assert proposed.status_code == 303
    match = repo.load_match(proposed.headers["location"].rsplit("/", 1)[1])
    first = client.post(f"/confirm/{match.token}", data={"answer": "yes"},
                        follow_redirects=False)
    assert first.status_code == 303
    assert repo.load_match(match.id).state is MatchState.CONFIRMED
    again = client.post(f"/confirm/{match.token}", data={"answer": "yes"},
                        follow_redirects=False)
    assert again.status_code == 400
    assert "cannot move from confirmed" in again.text
