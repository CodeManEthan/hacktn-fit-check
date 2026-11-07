"""Battery-only devices: no supply voltage, frequency or grid ask, everything else as before."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.checks.environment import check_grid_reliability
from app.checks.power import BATTERY_ONLY, check_frequency, check_voltage
from app.checks.runner import check_fit
from app.data.seed import FACILITIES
from app.data.serialize import device_from_dict, device_to_dict
from app.domain.device import SupplyIntel
from app.domain.provenance import UNKNOWN, Provenance, fact
from app.domain.requirement import RequirementKind
from app.domain.result import CheckStatus
from app.reference import device_library as lib
from app.web.presenters import identity_pairs
from tests.factories import device, facility, intel

SPEC = Provenance.SPEC_SHEET
MAINS_KEYS = ("voltage", "frequency", "grid")
POWER_ASKS = {RequirementKind.TRANSFORMER, RequirementKind.BACKUP_POWER, RequirementKind.STABILISER}
CLINICS = {f.slug: f for f in FACILITIES}
SONOTRAX = "edan-sonotrax-basic-a"
BATTERY_SLUGS = {SONOTRAX, "roche-accu-chek-performa"}


def _library(slug: str):
    return lib.to_device_facts(lib.entry(slug))


def _results(verdict) -> dict:
    return {r.key: r for r in verdict.results}


# ---- the three checks -----------------------------------------------------------

BATTERY = device(voltage_min_v=UNKNOWN, voltage_max_v=UNKNOWN, frequencies_hz=UNKNOWN,
                 mains_powered=fact(False, SPEC))
# A clinic where every mains check would otherwise ask for something.
HARSH = facility(mains_voltage_v=110, mains_frequency_hz=60, outages_per_week=10,
                 has_voltage_stabilizer=False, has_generator=False)


@pytest.mark.parametrize("check", [check_voltage, check_frequency, check_grid_reliability])
def test_a_battery_only_device_passes_the_mains_checks(check):
    result = check(BATTERY, HARSH, intel())

    assert result.status is CheckStatus.PASS
    assert result.detail == BATTERY_ONLY
    assert result.requirement is None
    assert result.source is SPEC


@pytest.mark.parametrize("mains", [UNKNOWN, fact(True, SPEC)])
@pytest.mark.parametrize("check", [check_voltage, check_frequency, check_grid_reliability])
def test_unknown_or_mains_powered_runs_the_check_as_before(check, mains):
    plain = device(voltage_min_v=UNKNOWN, voltage_max_v=UNKNOWN, frequencies_hz=UNKNOWN)
    marked = replace(plain, mains_powered=mains)

    assert check(marked, HARSH, intel()) == check(plain, HARSH, intel())
    assert check(marked, HARSH, intel()).status is not CheckStatus.PASS


# ---- the library device at the seeded clinics -----------------------------------

@pytest.mark.parametrize("slug", ["nepal-phc", "uganda-district"])
def test_the_sonotrax_gets_no_voltage_frequency_or_ups_ask(slug):
    clinic = CLINICS[slug]
    verdict = check_fit(_library(SONOTRAX), clinic, SupplyIntel(country=clinic.country))
    rows = _results(verdict)

    for key in MAINS_KEYS:
        assert rows[key].status is CheckStatus.PASS, key
        assert rows[key].detail == BATTERY_ONLY, key
    assert not {need.kind for need in verdict.needs} & POWER_ASKS
    assert not any(r.question for r in verdict.results if r.key in MAINS_KEYS)


@pytest.mark.parametrize("slug", ["nepal-phc", "uganda-district"])
def test_a_mains_device_at_the_same_clinic_is_unchanged(slug):
    clinic = CLINICS[slug]
    mains = _library("drive-devilbiss-525ks")
    assert not mains.mains_powered.known

    rows = _results(check_fit(mains, clinic, SupplyIntel(country=clinic.country)))

    assert rows["grid"].status is CheckStatus.MITIGABLE
    assert rows["grid"].requirement.kind in POWER_ASKS
    assert "batteries" not in rows["voltage"].detail and "batteries" not in rows["frequency"].detail


def test_only_battery_only_rows_change_and_no_outcome_does():
    # Every library device at every seeded clinic, against the same device
    # with the fact taken away: what the screening history would hold.
    changed = set()
    for e in lib.load_library():
        built = lib.to_device_facts(e)
        before = replace(built, mains_powered=UNKNOWN)
        for clinic in FACILITIES:
            given = SupplyIntel(country=clinic.country)
            old, new = check_fit(before, clinic, given), check_fit(built, clinic, given)
            assert new.outcome is old.outcome, (e.slug, clinic.slug)
            was, now = _results(old), _results(new)
            assert was.keys() == now.keys(), (e.slug, clinic.slug)
            differ = {key for key in was if was[key] != now[key]}
            if differ:
                changed.add(e.slug)
                assert differ <= set(MAINS_KEYS), (e.slug, clinic.slug, differ)
    assert changed == BATTERY_SLUGS


# ---- the library data, storage and the verdict page -----------------------------

def test_the_library_marks_exactly_the_two_battery_devices():
    marked = {e.slug for e in lib.load_library() if "mains_powered" in e.facts}
    assert marked == BATTERY_SLUGS
    for slug in BATTERY_SLUGS:
        facts = _library(slug)
        assert facts.mains_powered.value is False and facts.battery_only, slug
        assert "batter" in lib.entry(slug).facts["mains_powered"]["quote"], slug
    assert lib.entry(SONOTRAX).facts["mains_powered"]["page"] == 15


def test_a_stored_device_without_the_fact_loads_as_unknown():
    stored = device_to_dict(device())
    del stored["mains_powered"]

    loaded = device_from_dict(stored)

    assert not loaded.mains_powered.known and not loaded.battery_only
    assert device_from_dict(device_to_dict(BATTERY)).battery_only


def test_the_supply_line_says_batteries():
    assert ("Supply", "Batteries") in identity_pairs(_library(SONOTRAX))
    assert ("Supply", "100–240 V") in identity_pairs(device())
    assert ("Supply", "Batteries") not in identity_pairs(device())
