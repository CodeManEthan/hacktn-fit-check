"""§5.3: GET /packet/<offer_id>?facility=<slug>, with a fake model and a fake
translator. Each test gets its own SQLite file and a fixture library holding
the 525KS instruction-guide excerpt.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.device import SupplyIntel
from app.domain.marketplace import GeoPoint, MatchRecord, Offer, Request, RequestLine
from app.domain.provenance import Provenance, fact
from app.domain.requirement import Requirement, RequirementKind
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict
from app.main import app
from app.reference import device_library, import_rules
from app.web import marketplace_routes
from tests.factories import device, facility

FIXTURES = Path(__file__).parent / "fixtures"
DOCS = [
    {"slug": "drive-525ks", "file": "drive-525ks/registry.json", "title": "GUDID record",
     "url": "https://example.org/gudid", "languages": ["English"], "kind": "registry"},
    {"slug": "drive-525ks", "file": "drive-525ks/guide.txt", "title": "525KS instruction guide",
     "url": "https://example.org/525ks.pdf", "languages": ["English", "French"],
     "kind": "manual"},
]
CARD = {
    "quick_start": [{"text": "Allow up to 15 minutes for start up.", "page": 8}],
    "care_schedule": [{"text": "Wipe the outer cabinet with a damp cloth.",
                       "interval": "7 days", "page": 9}],
    "consumables": [{"item": "Intake filter", "part_number": "MC99Z-123",
                     "interval": "", "page": 14}],
    "warnings": [{"text": "Keep it 2 m from flames.", "page": 8}],
}
FRENCH = {
    "Allow up to 15 minutes for start up.": "Prévoyez jusqu'à 15 minutes pour le démarrage.",
    "Wipe the outer cabinet with a damp cloth.": "Essuyez le boîtier avec un chiffon humide.",
    "7 days": "7 jours",
    "Keep it 2 m from flames.": "Gardez-le à 2 m des flammes.",
}


class FakeChat:
    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt, **kwargs):
        self.calls += 1
        return CARD


class FakeLanguage:
    def __init__(self):
        self.calls = 0

    def batch_language_translation(self, details):
        self.calls += 1
        docs = [SimpleNamespace(key=d.key, translated_text=FRENCH[d.text])
                for d in details.documents]
        return SimpleNamespace(data=SimpleNamespace(documents=docs, errors=[]))


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'packet.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    library = tmp_path / "library" / "drive-525ks"
    library.mkdir(parents=True)
    shutil.copy(FIXTURES / "525ks_excerpt.txt", library / "guide.txt")
    monkeypatch.setattr(device_library, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(import_rules, "RULES_PATH", FIXTURES / "import_rules.json")
    chat, language = FakeChat(), FakeLanguage()
    monkeypatch.setattr(marketplace_routes, "_packet_chat", lambda: chat)
    monkeypatch.setattr(marketplace_routes, "_packet_language", lambda: language)
    ns = SimpleNamespace(client=TestClient(app), chat=chat, language=language)
    yield ns
    if db._engine is not None:
        db._engine.dispose()


def an_offer(documents=DOCS, **device_kw) -> int:
    facts = device(documents=fact(documents, Provenance.SPEC_SHEET), **device_kw)
    return repo.save_offer(Offer(
        donor_name="Mercy Regional", donor_email="m@example.org", device=facts,
        device_type="oxygen concentrator",
        origin=GeoPoint(country="Testland", city="Here", latitude=0.1, longitude=0.1),
        quantity_offered=1,
    ))


def a_clinic(slug="test-clinic", **kw) -> str:
    repo.save_facility(facility(slug=slug, latitude=0.0, longitude=0.0, **kw))
    return slug


def test_an_english_clinic_gets_the_gated_english_card_and_the_paperwork(fakes):
    offer_id, slug = an_offer(), a_clinic()

    page = fakes.client.get(f"/packet/{offer_id}?facility={slug}")

    assert page.status_code == 200
    body = page.text
    assert "English, as requested" in body
    assert "Allow up to 15 minutes for start up." in body and "p.&nbsp;8" in body
    assert "MC99Z-123" not in body                      # invented part code, gated out
    assert "1 lines dropped: not found in the manual" in body
    assert "no-objection letter" in body and "packing list with serial numbers" in body
    assert "525KS instruction guide" in body            # an edition in English, linked
    assert "machine-translated" not in body
    assert fakes.language.calls == 0


def test_the_packet_is_cached_and_rebuild_clears_it(fakes):
    offer_id, slug = an_offer(), a_clinic()

    fakes.client.get(f"/packet/{offer_id}?facility={slug}")
    fakes.client.get(f"/packet/{offer_id}?facility={slug}")
    assert fakes.chat.calls == 1
    stored = repo.load_packet(offer_id, slug)
    assert stored.language == "English" and stored.translated is None
    assert stored.card["dropped"] == 1

    fakes.client.get(f"/packet/{offer_id}?facility={slug}&rebuild=1")
    assert fakes.chat.calls == 2


def test_a_french_clinic_gets_the_translation_beside_the_english(fakes):
    offer_id = an_offer()
    slug = a_clinic("haiti-clinic", country="Haiti", staff_languages=("French", "Haitian Creole"))

    body = fakes.client.get(f"/packet/{offer_id}?facility={slug}").text

    assert "French beside English, machine-translated, verify" in body
    assert "Prévoyez jusqu&#39;à 15 minutes pour le démarrage." in body
    assert "Allow up to 15 minutes for start up." in body
    assert "No import rules for Haiti are on file" in body
    stored = repo.load_packet(offer_id, slug)
    assert stored.language == "French"
    assert stored.translated["engine"] == "oci-language"
    assert stored.translated["card"]["care_schedule"][0]["interval"] == "7 jours"

    fakes.client.get(f"/packet/{offer_id}?facility={slug}")
    assert fakes.language.calls == 1 and fakes.chat.calls == 1


def test_a_nepali_clinic_gets_the_english_card_with_a_visible_flag(fakes):
    offer_id = an_offer()
    slug = a_clinic("nepal-clinic", country="Nepal", staff_languages=("Nepali", "English"))

    body = fakes.client.get(f"/packet/{offer_id}?facility={slug}").text

    assert "Nepali is not supported by OCI Language; English card supplied" in body
    assert 'class="flag hold"' in body
    assert "Allow up to 15 minutes for start up." in body
    assert fakes.language.calls == 0


def test_an_offer_with_no_documents_says_so_and_still_lists_the_paperwork(fakes):
    offer_id, slug = an_offer(documents=[]), a_clinic()

    body = fakes.client.get(f"/packet/{offer_id}?facility={slug}").text

    assert "No manual could be found for this device." in body
    assert "no-objection letter" in body
    assert fakes.chat.calls == 0
    assert repo.load_packet(offer_id, slug) is None


def test_a_manual_whose_text_is_missing_is_reported_not_built(fakes):
    missing = [{**DOCS[1], "file": "drive-525ks/absent.txt"}]
    offer_id, slug = an_offer(documents=missing), a_clinic()

    body = fakes.client.get(f"/packet/{offer_id}?facility={slug}").text

    assert "text is not in the library" in body
    assert fakes.chat.calls == 0


def test_send_with_comes_from_a_fresh_check_when_no_match_is_stored(fakes):
    offer_id = an_offer(manual_languages=fact(["English"], Provenance.SPEC_SHEET))
    slug = a_clinic("haiti-clinic", country="Haiti", staff_languages=("French",))

    body = fakes.client.get(f"/packet/{offer_id}?facility={slug}").text

    assert "Manual language." in body
    assert "A handover packet in French, built from the manufacturer" in body


def test_send_with_uses_the_stored_verdict_of_a_match(fakes):
    offer_id, slug = an_offer(), a_clinic()
    request_id = repo.save_request(Request(
        facility_slug=slug, lines=(RequestLine(device_type="oxygen concentrator", quantity=1),)))
    stored = Verdict(Outcome.WORKS_IF, (CheckResult(
        key="power", label="Power", status=CheckStatus.MITIGABLE, detail="d",
        fix="A 1 kVA stabiliser.",
        requirement=Requirement(RequirementKind.STABILISER, "A 1 kVA stabiliser.", "power")),))
    repo.save_match(MatchRecord(request_id=request_id, offer_id=offer_id, verdict=stored,
                                intel=SupplyIntel()))

    body = fakes.client.get(f"/packet/{offer_id}?facility={slug}").text

    assert "A 1 kVA stabiliser." in body


def test_an_unknown_offer_or_a_missing_clinic_is_an_error(fakes):
    assert fakes.client.get("/packet/999?facility=test-clinic").status_code == 400
    offer_id = an_offer()
    assert fakes.client.get(f"/packet/{offer_id}").status_code == 400
    assert fakes.client.get(f"/packet/{offer_id}?facility=nowhere").status_code == 400


def test_a_model_failure_shows_the_problem_and_caches_nothing(fakes, monkeypatch):
    from app.ai.client import GenAiError

    class Down:
        def complete_json(self, *a, **k):
            raise GenAiError("Generative AI call failed: timeout")

    monkeypatch.setattr(marketplace_routes, "_packet_chat", lambda: Down())
    offer_id, slug = an_offer(), a_clinic()

    body = fakes.client.get(f"/packet/{offer_id}?facility={slug}").text

    assert "The card could not be built" in body
    assert repo.load_packet(offer_id, slug) is None
