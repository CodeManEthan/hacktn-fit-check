"""The output side: one result per check, rolled up into one verdict."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.provenance import Provenance


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    MITIGABLE = "mitigable"
    NEEDS_INFO = "needs info"


class Outcome(str, Enum):
    WORKS = "works"
    WORKS_IF = "works if you also send"
    WONT_WORK = "won't work"


@dataclass(frozen=True)
class CheckResult:
    key: str
    label: str
    status: CheckStatus
    detail: str
    fix: str = ""
    source: Provenance = Provenance.UNKNOWN
    question: str = ""

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
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.FAIL)

    @property
    def passes(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.PASS)

    @property
    def unknowns(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.NEEDS_INFO)

    @property
    def headline(self) -> str:
        if self.outcome is Outcome.WORKS:
            return f"All {len(self.results)} checks pass"
        if self.outcome is Outcome.WONT_WORK:
            return f"{len(self.failures)} of {len(self.results)} checks failed"
        return f"{len(self.requirements)} things must ship with it"
