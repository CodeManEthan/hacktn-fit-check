"""Storage tests for the marketplace repository.

Each test gets its own SQLite file in a temporary directory. app.data.db caches
one engine per process in a module global, so the fixture clears that cache as
well as pointing SETTINGS at the temporary path.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.domain.marketplace import (
    GeoPoint,
    MatchRecord,
    MatchState,
    Offer,
    OfferStatus,
    Request,
    RequestStatus,
)
from app.domain.provenance import Provenance, fact
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict
from tests.factories import device, facility, intel

CONFIRM_WINDOW_HOURS = 72
HOURS_BEYOND_THE_WINDOW = 80
DAYS_UNTIL_CUTOFF = 30
STOCK_ON_HAND = 4


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A fresh database per test. monkeypatch restores the cached engine after."""
    url = f"sqlite:///{tmp_path / 'marketplace.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    db.init_db()
    yield
    if db._engine is not None:
        db._engine.dispose()


def concentrator() -> Offer:
    return Offer(
        donor_name="Mercy Regional",
        donor_email="donations@mercy.example",
        device=device(model=fact("Concentrator 5L", Provenance.NAMEPLATE)),
        device_type="oxygen concentrator",
        origin=GeoPoint(
            country="United States",
            city="Nashville",
            latitude=36.1627,
            longitude=-86.7816,
        ),
        quantity_offered=STOCK_ON_HAND,
        available_until=date.today() + timedelta(days=DAYS_UNTIL_CUTOFF),
    )


def a_full_verdict() -> Verdict:
    return Verdict(
        outcome=Outcome.WORKS_IF,
        results=(
            CheckResult(
                key="voltage",
                label="Mains voltage",
                status=CheckStatus.MITIGABLE,
                detail="The device wants 110 V and the clinic supplies 240 V.",
                fix="Send a step-down transformer.",
                source=Provenance.NAMEPLATE,
            ),
            CheckResult(
                key="frequency",
                label="Mains frequency",
                status=CheckStatus.PASS,
                detail="The device accepts 50 Hz.",
                source=Provenance.SPEC_SHEET,
            ),
        ),
        requirements=("a step-down transformer",),
        questions=("What year was it made?",),
    )


def a_match(**overrides) -> MatchRecord:
    base = MatchRecord(
        request_id=1,
        offer_id=1,
        verdict=a_full_verdict(),
        intel=intel(),
        quantity=2,
        distance_km=1234.5,
    )
    return replace(base, **overrides)


def as_saved(record):
    """Strip the two fields the database fills in, so the rest can be compared."""
    return replace(record, id=None, created_at=None)


def test_an_offer_comes_back_with_its_device_provenance_intact(database):
    # Arrange
    original = concentrator()

    # Act
    offer_id = repo.save_offer(original)
    loaded = repo.load_offer(offer_id)

    # Assert
    assert loaded is not None
    assert as_saved(loaded) == original
    assert loaded.device.voltage_min_v.source is Provenance.NAMEPLATE
    assert loaded.device.temp_max_c.source is Provenance.SPEC_SHEET
    assert loaded.device.frequencies_hz.value == [50, 60]
    assert loaded.origin.located


def test_a_match_comes_back_with_its_whole_verdict(database):
    # Arrange
    original = a_match()

    # Act
    match_id = repo.save_match(original)
    loaded = repo.load_match(match_id)

    # Assert
    assert loaded is not None
    assert as_saved(loaded) == original
    assert loaded.verdict == a_full_verdict()
    assert loaded.verdict.conditional[0].fix == "Send a step-down transformer."
    assert loaded.intel.consumables_available.source is Provenance.MODEL_KNOWLEDGE
    assert loaded.distance_km == pytest.approx(1234.5)


def test_save_match_generates_a_twelve_character_id(database):
    match_id = repo.save_match(a_match())

    assert len(match_id) == 12
    assert repo.load_match(match_id) is not None


def test_open_offers_for_type_excludes_expired_and_exhausted_offers(database):
    # Arrange: one offer that should match, and three that should not.
    repo.save_offer(replace(concentrator(), donor_name="Still open"))
    repo.save_offer(
        replace(
            concentrator(),
            donor_name="Expired",
            available_until=date.today() - timedelta(days=1),
        )
    )
    repo.save_offer(
        replace(
            concentrator(),
            donor_name="Exhausted",
            quantity_reserved=1,
            quantity_shipped=STOCK_ON_HAND - 1,
        )
    )
    repo.save_offer(
        replace(concentrator(), donor_name="Withdrawn", status=OfferStatus.WITHDRAWN)
    )

    # Act
    found = repo.open_offers_for_type("concentrator")

    # Assert
    assert [o.donor_name for o in found] == ["Still open"]
    assert found[0].quantity_available == STOCK_ON_HAND


