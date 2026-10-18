"""How much of one request line the compatible offers can actually cover.

Coverage is counted only across offers that already passed the screening and
whose facts are known. An incompatible donor with a full warehouse
contributes nothing here, nor does one whose offer still has open questions:
this is the stage that decides *how much*, after the checks have decided
*whether*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.domain.marketplace import RequestLine
from app.domain.result import Outcome

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from app.matching.pipeline import Candidate

NOTHING = 0


@dataclass(frozen=True)
class LineCoverage:
    """One request line, and what the compatible inventory can do about it."""

    line: RequestLine
    candidates: list["Candidate"] = field(default_factory=list)
    screened: int = NOTHING
    # Offers of this kind the screening rejected, each with its verdict.
    rejected: tuple["Candidate", ...] = ()

    @property
    def proposable(self) -> list["Candidate"]:
        """The survivors that can be proposed to a donor today.

        A NEEDS_INFO survivor is listed on the page but is not stock anyone
        can ask for until its questions are answered (design §2.3).
        """
        return [c for c in self.candidates if c.verdict.outcome is not Outcome.NEEDS_INFO]

    @property
    def asking(self) -> list["Candidate"]:
        """The survivors that have questions to answer first."""
        return [c for c in self.candidates if c.verdict.outcome is Outcome.NEEDS_INFO]

    @property
    def units_available(self) -> int:
        """Units this line could actually draw, never more than it asked for."""
        pooled = sum(c.offer.quantity_available for c in self.proposable)
        return min(pooled, self.line.quantity)

    @property
    def shortfall(self) -> int:
        """Units nobody compatible can supply."""
        return self.line.quantity - self.units_available

    @property
    def complete(self) -> bool:
        return self.shortfall == NOTHING

    @property
    def donors_needed(self) -> int:
        """How many donors it takes, largest first, to reach what was asked."""
        remaining = self.line.quantity
        used = NOTHING
        for candidate in sorted(
            self.proposable, key=lambda c: c.offer.quantity_available, reverse=True
        ):
            if remaining <= NOTHING:
                break
            remaining -= candidate.offer.quantity_available
            used += 1
        return used

    @property
    def needs_split(self) -> bool:
        """Covered, but only by combining more than one donor's stock."""
        return self.complete and self.donors_needed > 1

    @property
    def headline(self) -> str:
        """The line's coverage as a person would read it."""
        return f"{self.units_available} of {self.line.quantity} available"
