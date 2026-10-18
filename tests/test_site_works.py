"""Design §2.4: site works and training fail; a FAIL never carries a Requirement."""

from __future__ import annotations

from datetime import date

import pytest

from app.checks import check_fit, run_checks
from app.checks.documentation import check_operation_and_repair
from app.checks.environment import check_grid_reliability, check_humidity, check_temperature
from app.checks.utilities import check_compressed_air, check_distilled_water, check_oxygen
from app.domain.device import DeviceFacts
from app.domain.provenance import UNKNOWN, Provenance, fact
from app.domain.requirement import RequirementKind
from app.domain.result import CheckStatus, Outcome
from tests.factories import device, facility, intel

SPEC = Provenance.SPEC_SHEET


def test_a_hot_ward_without_air_conditioning_fails_and_names_what_is_missing():
    result = check_temperature(
        device(temp_max_c=fact(35.0, SPEC)),
        facility(ambient_temp_max_c=41.0, has_air_conditioning=False, name="Kayunga"),
        intel(),
    )

    assert result.status is CheckStatus.FAIL
    assert "needs air conditioning the ward at Kayunga does not have" in result.detail
    assert "Kayunga" in result.fix and result.requirement is None


@pytest.mark.parametrize("check, need, has, words", [
    (check_oxygen, "needs_oxygen", "has_oxygen_supply", "medical oxygen supply"),
    (check_distilled_water, "needs_distilled_water", "has_distilled_water", "distilled water supply"),
    (check_compressed_air, "needs_compressed_air", "has_compressed_air", "medical air supply"),
])
def test_a_utility_the_clinic_lacks_fails_with_no_requirement(check, need, has, words):
    result = check(device(**{need: fact(True, SPEC)}), facility(**{has: False}, name="Kayunga"), intel())

    assert result.status is CheckStatus.FAIL
    assert result.requirement is None
    assert result.fix.startswith("Kayunga would need")
    assert words in result.fix


@pytest.mark.parametrize("check, need, has", [
    (check_oxygen, "needs_oxygen", "has_oxygen_supply"),
    (check_distilled_water, "needs_distilled_water", "has_distilled_water"),
    (check_compressed_air, "needs_compressed_air", "has_compressed_air"),
])
def test_a_utility_needed_and_present_still_passes(check, need, has):
    result = check(device(**{need: fact(True, SPEC)}), facility(**{has: True}), intel())

    assert result.status is CheckStatus.PASS


def test_a_high_skill_device_without_trained_staff_fails_naming_training_and_service():
    result = check_operation_and_repair(
        device(operating_complexity=fact("high", SPEC)),
        facility(trained_operators=False, has_biomed_technician=False, name="Kayunga"),
        intel(),
    )

    assert result.status is CheckStatus.FAIL
    assert result.requirement is None
    assert "trained" in result.fix and "service contract" in result.fix
    assert "nobody trained to run it" in result.detail


def test_a_low_skill_device_without_trained_staff_still_passes():
    result = check_operation_and_repair(
        device(operating_complexity=fact("low", SPEC)),
        facility(trained_operators=False, has_biomed_technician=False),
        intel(),
    )

    assert result.status is CheckStatus.PASS


def test_humidity_and_grid_stay_add_ons():
    humid = check_humidity(device(humidity_max_pct=fact(80.0, SPEC)), facility(humidity_max_pct=95.0), intel())
    grid = check_grid_reliability(device(), facility(outages_per_week=6, has_voltage_stabilizer=False), intel())

    assert humid.requirement.kind is RequirementKind.DEHUMIDIFIER
    assert grid.requirement.kind is RequirementKind.STABILISER


HOSTILE_DEVICES = [
    device(frequencies_hz=fact([60], SPEC)),
    device(temp_max_c=fact(20.0, SPEC)),
    device(temp_max_c=fact(30.0, SPEC)),
    device(needs_oxygen=fact(True, SPEC), needs_distilled_water=fact(True, SPEC),
           needs_compressed_air=fact(True, SPEC)),
    device(operating_complexity=fact("high", SPEC)),
    device(year_of_manufacture=fact(1990, SPEC)),
    device(expiry_date=fact(date(2001, 1, 1), Provenance.DONOR_STATED),
           dated_items=fact(True, Provenance.DONOR_STATED)),
    device(manual_languages=fact(["Japanese"], SPEC)),
    DeviceFacts(),
]
HOSTILE_SITES = [
    facility(),
    facility(ambient_temp_max_c=44.0, has_air_conditioning=False, has_oxygen_supply=False,
             has_distilled_water=False, has_compressed_air=False, trained_operators=False,
             has_biomed_technician=False, outages_per_week=9, has_generator=False,
             has_voltage_stabilizer=False, humidity_max_pct=99.0, mains_voltage_v=110,
             mains_frequency_hz=60, staff_languages=("French",)),
]


@pytest.mark.parametrize("dev", HOSTILE_DEVICES)
@pytest.mark.parametrize("site", HOSTILE_SITES)
def test_no_fail_anywhere_carries_a_requirement(dev, site):
    for supported in (True, False):
        research = intel(manufacturer_supported=fact(supported, Provenance.RECORDED),
                         consumables_available=fact(False, Provenance.RECORDED),
                         spare_parts_available=fact(False, Provenance.RECORDED))
        for result in run_checks(dev, site, research):
            if result.status is CheckStatus.FAIL:
                assert result.requirement is None, result.key


def test_no_check_raises_a_site_works_requirement():
    site = HOSTILE_SITES[1]
    for dev in HOSTILE_DEVICES:
        verdict = check_fit(dev, site, intel())
        kinds = {r.kind for r in verdict.needs}
        assert not kinds & {RequirementKind.COOLING, RequirementKind.OXYGEN_SUPPLY,
                            RequirementKind.WATER_SUPPLY, RequirementKind.AIR_COMPRESSOR,
                            RequirementKind.TRAINING}


def test_site_works_make_the_verdict_wont_work():
    verdict = check_fit(device(needs_oxygen=fact(True, SPEC)), facility(has_oxygen_supply=False), intel())

    assert verdict.outcome is Outcome.WONT_WORK
    assert [r.key for r in verdict.failures] == ["oxygen"]
