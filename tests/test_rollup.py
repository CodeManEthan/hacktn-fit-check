"""The rollup order of design §2.1, and the rule that a FAIL carries no Requirement."""

from __future__ import annotations

from app.checks import build_verdict
from app.domain.requirement import Requirement, RequirementKind
from app.domain.result import CheckResult, CheckStatus, Outcome


def result(status: CheckStatus, key: str = "k", requirement: Requirement | None = None) -> CheckResult:
    return CheckResult(
        key=key, label=key, status=status, detail="d",
        fix="send a stabiliser" if status is CheckStatus.MITIGABLE else "",
        question="What is it?" if status is CheckStatus.NEEDS_INFO else "",
        requirement=requirement,
    )


STABILISER = Requirement(RequirementKind.STABILISER, "A voltage stabiliser.", "grid")


def test_a_fail_outranks_an_unknown():
    verdict = build_verdict((result(CheckStatus.NEEDS_INFO), result(CheckStatus.FAIL)))
    assert verdict.outcome is Outcome.WONT_WORK


def test_an_unknown_outranks_an_add_on_and_still_lists_it():
    verdict = build_verdict((
        result(CheckStatus.MITIGABLE, "grid", STABILISER),
        result(CheckStatus.NEEDS_INFO, "spare_parts"),
    ))

    assert verdict.outcome is Outcome.NEEDS_INFO
    assert verdict.requirements == ("send a stabiliser",)
    assert verdict.needs == (STABILISER,)
    assert verdict.questions == ("What is it?",)
    assert verdict.stamp == "Needs info"
    assert verdict.headline == "1 question before a verdict"


def test_add_ons_alone_are_works_if():
    verdict = build_verdict((result(CheckStatus.PASS), result(CheckStatus.MITIGABLE, "grid", STABILISER)))
    assert verdict.outcome is Outcome.WORKS_IF


def test_all_pass_is_works():
    verdict = build_verdict((result(CheckStatus.PASS), result(CheckStatus.PASS)))
    assert verdict.outcome is Outcome.WORKS
