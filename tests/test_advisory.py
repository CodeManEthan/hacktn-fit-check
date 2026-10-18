"""The buy-locally advisory: prices under a frequency rejection, never a verdict.

The last tests seed a 60 Hz concentrator and Kayunga (50 Hz) in SQLite. Until
[B1]'s screen_offers lists rejected offers, the page test feeds the real
verdict through a stubbed screening, and the end-to-end test skips.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.checks.runner import check_fit
from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.data.seed import FACILITIES
from app.domain.device import SupplyIntel
from app.domain.marketplace import GeoPoint, Offer, Request, RequestLine
from app.domain.provenance import Provenance, fact
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict
from app.main import app
from app.matching.pipeline import Candidate, Screening
from app.web import marketplace_routes
from app.web.marketplace_routes import TEMPLATES
from app.web.presenters import (
    LOCAL_PRICES_PATH, Advisory, advisory_for, load_local_prices, price_text,
)
from tests.factories import device

KAYUNGA = next(f for f in FACILITIES if f.slug == "uganda-district")
SIXTY_HZ = device(frequencies_hz=fact([60], Provenance.NAMEPLATE),
                  voltage_min_v=fact(230, Provenance.NAMEPLATE),
                  voltage_max_v=fact(230, Provenance.NAMEPLATE))
VERDICT_WORDS = ("won't work", "works", "do not ship", "clear to ship", "ship with",
                 "needs info", "reject")


def an_offer(**kw) -> Offer:
    base = dict(donor_name="Mercy Regional", donor_email="m@example.org", device=SIXTY_HZ,
                device_type="oxygen concentrator",
                origin=GeoPoint(country="Uganda", city="Jinja", latitude=0.44, longitude=33.2),
                quantity_offered=1)
    return Offer(**{**base, **kw})


def result(key: str, status: CheckStatus) -> CheckResult:
    return CheckResult(key=key, label=key, status=status, detail="")


def candidate(outcome=Outcome.WONT_WORK, keys=("frequency",), status=CheckStatus.FAIL,
              sold=None, country="Uganda", device_type="oxygen concentrator") -> Candidate:
    verdict = Verdict(outcome=outcome, results=tuple(result(k, status) for k in keys))
    intel = SupplyIntel(country=country, sold_locally=sold or fact(None, Provenance.RECORDED))
    return Candidate(offer=an_offer(id=7, device_type=device_type), verdict=verdict, intel=intel)


# --- the price table ------------------------------------------------------


def test_the_table_carries_the_research_figures():
    entry = load_local_prices()["oxygen concentrator"]
    rows = {row["ref"]: row for row in entry["rows"]}

    assert entry["as_of"] == "2026-10-17"
    assert rows["A12"]["price_usd"] == 288
    assert rows["A12"]["url"] == "https://supply.unicef.org/catalogsearch/result/?q=oxygen"
    assert (rows["A1"]["price_usd_min"], rows["A1"]["price_usd_max"]) == (1319.19, 2272.98)
    assert rows["A1"]["url"] == "https://www.ato.com/1-phase-frequency-converter"
    assert rows["A9"]["price_usd"] == 205.41
    assert rows["A9"]["url"] == "https://supply.unicef.org/s0002632.html"
    assert all(row["quote"] for row in entry["rows"])


def test_the_table_is_read_by_type_ignoring_case_and_spacing(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"_source": "x", "Oxygen  Concentrator": {"rows": []}}))

    assert list(load_local_prices(path)) == ["oxygen concentrator"]
    assert load_local_prices(tmp_path / "absent.json") == {}


@pytest.mark.parametrize("row, shown", [
    ({"price_usd": 288, "indicative": True}, "about $290"),
    ({"price_usd_min": 1319.19, "price_usd_max": 2272.98}, "$1,319 to $2,273"),
    ({"price_usd": 205.41}, "$205"),
])
def test_prices_are_shown_in_round_dollars(row, shown):
    assert price_text(row) == shown


# --- when the advisory appears ----------------------------------------------


def test_a_frequency_rejection_gets_the_advisory_rows_and_sources():
    advice = advisory_for(candidate())

    assert isinstance(advice, Advisory)
    assert advice.as_of == "2026-10-17"
    assert [r["price_text"] for r in advice.rows] == ["about $290", "$1,319 to $2,273", "$205"]
    assert all(r["url"].startswith("https://") and r["source"] for r in advice.rows)


def test_a_rejection_for_another_reason_gets_none():
    assert advisory_for(candidate(keys=("support",))) is None


@pytest.mark.parametrize("outcome, status", [
    (Outcome.WORKS_IF, CheckStatus.MITIGABLE),
    (Outcome.NEEDS_INFO, CheckStatus.NEEDS_INFO),
    (Outcome.WORKS, CheckStatus.PASS),
])
def test_only_a_rejected_verdict_gets_one(outcome, status):
    assert advisory_for(candidate(outcome=outcome, status=status)) is None


def test_a_type_without_prices_gets_none():
    assert advisory_for(candidate(device_type="infusion pump")) is None


def test_unknown_sold_locally_asks_the_nonprofit_and_links_the_form():
    advice = advisory_for(candidate())

    assert advice.sold_locally_text == "Sold in Uganda: don't know, ask the nonprofit"
    assert advice.supply_url == "/supply/Uganda?device_type=oxygen+concentrator&model="


@pytest.mark.parametrize("value, word", [(True, "yes"), (False, "no")])
def test_a_recorded_sold_locally_is_stated_with_its_source(value, word):
    sold = fact(value, Provenance.RECORDED, "recorded by Asha N., 2026-10-17")

    advice = advisory_for(candidate(sold=sold))

    assert advice.sold_locally_text == f"Sold in Uganda: {word} (recorded by Asha N., 2026-10-17)"
    assert advice.supply_url == ""


def test_the_advisory_writes_no_verdict():
    rejected = candidate()

    advisory_for(rejected)

    assert rejected.verdict.outcome is Outcome.WONT_WORK


# --- the partial ----------------------------------------------------------


def render(c, advice, path="/request/4/matches"):
    class FakeRequest:
        class url:
            pass
    FakeRequest.url.path = path
    return TEMPLATES.get_template("_advisory.html").render(
        advisory={c.offer.id: advice}, c=c, request=FakeRequest)


def test_the_partial_is_a_gray_block_without_a_verdict_word():
    c = candidate()
    html = render(c, advisory_for(c))

    assert "Buy locally instead?" in html
    assert "var(--raise)" in html
    assert "about $290" in html and "$1,319 to $2,273" in html and "$205" in html
    assert 'href="https://supply.unicef.org/s0002632.html"' in html
    assert "prices as read on 2026-10-17" in html
    lowered = html.lower()
    assert not [w for w in VERDICT_WORDS if w in lowered]


def test_the_partial_links_the_question_back_to_this_page():
    c = candidate()
    html = render(c, advisory_for(c))

    assert ('href="/supply/Uganda?device_type=oxygen+concentrator&amp;model=&amp;back=/request/4/matches"'
            in html)
    assert "don&#39;t know, ask the nonprofit" in html


def test_the_partial_renders_nothing_without_an_advisory():
    assert render(candidate(), None).strip() == ""


# --- on the matches page ----------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'advisory.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    monkeypatch.setattr(marketplace_routes, "categorise_support", lambda *a: None)
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def seed_kayunga_request() -> int:
    repo.save_facility(KAYUNGA)
    return repo.save_request(Request(
        facility_slug=KAYUNGA.slug,
        lines=(RequestLine(device_type="oxygen concentrator", quantity=1),),
    ))


def test_the_real_verdict_for_a_60_hz_unit_at_kayunga_gets_the_advisory():
    intel = SupplyIntel(country=KAYUNGA.country)
    verdict = check_fit(SIXTY_HZ, KAYUNGA, intel)

    assert verdict.outcome is Outcome.WONT_WORK
    assert "frequency" in [r.key for r in verdict.failures]
    advice = advisory_for(Candidate(offer=an_offer(id=1), verdict=verdict, intel=intel))
    assert advice is not None and advice.rows


def test_the_matches_page_shows_the_advisory_under_the_rejected_offer(client, monkeypatch):
    request_id = seed_kayunga_request()
    offer_id = repo.save_offer(an_offer())
    intel = SupplyIntel(country=KAYUNGA.country)
    rejected = Candidate(offer=repo.load_offer(offer_id), verdict=check_fit(SIXTY_HZ, KAYUNGA, intel),
                         intel=intel)
    monkeypatch.setattr(marketplace_routes, "screen_offers",
                        lambda *a, **kw: Screening(ranked=[], rejected=[rejected]))

    text = client.get(f"/request/{request_id}/matches").text

    rejected_at = text.index("Won't work at Kayunga District Hospital")
    assert text.index("Buy locally instead?") > rejected_at
    assert "about $290" in text and "Sold in Uganda: don&#39;t know, ask the nonprofit" in text
    assert f"back=/request/{request_id}/matches" in text


def test_end_to_end_once_the_screening_lists_rejections(client):
    request_id = seed_kayunga_request()
    offer = repo.load_offer(repo.save_offer(an_offer()))
    screening = marketplace_routes.screen_offers(KAYUNGA, [offer],
                                                 intel_for=marketplace_routes.supply_lookup)
    if not screening.rejected:
        pytest.skip("screen_offers lists no rejected offers until [B1] lands")

    text = client.get(f"/request/{request_id}/matches").text

    assert "Buy locally instead?" in text and "about $290" in text
