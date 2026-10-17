from app.checks.power import check_frequency, check_voltage
from app.domain.provenance import Provenance, UNKNOWN, fact
from app.domain.result import CheckStatus
from tests.factories import device, facility, intel


def test_frequency_passes_when_the_device_is_rated_for_both_grids():
    # Arrange
    dev = device(frequencies_hz=fact([50, 60], Provenance.NAMEPLATE))
    site = facility(mains_frequency_hz=50)

    # Act
    result = check_frequency(dev, site, intel())

    # Assert
    assert result.status is CheckStatus.PASS


def test_frequency_fails_when_a_60_hz_device_meets_a_50_hz_grid():
    dev = device(frequencies_hz=fact([60], Provenance.NAMEPLATE))
    site = facility(mains_frequency_hz=50)

    result = check_frequency(dev, site, intel())

    assert result.status is CheckStatus.FAIL
    assert "60" in result.detail and "50" in result.detail


def test_frequency_asks_a_question_when_the_rating_is_unknown():
    dev = device(frequencies_hz=UNKNOWN)

    result = check_frequency(dev, facility(), intel())

    assert result.status is CheckStatus.NEEDS_INFO
    assert result.question


def test_voltage_passes_for_an_auto_ranging_supply():
    dev = device(
        voltage_min_v=fact(100, Provenance.NAMEPLATE),
        voltage_max_v=fact(240, Provenance.NAMEPLATE),
    )
    site = facility(mains_voltage_v=240)

    result = check_voltage(dev, site, intel())

    assert result.status is CheckStatus.PASS


def test_voltage_is_mitigable_with_a_transformer_sized_above_the_draw():
    dev = device(
        voltage_min_v=fact(110, Provenance.NAMEPLATE),
        voltage_max_v=fact(120, Provenance.NAMEPLATE),
        power_watts=fact(400, Provenance.NAMEPLATE),
    )
    site = facility(mains_voltage_v=240)

    result = check_voltage(dev, site, intel())

    assert result.status is CheckStatus.MITIGABLE
    assert "500 W" in result.fix
