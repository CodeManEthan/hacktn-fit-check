"""A public registration never changes another clinic's row.

POST /clinic slugifies the name. When that slug already belongs to a clinic,
the newcomer gets a free slug (`-2`, `-3`, ...) and lands on its own upload
page; the existing clinic keeps its profile and its reviewer verification.
The seed's path (save_facility without `new`) still replaces its own rows.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.facility_trust import Verification
from app.main import app
from app.web import marketplace_routes
from tests.factories import facility


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'register.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    monkeypatch.setattr(marketplace_routes, "categorise_support", lambda *a: None)
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def register(client: TestClient, name: str, country: str = "Kenya"):
    return client.post(
        "/clinic", data={"name": name, "country": country}, follow_redirects=False
    )


def test_a_taken_slug_gets_a_new_one_and_the_existing_clinic_is_untouched(client):
    repo.save_facility(facility(slug="uganda-district", name="Kayunga District Hospital",
                                country="Uganda"))
    repo.set_verification("uganda-district", Verification.REVIEWER_APPROVED, "checked")

    response = register(client, "UGANDA  district!!")

    assert response.status_code == 303
    assert response.headers["location"] == "/request/upload?facility=uganda-district-2"
    kept = repo.load_facility("uganda-district")
    assert (kept.name, kept.country) == ("Kayunga District Hospital", "Uganda")
    assert repo.load_verification("uganda-district").tier is Verification.REVIEWER_APPROVED
    newcomer = repo.load_facility("uganda-district-2")
    assert (newcomer.slug, newcomer.name, newcomer.country) == (
        "uganda-district-2", "UGANDA  district!!", "Kenya")
    assert repo.load_verification("uganda-district-2").tier is Verification.SELF_DECLARED


def test_each_further_registration_takes_the_next_free_slug(client):
    for expected in ("same-name", "same-name-2", "same-name-3"):
        response = register(client, "Same Name")
        assert response.headers["location"] == f"/request/upload?facility={expected}"
    assert {f.slug for f in repo.list_facilities()} == {"same-name", "same-name-2", "same-name-3"}


def test_long_names_that_share_the_first_sixty_slug_characters_stay_apart(client):
    stem = "a" * 60
    first = register(client, stem + " North")
    second = register(client, stem + " South")

    assert first.headers["location"] == f"/request/upload?facility={stem}"
    second_slug = second.headers["location"].split("facility=")[1]
    assert second_slug != stem and len(second_slug) <= 64
    assert repo.load_facility(stem).name == stem + " North"
    assert repo.load_facility(second_slug).name == stem + " South"


def test_the_seed_path_still_replaces_its_own_row(client):
    repo.save_facility(facility(slug="nepal-phc", name="Old name"))
    assert repo.save_facility(facility(slug="nepal-phc", name="New name")) == "nepal-phc"
    assert repo.load_facility("nepal-phc").name == "New name"
    assert [f.slug for f in repo.list_facilities()] == ["nepal-phc"]
