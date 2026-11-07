"""app/data/seeded.py: which donors, clinics and request the demo seed writes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import SETTINGS
from app.data import db, repository, seeded
from app.data import marketplace_repo as repo
from app.data.seed import FACILITIES
from app.domain.device import DeviceFacts
from app.domain.marketplace import GeoPoint, Offer, Request, RequestLine


def _offer(donor_name: str) -> Offer:
    return Offer(donor_name=donor_name, donor_email="a@example.org",
                 device_type="oxygen concentrator", quantity_offered=1,
                 origin=GeoPoint(country="United States", city="Nashville"),
                 device=DeviceFacts(raw_input="concentrator"))


def test_every_rename_ends_on_a_seeded_name_and_starts_off_the_list():
    for old, new in seeded.DONOR_RENAMES.items():
        assert new in seeded.SEEDED_DONORS
        assert old not in seeded.SEEDED_DONORS


@pytest.mark.parametrize("old_name", [
    "Nashville General Hospital biomedical department",
    "Leeds Teaching Hospitals NHS Trust, surplus",
    "Harris County clinic network",
])
def test_a_hero_offer_counts_as_seeded_under_its_old_and_its_new_name(old_name):
    new_name = seeded.DONOR_RENAMES[old_name]
    assert new_name in seeded.HERO_DONORS
    assert seeded.is_seeded_offer(_offer(old_name))
    assert seeded.is_seeded_offer(_offer(new_name))


def test_the_check_ignores_case_and_outer_spaces():
    assert seeded.is_seeded_donor("  midland teaching hospital, SURPLUS ")


def test_a_visitors_donor_is_not_seeded():
    assert not seeded.is_seeded_offer(_offer("A visitor's own hospital"))
    assert not seeded.is_seeded_donor("")


def test_the_seeded_clinics_are_the_seed_files_clinics():
    assert seeded.SEEDED_CLINIC_SLUGS == {profile.slug for profile in FACILITIES}
    assert seeded.is_seeded_clinic("uganda-district")
    assert not seeded.is_seeded_clinic("kayunga-uganda")


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'seeded.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    for profile in FACILITIES:
        repo.save_facility(profile)
    yield
    if db._engine is not None:
        db._engine.dispose()


def _request(slug: str, device_type: str) -> int:
    return repo.save_request(Request(facility_slug=slug,
                                     lines=(RequestLine(device_type=device_type, quantity=1),)))


def test_the_kayunga_request_is_found_by_clinic_and_type(fresh_db):
    assert seeded.kayunga_request_id() is None
    other_type = _request("uganda-district", "ventilator")
    other_clinic = _request("haiti-community", "oxygen concentrator")
    kayunga = _request("uganda-district", "Oxygen Concentrator ")

    assert seeded.kayunga_request_id() == kayunga
    assert seeded.is_seeded_request(kayunga)
    assert not seeded.is_seeded_request(other_type)
    assert not seeded.is_seeded_request(other_clinic)
