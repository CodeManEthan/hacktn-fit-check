"""The verification state machine.

Four legal moves and no others:

    PROPOSED -> AWAITING_DONOR -> CONFIRMED | WITHDRAWN
    CONFIRMED -> SHIPPED

Every other move raises, including a move onto the state a match is already
in. WITHDRAWN and SHIPPED are terminal. Nothing here mutates a record: each
transition returns a new one. The one exception is run_timeout_sweep at the
bottom, which applies sweep_timeouts to the database.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Iterable, Mapping

from app.data import marketplace_repo as repo
from app.domain.marketplace import DEFAULT_CONFIRM_WINDOW_HOURS, MatchRecord, MatchState

LEGAL_TRANSITIONS: Mapping[MatchState, frozenset[MatchState]] = MappingProxyType(
    {
        MatchState.PROPOSED: frozenset({MatchState.AWAITING_DONOR}),
        MatchState.AWAITING_DONOR: frozenset(
            {MatchState.CONFIRMED, MatchState.WITHDRAWN}
        ),
        MatchState.CONFIRMED: frozenset({MatchState.SHIPPED}),
        MatchState.WITHDRAWN: frozenset(),
        MatchState.SHIPPED: frozenset(),
    }
)


class InvalidTransition(ValueError):
    """Raised when a match is asked to make a move the spec does not allow."""

    def __init__(self, current: MatchState, target: MatchState) -> None:
        super().__init__(
            f"A match cannot move from {current.value} to {target.value}."
        )
        self.current = current
        self.target = target


def transition(
    match: MatchRecord, target: MatchState, now: datetime | None = None
) -> MatchRecord:
    """The only door into the state machine. Returns a new record."""
    allowed = LEGAL_TRANSITIONS.get(match.state, frozenset())
    if target not in allowed:
        raise InvalidTransition(match.state, target)
    return match.to_state(target, now)


def request_confirmation(
    match: MatchRecord, now: datetime | None = None
) -> MatchRecord:
    """Send the proposal to the donor and start the confirmation window."""
    return transition(match, MatchState.AWAITING_DONOR, now)


def confirm(match: MatchRecord, now: datetime | None = None) -> MatchRecord:
    """The donor said yes."""
    return transition(match, MatchState.CONFIRMED, now)


def withdraw(match: MatchRecord, now: datetime | None = None) -> MatchRecord:
    """The donor said no, or the window closed on silence."""
    return transition(match, MatchState.WITHDRAWN, now)


def mark_shipped(match: MatchRecord, now: datetime | None = None) -> MatchRecord:
    """The device is on its way."""
    return transition(match, MatchState.SHIPPED, now)


def has_timed_out(
    match: MatchRecord,
    now: datetime,
    window_hours: int = DEFAULT_CONFIRM_WINDOW_HOURS,
) -> bool:
    """True when a match is still awaiting a donor and the window has passed.

    A match with no creation time cannot be aged, so it is never swept: the
    honest answer to "how old is this" is unknown, not "old enough".
    """
    if window_hours < 0:
        raise ValueError(f"The confirmation window cannot be negative: {window_hours}.")
    if match.state is not MatchState.AWAITING_DONOR:
        return False
    if match.created_at is None:
        return False

    created_at = match.created_at
    if (created_at.tzinfo is None) != (now.tzinfo is None):
        raise ValueError(
            "Cannot age a match against a time zone it does not share: "
            f"created_at={created_at!r}, now={now!r}."
        )
    return created_at + timedelta(hours=window_hours) < now


def sweep_timeouts(
    matches: Iterable[MatchRecord],
    now: datetime,
    window_hours: int = DEFAULT_CONFIRM_WINDOW_HOURS,
) -> list[MatchRecord]:
    """Withdraw every match the donor let expire, and return the new records."""
    return [
        withdraw(match, now)
        for match in matches
        if has_timed_out(match, now, window_hours)
    ]


# How often a page view may trigger the sweep. A match is at most this much
# late being withdrawn, against a window of days.
SWEEP_INTERVAL = timedelta(minutes=5)

_LOG = logging.getLogger(__name__)
_sweep_lock = threading.Lock()
_last_sweep: datetime | None = None


def run_timeout_sweep(
    now: datetime | None = None,
    *,
    force: bool = False,
    window_hours: int = DEFAULT_CONFIRM_WINDOW_HOURS,
) -> int:
    """Withdraw the matches whose donor let the window pass, and free their stock.

    Called from the clinic's pages, at most once per SWEEP_INTERVAL unless
    forced. Each match is withdrawn by a conditional update, so its stock is
    released once even if the donor answers at the same moment or two sweeps
    overlap. Returns how many matches this call withdrew. A storage failure is
    logged and leaves the page to render; the next sweep tries again.
    """
    global _last_sweep
    now = now or datetime.now()
    if not _sweep_lock.acquire(blocking=False):
        return 0
    try:
        if not force and _last_sweep is not None and now - _last_sweep < SWEEP_INTERVAL:
            return 0
        _last_sweep = now
        withdrawn = 0
        try:
            # The move and the give-back are one transaction in the repo, and
            # the give-back is a decrement in SQL, so a hold placed on the same
            # offer meanwhile is never overwritten.
            for match in repo.open_matches_older_than(window_hours, now):
                if not has_timed_out(match, now, window_hours):
                    continue
                if repo.respond_to_match(match, MatchState.WITHDRAWN, now, release=True):
                    withdrawn += 1
        except repo.RepositoryError:
            _LOG.exception("The timeout sweep stopped early")
        return withdrawn
    finally:
        _sweep_lock.release()
