from app.checks.environment import (
    check_grid_reliability,
    check_humidity,
    check_temperature,
)
from app.domain.device import DeviceFacts
from app.domain.provenance import Provenance, UNKNOWN, fact
from app.domain.result import CheckStatus
from tests.factories import device, facility, intel


def test_temperature_passes_when_the_room_stays_inside_the_rating():
    dev = device(temp_max_c=fact(40.0, Provenance.SPEC_SHEET))
    site = facility(ambient_temp_max_c=30.0, has_air_conditioning=False)

    result = check_temperature(dev, site, intel())

    assert result.status is CheckStatus.PASS


def test_temperature_is_mitigable_when_a_hot_room_has_no_air_conditioning():
    dev = device(temp_max_c=fact(40.0, Provenance.SPEC_SHEET))
    site = facility(ambient_temp_max_c=44.0, has_air_conditioning=False)

    result = check_temperature(dev, site, intel())

    assert result.status is CheckStatus.MITIGABLE
    assert "air conditioning" in result.fix.lower()


def test_temperature_fails_when_even_air_conditioning_cannot_save_it():
    dev = device(temp_max_c=fact(20.0, Provenance.SPEC_SHEET))
    site = facility(ambient_temp_max_c=44.0, has_air_conditioning=True)

    result = check_temperature(dev, site, intel())

    assert result.status is CheckStatus.FAIL


def test_an_unstated_climate_range_falls_back_to_the_standard_default():
    # Arrange: a device whose manual says nothing about temperature
    dev = DeviceFacts().with_defaults()

    # Act
    site = facility(ambient_temp_max_c=44.0, has_air_conditioning=False)
    result = check_temperature(dev, site, intel())

    # Assert: 40 C is the IEC 60601-1 default, so a 44 C room is a flag, not a blank
    assert result.status is CheckStatus.MITIGABLE
    assert result.source is Provenance.STANDARD_DEFAULT


def test_humidity_is_mitigable_above_the_rated_maximum():
    dev = device(humidity_max_pct=fact(80.0, Provenance.SPEC_SHEET))
    site = facility(humidity_max_pct=95.0)

    result = check_humidity(dev, site, intel())

    assert result.status is CheckStatus.MITIGABLE


def test_grid_passes_when_a_generator_and_stabiliser_cover_the_outages():
    site = facility(outages_per_week=10, has_generator=True, has_voltage_stabilizer=True)

    result = check_grid_reliability(device(), site, intel())

    assert result.status is CheckStatus.PASS


def test_grid_is_mitigable_when_outages_are_frequent_and_unprotected():
    site = facility(outages_per_week=7, has_generator=False, has_voltage_stabilizer=False)

    result = check_grid_reliability(device(), site, intel())

    assert result.status is CheckStatus.MITIGABLE
    assert "stabiliser" in result.fix


def test_grid_findings_are_attributed_to_the_clinic_not_the_device():
    # The clinic reports its own outages, so the fact is theirs, not the nameplate's.
    site = facility(outages_per_week=7, has_generator=False, has_voltage_stabilizer=False)

    result = check_grid_reliability(device(), site, intel())

    assert result.source is Provenance.FACILITY_STATED
