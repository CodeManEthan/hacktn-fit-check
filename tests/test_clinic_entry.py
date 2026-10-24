"""Every door a clinic uses to apply leads to GET /clinic, the registration form.

Each test follows the real link or redirect from the page it sits on and checks
that what loads is the application form itself (its action and heading), not
merely a 200. Links meant for a clinic that is already registered (the
landing's quiet "Request for a registered clinic", the matches pages) stay on
/request and are not tested here.
"""
from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.main import app
from app.web import marketplace_routes

FORM_ACTION = '<form method="post" action="/clinic" class="form">'
FORM_HEADING = "<h1>Facility specification</h1>"


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'entry.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    monkeypatch.setattr(marketplace_routes, "categorise_support", lambda *a: None)
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def assert_application_form(response) -> None:
    assert response.status_code == 200, response.text[:300]
    assert response.url.path == "/clinic"
    assert FORM_ACTION in response.text
    assert FORM_HEADING in response.text


def clinic_links(body: str) -> list[str]:
    """Every href on the page that points at the registration form."""
    return re.findall(r'href="(/clinic(?:\?[^"]*)?)"', body)


def follow_clinic_link(client: TestClient, page: str, marker: str) -> None:
    """Find the /clinic link next to `marker` on `page`, follow it, check the form."""
    body = client.get(page).text
    at = body.index(marker)
    nearby = body[max(0, at - 300): at + 300]
    links = clinic_links(nearby)
    assert links, f"no /clinic link near {marker!r} on {page}"
    assert_application_form(client.get(links[0]))


# ---- the header's role toggle ---------------------------------------------

def test_the_clinic_role_button_opens_the_application_form(client):
    reply = client.post("/role", data={"role": "clinic", "back": "/impact"},
                        follow_redirects=False)
    assert reply.status_code == 303 and reply.headers["location"] == "/clinic"
    assert "role=clinic" in reply.headers["set-cookie"]
    assert_application_form(client.post("/role", data={"role": "clinic", "back": "/"}))


# ---- links on pages --------------------------------------------------------

def test_the_landing_clinic_card_opens_the_application_form(client):
    body = client.get("/").text
    cta = re.search(r'class="lander-cta" href="([^"]+)">Register your clinic<', body)
    assert cta and cta.group(1) == "/clinic"
    assert_application_form(client.get(cta.group(1)))


def test_the_contact_page_link_opens_the_application_form(client):
    follow_clinic_link(client, "/contact", "register a clinic</a>")


def test_the_help_page_link_opens_the_application_form(client):
    follow_clinic_link(client, "/help", "register your clinic</a>")


def test_the_about_page_link_opens_the_application_form(client):
    follow_clinic_link(client, "/about", "Register your clinic</a> to start")


def test_the_inventory_csv_page_with_no_clinics_links_the_application_form(client):
    follow_clinic_link(client, "/offer/csv", "Register one</a>")


@pytest.mark.parametrize("page", ["/request", "/request/upload"])
def test_the_request_pages_offer_registration_for_an_unlisted_clinic(client, page):
    follow_clinic_link(client, page, "Clinic not listed?")


# ---- error pages -----------------------------------------------------------

def back_link(body: str) -> str:
    match = re.search(r'<a class="go" href="([^"]+)"[^>]*>Go back</a>', body)
    assert match, body[:400]
    return match.group(1)


def test_uploading_for_an_unregistered_clinic_sends_back_to_the_form(client):
    reply = client.post("/request/upload",
                        data={"facility_slug": "nowhere", "wanted_text": "2 monitors"})
    assert reply.status_code == 400
    assert "That facility is not registered. Register it first." in reply.text
    assert back_link(reply.text) == "/clinic"
    assert_application_form(client.get(back_link(reply.text)))


def test_requesting_for_an_unregistered_clinic_saves_nothing_and_sends_to_the_form(client):
    reply = client.post("/request", data={"facility_slug": "nowhere",
                                          "device_type": "ventilator", "quantity": "1"},
                        follow_redirects=False)
    assert reply.status_code == 400
    assert "That facility is not registered. Register it first." in reply.text
    assert back_link(reply.text) == "/clinic"
    assert repo.list_open_requests() == []
    assert_application_form(client.get(back_link(reply.text)))


# ---- submitting the form ---------------------------------------------------

def test_registering_lands_on_the_list_upload_with_the_new_clinic_chosen(client):
    reply = client.post("/clinic", data={"name": "Entry Test Clinic", "country": "Uganda"},
                        follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"] == "/request/upload?facility=entry-test-clinic"
    page = client.get(reply.headers["location"])
    assert page.status_code == 200
    assert '<form method="post" action="/request/upload"' in page.text
    assert '<option value="entry-test-clinic" selected>' in page.text


# ---- the removed single-device flow ----------------------------------------

def test_the_old_donor_form_is_gone(client):
    assert client.get("/donor").status_code == 404
    assert client.post("/donor", data={"model_name": "x"}).status_code in (404, 405)


def test_a_stale_clinic_link_with_a_device_id_still_opens_the_application_form(client):
    # /donor used to redirect to /clinic?device_id=N; such a link now just
    # lands on the registration form.
    assert_application_form(client.get("/clinic?device_id=3"))
