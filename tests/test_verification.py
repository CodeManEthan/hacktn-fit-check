"""The verification state machine: only the four legal moves, nothing else."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import product

import pytest

from app.domain.device import SupplyIntel
from app.domain.marketplace import DEFAULT_CONFIRM_WINDOW_HOURS, MatchRecord, MatchState
from app.domain.result import Outcome, Verdict
from app.matching.verification import (
    InvalidTransition,
    confirm,
    has_timed_out,
    mark_shipped,
    request_confirmation,
    sweep_timeouts,
    withdraw,
)

NOW = datetime(2026, 3, 1, 12, 0, 0)
LEGAL_MOVES = {
    (MatchState.PROPOSED, MatchState.AWAITING_DONOR),
    (MatchState.AWAITING_DONOR, MatchState.CONFIRMED),
    (MatchState.AWAITING_DONOR, MatchState.WITHDRAWN),
    (MatchState.CONFIRMED, MatchState.SHIPPED),
}
MOVES = {
    MatchState.AWAITING_DONOR: lambda m: request_confirmation(m, NOW),
    MatchState.CONFIRMED: lambda m: confirm(m, NOW),
    MatchState.WITHDRAWN: lambda m: withdraw(m, NOW),
    MatchState.SHIPPED: lambda m: mark_shipped(m, NOW),
}
ILLEGAL_MOVES = [
    (origin, target)
    for origin, target in product(MatchState, MOVES)
    if (origin, target) not in LEGAL_MOVES
]


def match(**overrides) -> MatchRecord:
    base = MatchRecord(
        request_id=7,
        offer_id=4,
        verdict=Verdict(outcome=Outcome.WORKS, results=()),
        intel=SupplyIntel(country="Testland"),
        created_at=NOW - timedelta(hours=1),
    )
    return replace(base, **overrides)


def test_request_confirmation_moves_a_proposal_to_awaiting_donor():
    # Arrange
    proposed = match(state=MatchState.PROPOSED)

    # Act
    asked = request_confirmation(proposed, NOW)

    # Assert
    assert asked.state is MatchState.AWAITING_DONOR
    assert asked.responded_at == NOW


def test_confirm_moves_an_awaiting_match_to_confirmed():
    asked = match(state=MatchState.AWAITING_DONOR)

    confirmed = confirm(asked, NOW)

    assert confirmed.state is MatchState.CONFIRMED
    assert confirmed.responded_at == NOW


def test_withdraw_moves_an_awaiting_match_to_withdrawn():
    asked = match(state=MatchState.AWAITING_DONOR)

    gone = withdraw(asked, NOW)

    assert gone.state is MatchState.WITHDRAWN


def test_mark_shipped_moves_a_confirmed_match_to_shipped():
    confirmed = match(state=MatchState.CONFIRMED)

    shipped = mark_shipped(confirmed, NOW)

    assert shipped.state is MatchState.SHIPPED


def test_a_transition_returns_a_new_record_and_leaves_the_original_alone():
    proposed = match(state=MatchState.PROPOSED)

    asked = request_confirmation(proposed, NOW)

    assert proposed.state is MatchState.PROPOSED
    assert asked is not proposed
    assert asked.token == proposed.token


@pytest.mark.parametrize("origin,target", ILLEGAL_MOVES)
def test_every_illegal_transition_raises_and_names_both_states(origin, target):
    stuck = match(state=origin)

    with pytest.raises(InvalidTransition) as raised:
        MOVES[target](stuck)

    assert origin.value in str(raised.value)
    assert target.value in str(raised.value)
    assert raised.value.current is origin
    assert raised.value.target is target


def test_a_withdrawn_match_cannot_be_revived():
    gone = match(state=MatchState.WITHDRAWN)

    with pytest.raises(InvalidTransition):
        confirm(gone, NOW)


def test_a_shipped_match_is_the_end_of_the_line():
    shipped = match(state=MatchState.SHIPPED)

    for move in MOVES.values():
        with pytest.raises(InvalidTransition):
            move(shipped)


def test_a_match_times_out_once_it_is_past_the_window():
    stale = match(
        state=MatchState.AWAITING_DONOR,
        created_at=NOW - timedelta(hours=DEFAULT_CONFIRM_WINDOW_HOURS + 1),
    )

    assert has_timed_out(stale, NOW) is True


def test_a_match_inside_the_window_has_not_timed_out():
    fresh = match(
        state=MatchState.AWAITING_DONOR,
        created_at=NOW - timedelta(hours=DEFAULT_CONFIRM_WINDOW_HOURS - 1),
    )

    assert has_timed_out(fresh, NOW) is False


def test_a_match_exactly_on_the_window_has_not_timed_out_yet():
    on_the_line = match(
        state=MatchState.AWAITING_DONOR,
        created_at=NOW - timedelta(hours=DEFAULT_CONFIRM_WINDOW_HOURS),
    )

    assert has_timed_out(on_the_line, NOW) is False


@pytest.mark.parametrize(
    "state",
    [MatchState.PROPOSED, MatchState.CONFIRMED, MatchState.WITHDRAWN, MatchState.SHIPPED],
)
def test_only_an_awaiting_donor_match_can_time_out(state):
    old = match(state=state, created_at=NOW - timedelta(days=365))

    assert has_timed_out(old, NOW) is False


def test_a_match_with_no_creation_time_cannot_be_aged():
    undated = match(state=MatchState.AWAITING_DONOR, created_at=None)

    assert has_timed_out(undated, NOW) is False


def test_a_shorter_window_can_be_passed_in():
    asked = match(state=MatchState.AWAITING_DONOR, created_at=NOW - timedelta(hours=2))

    assert has_timed_out(asked, NOW, window_hours=1) is True
    assert has_timed_out(asked, NOW, window_hours=24) is False


def test_a_negative_window_is_rejected():
    asked = match(state=MatchState.AWAITING_DONOR)

    with pytest.raises(ValueError, match="window"):
        has_timed_out(asked, NOW, window_hours=-1)


def test_mixing_a_naive_and_an_aware_timestamp_is_rejected():
    aware = match(
        state=MatchState.AWAITING_DONOR,
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="time zone"):
        has_timed_out(aware, NOW)


def test_the_sweep_withdraws_only_the_matches_past_the_window():
    stale = match(
        id="stale",
        state=MatchState.AWAITING_DONOR,
        created_at=NOW - timedelta(hours=DEFAULT_CONFIRM_WINDOW_HOURS + 1),
    )
    fresh = match(id="fresh", state=MatchState.AWAITING_DONOR, created_at=NOW)
    confirmed = match(
        id="confirmed",
        state=MatchState.CONFIRMED,
        created_at=NOW - timedelta(days=30),
    )

    swept = sweep_timeouts([stale, fresh, confirmed], NOW)

    assert [m.id for m in swept] == ["stale"]
    assert swept[0].state is MatchState.WITHDRAWN
    assert swept[0].responded_at == NOW
    assert stale.state is MatchState.AWAITING_DONOR


def test_the_sweep_returns_nothing_when_every_match_is_fresh():
    assert sweep_timeouts([match(state=MatchState.AWAITING_DONOR)], NOW) == []
