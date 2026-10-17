"""A request is a list of things a clinic needs, not a single thing.

The clinic that needs two ventilators and five monitors is the normal case;
the single-line request is just the short one. These tests fix that shape.
"""

from __future__ import annotations

import pytest

from app.domain.marketplace import Request, RequestLine

TWO_VENTILATORS = RequestLine(device_type="ventilator", quantity=2)
FIVE_MONITORS = RequestLine(device_type="patient monitor", quantity=5)


def a_request(**overrides) -> Request:
    base = {"facility_slug": "uganda-district", "lines": (TWO_VENTILATORS, FIVE_MONITORS)}
    return Request(**{**base, **overrides})


def test_total_quantity_adds_every_line():
    assert a_request().total_quantity == 7


def test_device_types_lists_each_kind_once_in_order():
    # Arrange: the same kind asked for twice, as a clinic splitting wards might.
    duplicated = a_request(
        lines=(TWO_VENTILATORS, FIVE_MONITORS, RequestLine("ventilator", 1))
    )

    # Act / Assert
    assert duplicated.device_types == ("ventilator", "patient monitor")


def test_an_empty_request_totals_nothing_rather_than_failing():
    assert a_request(lines=()).total_quantity == 0
    assert a_request(lines=()).device_types == ()


def test_a_line_must_ask_for_at_least_one_unit():
    with pytest.raises(ValueError):
        RequestLine(device_type="ventilator", quantity=0)


def test_a_line_must_name_a_device_type():
    with pytest.raises(ValueError):
        RequestLine(device_type="   ", quantity=1)


def test_a_line_keeps_its_device_type_trimmed_and_lowercased():
    # Arrange / Act: clinics type inconsistently; matching is on this string.
    line = RequestLine(device_type="  Patient Monitor  ", quantity=1)

    # Assert
    assert line.device_type == "patient monitor"


def test_summary_reads_as_a_sentence_a_donor_would_understand():
    assert a_request().summary == "2 ventilators and 5 patient monitors"


def test_summary_of_a_single_unit_is_not_pluralised():
    assert a_request(lines=(RequestLine("ventilator", 1),)).summary == "1 ventilator"


def test_a_request_is_immutable():
    subject = a_request()
    with pytest.raises(Exception):
        subject.lines = ()
