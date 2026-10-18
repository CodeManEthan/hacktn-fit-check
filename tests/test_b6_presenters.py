"""[B6] The candidate page's presenters: citation links, questions, names."""

from __future__ import annotations

from types import SimpleNamespace

from app.checks import check_fit
from app.domain.device import SupplyIntel
from app.domain.provenance import UNKNOWN, Provenance, fact
from app.reference import device_library
from app.web import presenters
from tests.factories import device, facility


def test_a_url_note_links_to_itself():
    held = fact(True, Provenance.REGISTRY, "https://accessgudid.nlm.nih.gov/devices/1")
    link = presenters.source_link(device(), "manufacturer_supported", held)
    assert link.url == link.text == "https://accessgudid.nlm.nih.gov/devices/1"


def test_a_library_note_links_to_its_page_and_keeps_the_aside():
    facts = device_library.to_device_facts(device_library.entry("drive-devilbiss-525ks"))
    link = presenters.source_link(facts, "frequencies_hz", facts.frequencies_hz)
    assert link.text.endswith("p.11") and link.url.endswith("#page=11")
    assert "rating label" in link.aside


def test_a_plain_note_is_text_and_no_note_is_nothing():
    held = fact(False, Provenance.RECORDED, "recorded by the field team, 2026-10-18")
    assert presenters.source_link(device(), "x", held).url == ""
    assert presenters.source_link(device(), "x", fact(1, Provenance.DONOR_STATED)) is None
    assert presenters.source_link(device(), "x", UNKNOWN) is None


def test_only_the_country_questions_link_to_supply():
    unknown_consumables = device(consumables=UNKNOWN)
    clinic = facility()
    verdict = check_fit(unknown_consumables, clinic, SupplyIntel(country=clinic.country))
    asked = presenters.open_questions(verdict, unknown_consumables,
                                      SupplyIntel(country=clinic.country),
                                      "oxygen concentrator", clinic.country, back="/x")
    by_text = {q["text"]: q for q in asked}
    consume = next(q for t, q in by_text.items() if t.startswith("What does the device consume"))
    parts = next(q for t, q in by_text.items() if t.startswith("Can spare parts"))
    assert consume["url"] == "" and consume["who"] == "the donor"
    assert parts["url"].startswith(f"/supply/{clinic.country}?device_type=oxygen+concentrator")
    assert parts["url"].endswith("back=%2Fx")


def test_an_offer_with_no_maker_or_model_is_named_by_its_type():
    offer = SimpleNamespace(device=device(manufacturer=UNKNOWN, model=UNKNOWN),
                            device_type="oxygen concentrator")
    assert presenters.offer_name(offer) == "Unnamed oxygen concentrator"