def test_open_offers_for_type_ignores_offers_of_another_device(database):
    repo.save_offer(concentrator())

    assert repo.open_offers_for_type("incubator") == []


def test_open_offers_for_type_treats_a_blank_type_as_matching_nothing(database):
    repo.save_offer(concentrator())

    assert repo.open_offers_for_type("   ") == []


def test_load_match_by_token_finds_the_right_record(database):
    # Arrange: two matches, each with its own confirm token.
    wanted = a_match(offer_id=7, token="token-for-the-donor-we-emailed")
    repo.save_match(a_match(offer_id=8, token="some-other-token"))
    wanted_id = repo.save_match(wanted)

    # Act
    found = repo.load_match_by_token("token-for-the-donor-we-emailed")

    # Assert
    assert found is not None
    assert found.id == wanted_id
    assert found.offer_id == 7


def test_load_match_by_token_returns_none_for_an_unknown_token(database):
    repo.save_match(a_match())

    assert repo.load_match_by_token("not-a-real-token") is None


def test_a_facility_comes_back_with_its_tuple_fields_as_tuples(database):
    # Arrange
    original = facility(plug_types=("C", "G"), latitude=0.7028, longitude=32.8856)

    # Act
    slug = repo.save_facility(original)
    loaded = repo.load_facility(slug)

    # Assert
    assert loaded == original
    assert loaded.plug_types == ("C", "G")
    assert loaded.staff_languages == ("English",)


def test_saving_a_facility_twice_replaces_it_rather_than_duplicating_it(database):
    original = facility()

    repo.save_facility(original)
    repo.save_facility(replace(original, name="Renamed Clinic"))

    assert [f.name for f in repo.list_facilities()] == ["Renamed Clinic"]


def test_load_offer_returns_none_for_an_id_that_was_never_saved(database):
    assert repo.load_offer(404) is None


def test_update_offer_persists_a_reservation_without_mutating_the_original(database):
    # Arrange
    offer_id = repo.save_offer(concentrator())
    stored = repo.load_offer(offer_id)

    # Act
    repo.update_offer(stored.reserve(2))

    # Assert
    assert repo.load_offer(offer_id).quantity_reserved == 2
    assert stored.quantity_reserved == 0


def test_update_offer_refuses_an_offer_that_was_never_saved(database):
    with pytest.raises(repo.RepositoryError):
        repo.update_offer(concentrator())


def test_a_request_round_trips_and_only_open_ones_are_listed(database):
    # Arrange
    open_request = Request(
        facility_slug="uganda-district",
        device_type="oxygen concentrator",
        quantity=3,
        note="Maternity ward, two wards sharing one unit.",
    )
    request_id = repo.save_request(open_request)
    repo.save_request(
        replace(open_request, note="done", status=RequestStatus.FULFILLED)
    )

    # Act
    loaded = repo.load_request(request_id)
    still_open = repo.list_open_requests()

    # Assert
    assert as_saved(loaded) == open_request
    assert [r.id for r in still_open] == [request_id]


def test_matches_for_request_returns_only_that_requests_matches(database):
    # Arrange
    mine = repo.save_match(a_match(request_id=1))
    repo.save_match(a_match(request_id=2))

    # Act
    found = repo.matches_for_request(1)

    # Assert
    assert [m.id for m in found] == [mine]


def test_open_matches_older_than_finds_only_stale_unanswered_matches(database):
    # Arrange
    stale = datetime.now() - timedelta(hours=HOURS_BEYOND_THE_WINDOW)
    waiting = repo.save_match(a_match(created_at=stale, state=MatchState.AWAITING_DONOR))
    repo.save_match(a_match(created_at=stale, state=MatchState.CONFIRMED))
    repo.save_match(a_match(state=MatchState.PROPOSED))

    # Act
    timed_out = repo.open_matches_older_than(CONFIRM_WINDOW_HOURS)

    # Assert
    assert [m.id for m in timed_out] == [waiting]


def test_update_match_state_records_when_the_donor_answered(database):
    # Arrange
    match_id = repo.save_match(a_match())
    answered_at = datetime(2026, 3, 1, 9, 30)

    # Act
    repo.update_match_state(match_id, MatchState.CONFIRMED, answered_at)

    # Assert
    loaded = repo.load_match(match_id)
    assert loaded.state is MatchState.CONFIRMED
    assert loaded.responded_at == answered_at
    assert not loaded.is_open
