from datetime import date

from app.checks.supply import check_consumables, check_spare_parts, check_support_window
from app.domain.provenance import Provenance, UNKNOWN, fact
from app.domain.result import CheckStatus
from tests.factories import device, facility, intel


def test_consumables_pass_when_the_device_consumes_nothing():
    dev = device(consumables=fact([], Provenance.SPEC_SHEET))

    result = check_consumables(dev, facility(), intel())

    assert result.status is CheckStatus.PASS


def test_consumables_are_mitigable_when_they_are_not_sold_in_country():
    dev = device(consumables=fact(["patient circuits"], Provenance.SPEC_SHEET))
    research = intel(
        country="Uganda",
        consumables_available=fact(False, Provenance.MODEL_KNOWLEDGE),
    )

    result = check_consumables(dev, facility(), research)

    assert result.status is CheckStatus.MITIGABLE
    assert "patient circuits" in result.fix


def test_consumables_ask_a_question_when_availability_is_unknown():
    dev = device(consumables=fact(["reagents"], Provenance.SPEC_SHEET))
    research = intel(country="Haiti", consumables_available=UNKNOWN)

    result = check_consumables(dev, facility(), research)

    assert result.status is CheckStatus.NEEDS_INFO
    assert "Haiti" in result.question


def test_spare_parts_are_mitigable_when_unavailable_in_country():
    research = intel(
        country="Uganda", spare_parts_available=fact(False, Provenance.MODEL_KNOWLEDGE)
    )

    result = check_spare_parts(device(), facility(), research)

    assert result.status is CheckStatus.MITIGABLE


def test_support_fails_when_the_manufacturer_has_dropped_the_model():
    research = intel(manufacturer_supported=fact(False, Provenance.MODEL_KNOWLEDGE))

    result = check_support_window(device(), facility(), research)

    assert result.status is CheckStatus.FAIL


def test_support_fails_for_a_device_past_its_working_life():
    dev = device(year_of_manufacture=fact(date.today().year - 18, Provenance.NAMEPLATE))

    result = check_support_window(dev, facility(), intel())

    assert result.status is CheckStatus.FAIL


def test_support_is_mitigable_for_an_ageing_but_usable_device():
    dev = device(year_of_manufacture=fact(date.today().year - 11, Provenance.NAMEPLATE))

    result = check_support_window(dev, facility(), intel())

    assert result.status is CheckStatus.MITIGABLE
