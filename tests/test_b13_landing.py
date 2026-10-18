"""[B13] design §13 landing: upstream's layout with our copy, vendored images,
the operator strip under the cards, no seeded-clinic strip, no dead links."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

STATIC = Path(__file__).resolve().parents[1] / "app" / "web" / "static"


def landing() -> str:
    response = TestClient(app).get("/")
    assert response.status_code == 200
    return response.text


def test_the_landing_says_what_fit_check_does_and_asks_what_brings_you():
    body = landing()
    assert "power, climate, utilities, staff and import rules" in body
    assert "What brings you here?" in body
    assert 'href="/static/landing.css"' in body


def test_two_cards_then_the_operator_strip_with_the_middlemen():
    body = landing()
    donor, clinic, operator = (body.index(s) for s in
                               ("Intake form 1", "Intake form 2", 'class="lander-operator"'))
    assert donor < clinic < operator
    assert "The nonprofit" in body and "We scale the site visit, not replace it." in body
    assert "freight forwarder" in body
    assert "library" in body and "maker's document" in body


def test_the_clinic_card_opens_registration_with_a_quiet_request_link():
    body = landing()
    assert 'class="lander-cta" href="/clinic"' in body
    assert 'class="lander-alt" href="/request"' in body
    assert 'class="lander-cta" href="/offer"' in body


def test_images_are_vendored_and_served_not_hotlinked():
    body = landing()
    assert "unsplash.com" not in body
    sources = re.findall(r'<img src="([^"]+)"', body)
    assert sources == ["/static/img/landing-give.jpg", "/static/img/landing-need.jpg"]
    client = TestClient(app)
    for src in sources:
        assert (STATIC / src.removeprefix("/static/")).stat().st_size < 300_000
        reply = client.get(src)
        assert reply.status_code == 200 and reply.headers["content-type"] == "image/jpeg"
    assert client.get("/static/landing.css").status_code == 200


def test_no_seeded_strip_no_fake_network_no_dead_footer_links():
    body = landing()
    assert "/request?facility=" not in body and "Try a seeded clinic" not in body
    assert "Healthcare Network" not in body
    for dead in ("/privacy", "/terms", "/accessibility"):
        assert f'href="{dead}"' not in body
