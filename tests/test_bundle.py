from app.domain.marketplace import GeoPoint, Offer
from app.domain.requirement import Requirement, RequirementKind
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict
from app.matching.bundle import build_bundle, no_offers
from tests.factories import device


def a_verdict(*requirements: Requirement) -> Verdict:
    results = tuple(
        CheckResult(key=r.from_check or "k", label=r.kind.value,
                    status=CheckStatus.MITIGABLE, detail="", fix=r.detail, requirement=r)
        for r in requirements
    )
    return Verdict(outcome=Outcome.WORKS_IF, results=results)


def an_offer(**kw) -> Offer:
    base = dict(donor_name="Trust", donor_email="t@example.org", device=device(),
                origin=GeoPoint(country="Uganda"), quantity_offered=4)
    return Offer(**{**base, **kw})


def test_a_requirement_with_no_matching_offer_is_reported_as_a_gap():
    verdict = a_verdict(Requirement(RequirementKind.STABILISER, "A stabiliser", "grid"))

    bundle = build_bundle(verdict, no_offers)

    assert len(bundle.gaps) == 1
    assert not bundle.complete
    assert bundle.items[0].status == "nobody has one yet" or not bundle.items[0].available


def test_a_requirement_backed_by_stock_is_covered_with_its_unit_count():
    verdict = a_verdict(Requirement(RequirementKind.STABILISER, "A stabiliser", "grid"))
    finder = lambda kind, terms: [an_offer(support_kind=kind, quantity_offered=4)]

    bundle = build_bundle(verdict, finder)

    assert bundle.complete
    assert bundle.items[0].total_units == 4


def test_training_is_never_searched_for_because_it_cannot_be_shipped():
    verdict = a_verdict(Requirement(RequirementKind.TRAINING, "Training", "training"))
    searched = []

    def finder(kind, terms):
        searched.append(kind)
        return []

    bundle = build_bundle(verdict, finder)

    assert searched == []
    assert bundle.shippable == ()
    assert "cannot be shipped" in bundle.summary


def test_a_verdict_with_nothing_to_send_produces_an_empty_bundle():
    bundle = build_bundle(Verdict(outcome=Outcome.WORKS, results=()), no_offers)

    assert bundle.items == ()
    assert "Nothing needs to ship" in bundle.summary
