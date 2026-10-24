"""The 72-hour window on a donor's answer is enforced, and frees the stock once.

The match page promises that a donor who stays silent past the window has the
match withdrawn and the stock released. A clinic page view triggers the sweep.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.data import marketplace_repo as repo
from app.domain.marketplace import MatchState
from app.main import app
from app.matching import verification
from tests.test_marketplace_repo import a_match, concentrator, database  # noqa: F401

STALE_HOURS = 80
FRESH_HOURS = 1
STALE_QUANTITY = 2
FRESH_QUANTITY = 1


@pytest.fixture
def stock(database, monkeypatch):  # noqa: F811
    """One offer with two matches reserved against it: one stale, one fresh."""
    monkeypatch.setattr(verification, "_last_sweep", None)
    now = datetime.now()
    offer_id = repo.save_offer(concentrator())
    offer = repo.load_offer(offer_id)
    repo.update_offer(offer.reserve(STALE_QUANTITY + FRESH_QUANTITY))
    stale = repo.save_match(
        a_match(
            offer_id=offer_id,
            quantity=STALE_QUANTITY,
            state=MatchState.AWAITING_DONOR,
            token="stale-token",
            created_at=now - timedelta(hours=STALE_HOURS),
        )
    )
    fresh = repo.save_match(
        a_match(
            offer_id=offer_id,
            quantity=FRESH_QUANTITY,
            state=MatchState.AWAITING_DONOR,
            token="fresh-token",
            created_at=now - timedelta(hours=FRESH_HOURS),
        )
    )
    return {"offer": offer_id, "stale": stale, "fresh": fresh}


def test_opening_the_match_page_withdraws_a_stale_match_and_frees_its_stock(stock):
    response = TestClient(app).get(f"/match/{stock['fresh']}")

    assert response.status_code == 200
    assert repo.load_match(stock["stale"]).state is MatchState.WITHDRAWN
    assert repo.load_match(stock["fresh"]).state is MatchState.AWAITING_DONOR
    assert repo.load_offer(stock["offer"]).quantity_reserved == FRESH_QUANTITY


def test_a_second_sweep_does_not_release_the_same_stock_again(stock):
    assert verification.run_timeout_sweep(force=True) == 1
    assert verification.run_timeout_sweep(force=True) == 0

    assert repo.load_offer(stock["offer"]).quantity_reserved == FRESH_QUANTITY


def test_a_match_the_donor_declines_mid_sweep_is_not_released_twice(stock, monkeypatch):
    # The sweep read the stale match while it was still waiting on the donor...
    snapshot = repo.load_match(stock["stale"])
    monkeypatch.setattr(repo, "open_matches_older_than", lambda *_: [snapshot])
    # ...and the donor's "no" landed before the sweep wrote, releasing the stock.
    assert repo.withdraw_if_awaiting(snapshot.id)
    offer = repo.load_offer(stock["offer"])
    repo.update_offer(offer.release(snapshot.quantity))

    assert verification.run_timeout_sweep(force=True) == 0
    assert repo.load_offer(stock["offer"]).quantity_reserved == FRESH_QUANTITY


def test_the_sweep_runs_at_most_once_per_interval(stock):
    now = datetime.now()
    verification.run_timeout_sweep(now - timedelta(minutes=1))
    # That run withdrew the stale match. Put it back to waiting, so a run the
    # throttle let through would have something to withdraw.
    repo.update_match_state(stock["stale"], MatchState.AWAITING_DONOR)

    assert verification.run_timeout_sweep(now) == 0
    assert verification.run_timeout_sweep(now + verification.SWEEP_INTERVAL) == 1
