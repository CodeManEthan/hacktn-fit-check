"""The pipeline names the overnight builds fill in, at their working defaults."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.device import SupplyIntel
from app.domain.marketplace import GeoPoint, Offer, RequestLine
from app.domain.provenance import Provenance, fact
from app.domain.requirement import Requirement, RequirementKind
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict
from app.matching.bundle import BundleItem, build_bundle
from app.matching.coverage import LineCoverage
from app.matching.pipeline import (
    Candidate,
    Screening,
    empty_intel,
    rank_candidates,
    screen_offers,
)
from app.matching.supply_order import supply_lookup
from app.web.forms import FormError, apply_dated_items
from app.web.presenters import Advisory, advisory_for
from tests.factories import device, facility


def an_offer(**kw) -> Offer:
    base = dict(
        donor_name="Mercy", donor_email="m@example.org", device=device(),
        device_type="oxygen concentrator",
        origin=GeoPoint(country="Testland", latitude=0.0, longitude=0.0),
        quantity_offered=2,
    )
    return Offer(**{**base, **kw})


def test_empty_intel_takes_the_device_type_and_states_nothing():
    stated = empty_intel(device(), "oxygen concentrator", "Uganda")

    assert stated == SupplyIntel(country="Uganda")
    assert not stated.sold_locally.known


def test_the_lookup_is_called_with_the_offer_device_type():
    seen = []

    def lookup(device_facts, device_type, country):
        seen.append((device_type, country))
        return SupplyIntel(country=country)

    rank_candidates(facility(country="Uganda"), [an_offer(id=1)], lookup)

    assert seen == [("oxygen concentrator", "Uganda")]


def test_screen_offers_ranks_as_rank_candidates_and_returns_the_rejected():
    offers = [an_offer(id=1), an_offer(id=2, device=device(frequencies_hz=fact([60], Provenance.NAMEPLATE)))]

    screening = screen_offers(facility(), offers)

    assert isinstance(screening, Screening)
    assert [c.offer.id for c in screening.ranked] == [
        c.offer.id for c in rank_candidates(facility(), offers)
    ]
    assert [c.offer.id for c in screening.rejected] == [2]


def test_screen_offers_passes_today_and_the_lookup_through():
    expired = an_offer(id=1, available_until=date(2026, 1, 1))
    calls = []

    def lookup(d, t, c):
        calls.append(t)
        return SupplyIntel(country=c)

    screening = screen_offers(facility(), [expired, an_offer(id=2)], lookup, today=date(2026, 6, 1))

    assert [c.offer.id for c in screening.ranked] == [2]
    assert calls == ["oxygen concentrator"]


def test_supply_lookup_states_nothing_until_its_sources_are_wired():
    assert supply_lookup(device(), "oxygen concentrator", "Uganda") == SupplyIntel(country="Uganda")


def test_line_coverage_has_an_empty_rejected_list_by_default():
    cover = LineCoverage(line=RequestLine(device_type="oxygen concentrator", quantity=1))

    assert cover.rejected == ()


def test_bundle_items_carry_empty_rejected_and_unverified_offers():
    requirement = Requirement(RequirementKind.STABILISER, "A stabiliser", "grid")
    verdict = Verdict(outcome=Outcome.WORKS_IF, results=(
        CheckResult(key="grid", label="Grid", status=CheckStatus.MITIGABLE, detail="",
                    requirement=requirement),
    ))
    stock = an_offer(id=9, support_kind="voltage stabiliser")

    bundle = build_bundle(verdict, facility(), lambda kind, terms: [stock])

    item = bundle.items[0]
    assert item.offers == (stock,)
    assert item.rejected_offers == () and item.unverified_offers == ()
    assert item.available
    assert BundleItem(requirement=requirement).rejected_offers == ()


def test_advisory_for_returns_none_until_the_price_table_lands():
    rejected = Candidate(offer=an_offer(id=1), verdict=Verdict(outcome=Outcome.WONT_WORK, results=()))

    assert advisory_for(rejected) is None
    assert Advisory().rows == () and Advisory().supply_url == ""


def test_a_blank_expiry_is_the_answer_nothing_is_dated():
    dated = device(expiry_date=fact(date(2027, 1, 1), Provenance.NAMEPLATE))

    answered = apply_dated_items(dated, "  ")

    assert answered.dated_items == fact(False, Provenance.DONOR_STATED)
    assert not answered.expiry_date.known


def test_a_filled_expiry_is_dated_with_that_date():
    answered = apply_dated_items(device(), "2027-03-31")

    assert answered.dated_items == fact(True, Provenance.DONOR_STATED)
    assert answered.expiry_date == fact(date(2027, 3, 31), Provenance.DONOR_STATED)


def test_a_malformed_expiry_is_a_form_error():
    with pytest.raises(FormError):
        apply_dated_items(device(), "next spring")
