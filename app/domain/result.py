"""The output side: one result per check, rolled up into one verdict."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.provenance import Provenance
from app.domain.requirement import Requirement
from app.domain.text import plural


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    MITIGABLE = "mitigable"
    NEEDS_INFO = "needs info"


class Outcome(str, Enum):
    WORKS = "works"
    WORKS_IF = "works if you also send"
    WONT_WORK = "won't work"
    NEEDS_INFO = "needs info"


@dataclass(frozen=True)
class CheckResult:
    key: str
    label: str
    status: CheckStatus
    detail: str
    fix: str = ""
    source: Provenance = Provenance.UNKNOWN
    question: str = ""
    requirement: Requirement | None = None

    @property
    def blocking(self) -> bool:
        return self.status is CheckStatus.FAIL


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    results: tuple[CheckResult, ...]
    requirements: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    @property
    def needs(self) -> tuple[Requirement, ...]:
        """Every structured requirement this verdict raised, in result order."""
        return tuple(r.requirement for r in self.results if r.requirement is not None)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.FAIL)

    @property
    def passes(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.PASS)

    @property
    def unknowns(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.NEEDS_INFO)

    @property
    def conditional(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.MITIGABLE)

    @property
    def stamp(self) -> str:
        """Two words for the inspection stamp."""
        if self.outcome is Outcome.WORKS:
            return "Clear to ship"
        if self.outcome is Outcome.WONT_WORK:
            return "Do not ship"
        if self.outcome is Outcome.NEEDS_INFO:
            return "Needs info"
        return "Ship with"

    @property
    def headline(self) -> str:
        if self.outcome is Outcome.WORKS:
            checks = len(self.results)
            return "1 check passes" if checks == 1 else f"All {checks} checks pass"
        if self.outcome is Outcome.WONT_WORK:
            blockers = plural(len(self.failures), "blocker")
            return f"{blockers}, {plural(len(self.conditional), 'condition')}"
        if self.outcome is Outcome.NEEDS_INFO:
            asked = len(self.questions) or len(self.unknowns)
            return f"{plural(asked, 'question')} before a verdict"
        return f"{plural(len(self.requirements), 'thing')} it cannot arrive without"
