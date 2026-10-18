"""The hero trio against Kayunga on facts typed here (design §4.1, §4.3; verification-1).

B3 owns the seeded acceptance test (tests/test_hero_trio.py). This one pins
the rules: with the 525KS and 525DS facts as the library will carry them,
and the recorded Uganda answer for the 525KS, the outcomes are the ruled
ones. The import check reads the committed data/import_rules.json.
"""

from __future__ import annotations

import pytest

from app.config import PROJECT_ROOT
from app.data.seed import get_facility
from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.language import normalise_languages
from app.domain.marketplace import GeoPoint, Offer
from app.domain.provenance import Provenance, fact
from app.domain.requirement import RequirementKind
from app.domain.result import CheckStatus, Outcome
from app.matching.pipeline import empty_intel, screen_offers
from app.reference import import_rules

SPEC = Provenance.SPEC_SHEET
DONOR = Provenance.DONOR_STATED
RECORDED = Provenance.RECORDED
KAYUNGA = get_facility("uganda-district")

FILTERS = ["MC44D-605", "303DZ-605", "PV5LD-651", "525DD-626"]
KS_LANGUAGES = ["en", "es", "fr", "de", "it", "nl", "tr", "pt", "pl", "ar", "el", "da",
                "sv", "fi", "no", "cs", "sk", "zh-Hans", "sl", "ro", "ms"]
KS_MANUAL = {
    "slug": "drive-devilbiss-525ks",
    "file": "drive-devilbiss-525ks/SE-525KS-1_RevD.txt",
    "title": "525KS Instruction Guide SE-525KS-1 Rev D",
    "url": "https://www.drivemedical.de/ALL_PRODUCT_Media/RESPIRATORY/Oxygen/525KS/pdf/"
           "User%20Manual/SE-525KS-1%20Rev_D.pdf",
    "languages": normalise_languages(KS_LANGUAGES),
}


def concentrator(model: str, low: int, high: int, hz: list[int], watts: int, year: int,
                 languages: list[str]) -> DeviceFacts:
    return DeviceFacts(
        manufacturer=fact("DeVilbiss Healthcare LLC", SPEC),
        model=fact(model, SPEC),
        year_of_manufacture=fact(year, DONOR),
        voltage_min_v=fact(low, SPEC),
        voltage_max_v=fact(high, SPEC),
        frequencies_hz=fact(hz, SPEC),
        power_watts=fact(watts, SPEC),
        temp_min_c=fact(5.0, SPEC),
        temp_max_c=fact(35.0, SPEC),
        humidity_max_pct=fact(93.0, SPEC),
        needs_oxygen=fact(False, SPEC),
        needs_distilled_water=fact(False, SPEC),
        needs_compressed_air=fact(False, SPEC),
        consumables=fact(list(FILTERS), SPEC),
        dated_items=fact(False, DONOR),
        manual_languages=fact(normalise_languages(languages), SPEC),
        documents=fact([dict(KS_MANUAL)], SPEC),
        operating_complexity=fact("low", SPEC),
    )


OFFER_A = Offer(  # Nashville, the 115 V / 60 Hz build
    donor_name="Nashville General Hospital biomedical department", donor_email="a@example.org",
    device=concentrator("525DS", 97, 127, [60], 310, 2016, ["en", "es", "fr"]),
    device_type="oxygen concentrator", quantity_offered=1, id=1,
    origin=GeoPoint(country="United States", city="Nashville", latitude=36.1627, longitude=-86.7816),
)
OFFER_B = Offer(  # Leeds, the 230 V / 50 Hz build
    donor_name="Leeds Teaching Hospitals NHS Trust, surplus", donor_email="b@example.org",
    device=concentrator("525KS", 187, 253, [50], 312, 2019, KS_LANGUAGES),
    device_type="oxygen concentrator", quantity_offered=1, id=2,
    origin=GeoPoint(country="United Kingdom", city="Leeds", latitude=53.8008, longitude=-1.5491),
)
OFFER_C = Offer(  # Houston, nothing known
    donor_name="Harris County clinic network", donor_email="c@example.org",
    device=DeviceFacts(raw_input="older oxygen concentrator, works fine, 2 units"),
    device_type="oxygen concentrator", quantity_offered=2, id=3,
    origin=GeoPoint(country="United States", city="Houston", latitude=29.7604, longitude=-95.3698),
)


def recorded_uganda(device: DeviceFacts, device_type: str, country: str) -> SupplyIntel:
    """The one supply_answers row of §4.3: the 525KS in Uganda. Nothing for any model."""
    if country == "Uganda" and device.model.value == "525KS":
        return SupplyIntel(
            country=country,
            consumables_available=fact(False, RECORDED),
            spare_parts_available=fact(True, RECORDED),
        )
    return empty_intel(device, device_type, country)


@pytest.fixture(autouse=True)
def committed_import_rules(monkeypatch):
    monkeypatch.setattr(import_rules, "RULES_PATH", PROJECT_ROOT / "data" / "import_rules.json")


@pytest.fixture
def screening():
    return screen_offers(KAYUNGA, [OFFER_A, OFFER_B, OFFER_C], recorded_uganda)


def by_key(candidate):
    return {r.key: r for r in candidate.verdict.results}


def test_offer_b_works_if_a_stabiliser_and_filters_go_with_it(screening):
    b = next(c for c in screening.ranked if c.offer.id == 2)

    assert b.verdict.outcome is Outcome.WORKS_IF
    assert {r.kind for r in b.verdict.needs} == {RequirementKind.STABILISER,
                                                 RequirementKind.CONSUMABLES}
    assert b.verdict.unknowns == ()
    assert b.verdict.failures == ()
    results = by_key(b)
    assert results["import"].status is CheckStatus.PASS
    assert "English" in results["import"].detail
    assert results["manual"].status is CheckStatus.PASS
    assert results["shelf_life"].status is CheckStatus.PASS
    assert len(results) == 15


def test_offer_a_wont_work_on_frequency_alone(screening):
    (a,) = screening.rejected

    assert a.offer.id == 1
    assert a.verdict.outcome is Outcome.WONT_WORK
    assert [r.key for r in a.verdict.failures] == ["frequency"]
    assert by_key(a)["import"].status is CheckStatus.PASS


def test_offer_c_needs_info_and_ranks_below_b(screening):
    assert [c.offer.id for c in screening.ranked] == [2, 3]
    c = screening.ranked[1]
    assert c.verdict.outcome is Outcome.NEEDS_INFO
    assert c.verdict.failures == ()
    assert by_key(c)["import"].status is CheckStatus.NEEDS_INFO
