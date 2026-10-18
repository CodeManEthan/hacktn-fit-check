"""The round's acceptance test for [F3]: the hero trio screened against Kayunga.

The seed (scripts/seed_demo.py) runs into a fresh SQLite file; the Kayunga
request's line is screened with the supply order. GUDID is stubbed to "no
record" by refusing every HTTP call, so the registry can only stay silent.
The recorded answer reaches the checks through `supply_lookup`; on a tree
where that is still the empty default, the test's lookup fills what is
still unknown from `repo.load_supply_answer`, the source `supply_lookup`
reads second (design §2.6).

Five facts (design §4.3):
1. A (525DS, Nashville) is rejected, with `frequency` its only FAIL;
2. B (525KS, Leeds) is WORKS_IF with requirement kinds exactly
   {STABILISER, CONSUMABLES} and no NEEDS_INFO result;
3. C (no plate, Houston) is NEEDS_INFO;
4. the ranked list reads B then C;
5. B's bundle shows the Sollatek covering the stabiliser and the Houston
   AVR rejected.

Facts that need [B1]'s rules (the rejected list, the dated-items check,
NEEDS_INFO, the add-on self-check) are marked xfail(strict=False) so the
test documents the target; the lead runs it strict on the merged tree.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from app.checks.runner import check_fit
from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.domain.device import SupplyIntel
from app.domain.requirement import RequirementKind
from app.domain.result import CheckStatus, Outcome
from app.matching.bundle import build_bundle
from app.matching.pipeline import screen_offers
from app.matching.supply_order import supply_lookup
from scripts import seed_demo

NEEDS_B1 = pytest.mark.xfail(strict=False, reason="B1 not merged")
SUPPLY_FIELDS = ("consumables_available", "spare_parts_available",
                 "manufacturer_supported", "sold_locally")


@pytest.fixture
def seeded(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'hero.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)

    def no_record(*args, **kwargs):
        raise httpx.ConnectError("GUDID stubbed: no record")

    monkeypatch.setattr(httpx.Client, "send", no_record)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_record)
    seed_demo.main()
    capsys.readouterr()
    yield
    if db._engine is not None:
        db._engine.dispose()


def hero_lookup(device, device_type: str, country: str) -> SupplyIntel:
    """The supply order, with the recorded answer filling whatever it left unknown."""
    intel = supply_lookup(device, device_type, country)
    recorded = seed_demo.recorded_intel(device, device_type, country)
    filled = {name: getattr(recorded, name) for name in SUPPLY_FIELDS
              if not getattr(intel, name).known and getattr(recorded, name).known}
    return replace(intel, **filled) if filled else intel


def _kayunga():
    facility = repo.load_facility(seed_demo.KAYUNGA)
    request = next(r for r in repo.list_open_requests() if r.facility_slug == seed_demo.KAYUNGA)
    offers = repo.open_offers_for_type(request.lines[0].device_type)
    return facility, offers


def _by_donor(offers, prefix: str):
    return next(o for o in offers if o.donor_name.startswith(prefix))


def _screen():
    facility, offers = _kayunga()
    return facility, offers, screen_offers(facility, offers, intel_for=hero_lookup)


def _verdict(prefix: str):
    facility, offers = _kayunga()
    offer = _by_donor(offers, prefix)
    return check_fit(offer.device, facility, hero_lookup(offer.device, offer.device_type,
                                                         facility.country))


def _fails(verdict) -> set[str]:
    return {r.key for r in verdict.results if r.status is CheckStatus.FAIL}


def test_the_seed_puts_the_trio_on_the_kayunga_request(seeded):
    _, offers = _kayunga()
    assert {o.donor_name for o in offers} == {
        "Nashville General Hospital biomedical department",
        "Leeds Teaching Hospitals NHS Trust, surplus",
        "Harris County clinic network",
    }


def test_offer_a_fails_on_frequency_alone(seeded):
    verdict = _verdict("Nashville")
    assert verdict.outcome is Outcome.WONT_WORK
    assert _fails(verdict) == {"frequency"}


@NEEDS_B1
def test_offer_a_is_on_the_rejected_list(seeded):
    _, _, screening = _screen()
    rejected = {c.offer.donor_name: c for c in screening.rejected}
    a = rejected["Nashville General Hospital biomedical department"]
    assert _fails(a.verdict) == {"frequency"}


def test_offer_b_works_if_a_stabiliser_and_filters_go_with_it(seeded):
    verdict = _verdict("Leeds")
    assert verdict.outcome is Outcome.WORKS_IF
    assert {r.kind for r in verdict.needs} == {RequirementKind.STABILISER,
                                               RequirementKind.CONSUMABLES}


@NEEDS_B1
def test_offer_b_has_no_open_question(seeded):
    verdict = _verdict("Leeds")
    assert [r.key for r in verdict.results if r.status is CheckStatus.NEEDS_INFO] == []


@NEEDS_B1
def test_offer_c_needs_info(seeded):
    assert _verdict("Harris").outcome is Outcome.NEEDS_INFO


@NEEDS_B1
def test_the_ranked_list_reads_b_then_c(seeded):
    _, _, screening = _screen()
    assert [c.offer.donor_name for c in screening.ranked] == [
        "Leeds Teaching Hospitals NHS Trust, surplus",
        "Harris County clinic network",
    ]


@NEEDS_B1
def test_b_bundle_takes_the_sollatek_and_rejects_the_houston_avr(seeded):
    facility, _, screening = _screen()
    b = next(c for c in screening.ranked if c.offer.donor_name.startswith("Leeds"))
    bundle = build_bundle(b.verdict, facility, repo.offers_for_support)
    item = next(i for i in bundle.items if i.requirement.kind is RequirementKind.STABILISER)
    assert [o.donor_name for o in item.offers] == ["Kampala Power Trust"]
    assert [o.donor_name for o in item.rejected_offers] == ["Houston surplus AVR"]
