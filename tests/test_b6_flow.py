"""[B6] §7.1 role toggle and landing, §7.2 candidate verdict page, and the
matches page links, on the seeded demo data (scripts/seed_demo.py into a
fresh SQLite file). No model is called; GUDID is off under pytest.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.domain.provenance import Provenance, fact
from app.main import app
from app.matching import supply_order
from app.web import marketplace_routes
from scripts import seed_demo

KAYUNGA = seed_demo.KAYUNGA


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    path = tmp_path_factory.mktemp("b6") / "demo.db"
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


def page(demo, which: str) -> str:
    ids = demo["ids"]
    response = demo["client"].get(f"/request/{ids['request']}/offer/{ids[which]}")
    assert response.status_code == 200
    return response.text


def check_rows(body: str) -> list[str]:
    return re.findall(r'data-check="([a-z_]+)"', body)


# ---- §7.2 the candidate verdict page ------------------------------------------

def test_b_works_if_with_fifteen_cited_checks_and_the_send_with_list(demo):
    body = page(demo, "B")
    assert "Ship with" in body
    assert len(check_rows(body)) == 15 and len(set(check_rows(body))) == 15
    # Each library-cited check links to its page in the manufacturer's guide.
    assert "SE-525KS-1 Rev D (2025), p.11</a>" in body
    assert "#page=11" in body
    # The send-with list: Sollatek covers, the Houston AVR fails its own plate.
    assert re.search(r'pill good">covers</span> Sollatek AVS30', body)
    assert re.search(r"won't work here itself</span> automatic voltage regulator", body)
    assert "2 years of MC44D-605" in body
    packet = f'href="/packet/{demo["ids"]["B"]}?facility={KAYUNGA}"'
    assert packet in body and "Generate handover packet" in body
    assert 'href="/offer"' in body and "Screen another device" in body
    assert "What blocks it" not in body and "Buy locally instead?" not in body
    assert "Ask this donor" in body


def test_a_rejected_shows_its_blocker_and_the_advisory_but_no_packet(demo):
    body = page(demo, "A")
    assert "Do not ship" in body
    assert len(check_rows(body)) == 15
    assert "What blocks it at Kayunga District Hospital" in body
    assert "Rated 60 Hz, but the grid is 50 Hz" in body
    assert "Buy locally instead?" in body and "about $290" in body
    assert "/packet/" not in body and "Ask this donor" not in body
    # Its open supply questions go to the nonprofit's form, model filled in.
    assert "/supply/Uganda?device_type=oxygen+concentrator&amp;model=525DS&amp;back=" in body


def test_c_needs_info_lists_its_questions_with_the_supply_ones_linked(demo):
    body = page(demo, "C")
    assert "Needs info" in body
    assert "Unnamed oxygen concentrator" in body
    assert len(check_rows(body)) == 15
    assert "Can spare parts for this device be bought in Uganda?" in body
    assert "/supply/Uganda?device_type=oxygen+concentrator&amp;model=&amp;back=" in body
    assert "(for the donor)" in body          # the plate questions are the donor's
    assert "Ask this donor" not in body
    assert f'href="/packet/{demo["ids"]["C"]}?facility={KAYUNGA}"' in body


def test_the_support_row_links_the_gudid_record(demo, monkeypatch):
    record = "https://accessgudid.nlm.nih.gov/devices/00000000000000"

    def affirmed(device, device_type, country):
        intel = supply_order.supply_lookup(device, device_type, country)
        return replace(intel, manufacturer_supported=fact(True, Provenance.REGISTRY, record))

    monkeypatch.setattr(marketplace_routes, "supply_lookup", affirmed)
    body = page(demo, "B")
    support = body[body.index('data-check="support"'):]
    support = support[:support.index("</tr>")]
    assert f'href="{record}"' in support and "FDA GUDID" in support


def test_an_unknown_request_or_offer_is_an_error(demo):
    client, ids = demo["client"], demo["ids"]
    assert client.get(f"/request/999/offer/{ids['B']}").status_code == 400
    assert client.get(f"/request/{ids['request']}/offer/999").status_code == 400


# ---- the matches page ---------------------------------------------------------

def test_matches_links_each_candidate_and_its_packet(demo):
    ids = demo["ids"]
    body = demo["client"].get(f"/request/{ids['request']}/matches").text
    for which in ("A", "B", "C"):
        assert f'href="/request/{ids["request"]}/offer/{ids[which]}"' in body
    assert f'/packet/{ids["B"]}?facility={KAYUNGA}' in body
    assert f'/packet/{ids["C"]}?facility={KAYUNGA}' in body
    assert f'/packet/{ids["A"]}?' not in body              # rejected: no packet
    assert "Record the nonprofit's answer" in body
    assert "already been removed" not in body
    assert "stay on the page" in body
    # The order of [F3]: B first, then A among the won't-works, then C.
    at = {w: body.index(f'/request/{ids["request"]}/offer/{ids[w]}"') for w in "ABC"}
    assert at["B"] < at["A"] < at["C"]


# ---- §7.1 role toggle and landing ------------------------------------------------

def test_the_role_cookie_is_set_and_sends_you_back(demo):
    client = TestClient(app)
    response = client.post("/role", data={"role": "donor", "back": "/offer"},
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/offer"
    assert "role=donor" in response.headers["set-cookie"]


def test_the_role_only_returns_to_this_site(demo):
    response = TestClient(app).post("/role", data={"role": "clinic", "back": "//evil.example"},
                                    follow_redirects=False)
    assert response.headers["location"] == "/"


def test_each_role_recolours_the_bar_and_hides_nothing(demo):
    for role in ("donor", "clinic", "nonprofit"):
        client = TestClient(app, cookies={"role": role})
        body = client.get("/").text
        assert f'<body data-role="{role}">' in body
        assert f'value="{role}" class="role-btn role-btn-{role}"\n                aria-pressed="true"' in body
        assert 'href="/offer"' in body and 'href="/request"' in body


def test_only_the_nonprofit_role_adds_supply_and_review_to_the_bar(demo):
    nonprofit = TestClient(app, cookies={"role": "nonprofit"}).get("/offer").text
    donor = TestClient(app, cookies={"role": "donor"}).get("/offer").text
    bar = nonprofit[:nonprofit.index("</header>")]
    assert 'href="/supply"' in bar and 'href="/review"' in bar
    assert 'href="/supply"' not in donor[:donor.index("</header>")]


def test_an_unknown_role_cookie_is_ignored(demo):
    body = TestClient(app, cookies={"role": "admin"}).get("/").text
    assert "<body>" in body and "data-role" not in body.split("<body", 1)[1][:30]


def test_the_landing_shows_two_intake_forms_and_the_operator_between_them(demo):
    body = demo["client"].get("/").text
    donor, operator, clinic = (body.index(s) for s in
                               ("Intake form 1", "The nonprofit", "Intake form 2"))
    assert donor < operator < clinic
    assert 'class="middlemen"' in body and "freight forwarder" in body
    chips = re.findall(r'href="/request\?facility=([a-z-]+)"', body)
    assert chips[0] == KAYUNGA and len(chips) == 3


def test_the_supply_desk_lists_each_clinic_country(demo):
    body = demo["client"].get("/supply").text
    assert 'href="/supply/Uganda"' in body
