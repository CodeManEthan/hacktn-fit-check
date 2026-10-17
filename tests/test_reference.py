"""Tests for the reference-data layer.

No test here touches the network: the registry tests patch urlopen.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

from app.reference import country_power, device_class, device_registry
from app.reference.country_power import (
    CountryPower,
    CountryPowerDataError,
    country_centroid,
    lookup,
    parse_values,
    representative_value,
)
from app.reference.device_class import (
    DeviceClassDataError,
    lookup_class,
    normalise_name,
)
from app.reference.device_registry import (
    DeviceRegistryRecord,
    is_still_supported,
    lookup_model,
)

NETWORK_TARGET = "app.reference.device_registry.urllib.request.urlopen"


class FakeResponse:
    """Stands in for the context manager urlopen returns."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _clear_caches():
    """Each test reloads the tables, so a patched path cannot leak."""
    country_power._index.cache_clear()
    device_class._index.cache_clear()
    yield
    country_power._index.cache_clear()
    device_class._index.cache_clear()


# --------------------------------------------------------------------------
# country_power: parsing multi-valued voltage strings
# --------------------------------------------------------------------------


def test_parse_values_reads_a_single_voltage():
    # Arrange
    raw = "230"

    # Act
    values = parse_values(raw)

    # Assert
    assert values == (230,)


def test_parse_values_expands_a_hyphenated_voltage_range():
    raw = "220-240"

    values = parse_values(raw)

    assert values == (220, 240)


def test_parse_values_reads_a_space_separated_list_of_voltages():
    # This is the shape the committed table actually uses, e.g. Japan.
    raw = "100 200 210"

    values = parse_values(raw)

    assert values == (100, 200, 210)


def test_parse_values_returns_nothing_for_a_blank_string():
    values = parse_values("   ")

    assert values == ()


def test_representative_value_takes_the_upper_bound_of_a_range():
    # Arrange
    raw = "220-240"

    # Act
    representative = representative_value(raw)

    # Assert
    assert representative == 240


def test_representative_value_takes_the_highest_of_a_listed_set():
    assert representative_value("120 127 240") == 240


def test_representative_value_is_none_when_nothing_parses():
    assert representative_value("unknown") is None


def test_multi_voltage_country_is_flagged_as_multi_voltage():
    record = CountryPower(
        country_code="BR",
        country_name="Brazil",
        mains_voltage_v=220,
        mains_frequency_hz=60,
        plug_types=("C", "N"),
        mains_voltage_raw="127 220",
    )

    assert record.is_multi_voltage is True


# --------------------------------------------------------------------------
# country_power: lookup hit and miss
# --------------------------------------------------------------------------


def test_lookup_finds_a_country_by_name():
    # Arrange
    name = "Uganda"

    # Act
    record = lookup(name)

    # Assert
    assert record is not None
    assert record.country_code == "UG"
    assert record.mains_voltage_v == 240
    assert record.mains_frequency_hz == 50
    assert "G" in record.plug_types


def test_lookup_is_case_insensitive_on_the_country_name():
    assert lookup("uGaNdA") == lookup("Uganda")


def test_lookup_finds_a_country_by_iso_code():
    # Arrange
    code = "np"

    # Act
    record = lookup(code)

    # Assert
    assert record is not None
    assert record.country_name == "Nepal"


def test_lookup_returns_none_for_a_country_that_is_not_in_the_table():
    record = lookup("Wakanda")

    assert record is None


def test_lookup_returns_none_for_a_blank_query():
    assert lookup("   ") is None


def test_lookup_keeps_the_raw_voltage_string_alongside_the_parsed_value():
    # Japan lists three nominal voltages; the raw string must survive.
    record = lookup("Japan")

    assert record is not None
    assert record.mains_voltage_raw == "100 200 210"
    assert record.mains_voltage_v == 210


def test_lookup_raises_a_named_error_when_the_table_is_missing(tmp_path):
    # Arrange
    missing = tmp_path / "gone.csv"

    # Act / Assert
    with patch.object(country_power, "DATA_PATH", missing):
        with pytest.raises(CountryPowerDataError, match="missing"):
            lookup("Uganda")


def test_lookup_raises_a_named_error_when_the_table_has_no_expected_columns(tmp_path):
    broken = tmp_path / "broken.csv"
    broken.write_text("foo,bar\n1,2\n", encoding="utf-8")

    with patch.object(country_power, "DATA_PATH", broken):
        with pytest.raises(CountryPowerDataError, match="missing columns"):
            lookup("Uganda")


# --------------------------------------------------------------------------
# country_power: centroids
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "country",
    [
        "Uganda",
        "Haiti",
        "Nepal",
        "United States",
        "India",
        "Kenya",
        "Nigeria",
        "Ghana",
        "Bangladesh",
        "Philippines",
        "Guatemala",
        "Malawi",
        "Tanzania",
        "Zambia",
        "Ethiopia",
    ],
)
def test_country_centroid_is_known_for_every_shipping_destination(country):
    # Act
    centroid = country_centroid(country)

    # Assert
    assert centroid is not None
    latitude, longitude = centroid
    assert -90.0 <= latitude <= 90.0
    assert -180.0 <= longitude <= 180.0


def test_country_centroid_accepts_an_iso_code():
    assert country_centroid("UG") == country_centroid("Uganda")


def test_country_centroid_returns_none_for_a_country_with_no_centroid():
    # France is in the power table but has no centroid, which is a miss,
    # not an error.
    assert country_centroid("France") is None


def test_country_centroid_returns_none_for_an_unknown_country():
    assert country_centroid("Wakanda") is None


# --------------------------------------------------------------------------
# device_class: exact match only
# --------------------------------------------------------------------------


def test_lookup_class_finds_a_device_by_its_exact_name():
    # Arrange
    name = "infant incubator"

    # Act
    found = lookup_class(name)

    # Assert
    assert found is not None
    assert found.needs_oxygen is True


def test_lookup_class_normalises_case_whitespace_and_a_trailing_period():
    # Arrange
    messy = "  Infant   Incubator.  "

    # Act
    found = lookup_class(messy)

    # Assert
    assert found == lookup_class("infant incubator")


def test_lookup_class_returns_none_for_a_near_miss_rather_than_guessing():
    """The rejected fuzzy matcher paired these two. A near miss must be a miss."""
    # Arrange
    assert lookup_class("basic diagnostic x-ray system") is not None

    # Act
    found = lookup_class("haemodialysis system")

    # Assert
    assert found is None


@pytest.mark.parametrize(
    "near_miss",
    [
        "infant incubators",  # plural
        "incubator",  # substring of a real name
        "infant incubator system",  # real name plus a word
        "infant-incubator",  # punctuation changed
        "neonatal incubator",  # synonym
    ],
)
def test_lookup_class_returns_none_for_anything_but_the_exact_name(near_miss):
    # Arrange
    assert lookup_class("infant incubator") is not None

    # Act / Assert
    assert lookup_class(near_miss) is None


def test_lookup_class_returns_none_for_a_blank_query():
    assert lookup_class("  ") is None


def test_lookup_class_reports_consumables_as_a_tuple():
    found = lookup_class("examination/treatment light")

    assert found is not None
    assert isinstance(found.typical_consumables, tuple)
    assert any("fuses" in item for item in found.typical_consumables)


def test_lookup_class_leaves_an_unstated_utility_unknown_rather_than_false():
    # Nothing on the laryngoscope sheet speaks to oxygen, so it stays unknown.
    found = lookup_class("laryngoscopes")

    assert found is not None
    assert found.needs_oxygen is None


def test_lookup_class_reports_water_for_a_sterilizer_that_states_it():
    found = lookup_class("autoclave hp horizontal")

    assert found is not None
    assert found.needs_water is True


def test_device_class_never_exposes_the_sources_voltage_or_temperature_columns():
    """Those columns are one country's procurement demands, not device facts."""
    # Arrange
    found = lookup_class("infant incubator")
    forbidden = ("volts", "hz", "watts", "temp_c", "humidity_pct", "voltage")

    # Act
    fields = vars(found).keys()

    # Assert
    assert not [name for name in fields if any(f in name for f in forbidden)]


def test_normalise_name_is_idempotent():
    once = normalise_name("  Phototherapy Units/Systems.  ")

    assert normalise_name(once) == once


def test_lookup_class_raises_a_named_error_when_the_table_is_missing(tmp_path):
    # Arrange
    missing = tmp_path / "gone.json"

    # Act / Assert
    with patch.object(device_class, "DATA_PATH", missing):
        with pytest.raises(DeviceClassDataError, match="missing"):
            lookup_class("infant incubator")


def test_lookup_class_raises_a_named_error_on_malformed_json(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    with patch.object(device_class, "DATA_PATH", broken):
        with pytest.raises(DeviceClassDataError, match="not valid JSON"):
            lookup_class("infant incubator")


# --------------------------------------------------------------------------
# device_registry: offline behaviour and parsing
# --------------------------------------------------------------------------


def test_lookup_model_returns_none_on_a_network_error_instead_of_raising():
    # Arrange
    failure = urllib.error.URLError("no route to host")

    # Act
    with patch(NETWORK_TARGET, side_effect=failure) as fake:
        found = lookup_model("MX450")

    # Assert
    assert found is None
    assert fake.called


def test_lookup_model_returns_none_on_a_timeout_instead_of_raising():
    with patch(NETWORK_TARGET, side_effect=TimeoutError("timed out")):
        assert lookup_model("MX450") is None


def test_lookup_model_returns_none_when_openfda_answers_404():
    # Arrange
    not_found = urllib.error.HTTPError(
        url="https://api.fda.gov/device/udi.json",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,
    )

    # Act
    with patch(NETWORK_TARGET, side_effect=not_found):
        found = lookup_model("no such model")

    # Assert
    assert found is None


def test_lookup_model_returns_none_on_unreadable_json():
    class Garbage:
        def read(self):
            return b"<html>not json</html>"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    with patch(NETWORK_TARGET, return_value=Garbage()):
        assert lookup_model("MX450") is None


def test_lookup_model_returns_none_for_a_blank_query_without_calling_the_api():
    with patch(NETWORK_TARGET) as fake:
        found = lookup_model("   ")

    assert found is None
    assert not fake.called


def test_lookup_model_flattens_a_matching_record():
    # Arrange
    payload = {
        "results": [
            {
                "brand_name": "Tis-U-Trap",
                "company_name": "Coopersurgical, Inc.",
                "version_or_model_number": "MX450",
                "commercial_distribution_status": "Not in Commercial Distribution",
                "commercial_distribution_end_date": "2025-07-25",
                "gmdn_terms": [{"code": "47341", "name": "Intrauterine curette"}],
                "storage": [
                    {
                        "type": "Storage Environment Temperature",
                        "low": {"unit": "Degrees Celsius", "value": "15"},
                        "high": {"unit": "Degrees Celsius", "value": "30"},
                    }
                ],
            }
        ]
    }

    # Act
    with patch(NETWORK_TARGET, return_value=FakeResponse(payload)):
        found = lookup_model("MX450")

    # Assert
    assert found is not None
    assert found.brand_name == "Tis-U-Trap"
    assert found.gmdn_code == "47341"
    assert found.storage_temp_low_c == 15.0
    assert found.storage_temp_high_c == 30.0


def test_lookup_model_falls_back_to_the_description_search():
    # Arrange: the model-number search finds nothing, the description does.
    empty = FakeResponse({"results": []})
    hit = FakeResponse({"results": [{"brand_name": "Found By Description"}]})

    # Act
    with patch(NETWORK_TARGET, side_effect=[empty, hit]) as fake:
        found = lookup_model("suction curette")

    # Assert
    assert found is not None
    assert found.brand_name == "Found By Description"
    assert fake.call_count == 2
    assert "device_description" in fake.call_args_list[1].args[0].full_url


def test_lookup_model_returns_none_when_both_searches_are_empty():
    empty = FakeResponse({"results": []})

    with patch(NETWORK_TARGET, side_effect=[empty, empty]):
        assert lookup_model("nothing at all") is None


def test_lookup_model_leaves_storage_unknown_when_the_record_has_none():
    # Arrange: the common case, roughly six records in seven.
    payload = {"results": [{"brand_name": "No Storage Data", "storage": None}]}

    # Act
    with patch(NETWORK_TARGET, return_value=FakeResponse(payload)):
        found = lookup_model("MX450")

    # Assert
    assert found is not None
    assert found.storage_temp_low_c is None
    assert found.storage_temp_high_c is None


def test_lookup_model_survives_a_storage_entry_with_no_bounds():
    # Arrange: free-text storage rows carry no low/high at all.
    payload = {
        "results": [
            {
                "brand_name": "Brilliant Crios",
                "storage": [
                    {
                        "type": "Special Storage Condition, Specify",
                        "special_conditions": "Do not expose to direct sunlight.",
                    }
                ],
            }
        ]
    }

    # Act
    with patch(NETWORK_TARGET, return_value=FakeResponse(payload)):
        found = lookup_model("Brilliant Crios")

    # Assert
    assert found is not None
    assert found.storage_temp_low_c is None


def test_lookup_model_converts_a_fahrenheit_storage_range_to_celsius():
    payload = {
        "results": [
            {
                "storage": [
                    {
                        "type": "Storage Environment Temperature",
                        "low": {"unit": "Degrees Fahrenheit", "value": "32"},
                        "high": {"unit": "Degrees Fahrenheit", "value": "212"},
                    }
                ]
            }
        ]
    }

    with patch(NETWORK_TARGET, return_value=FakeResponse(payload)):
        found = lookup_model("thing")

    assert found is not None
    assert found.storage_temp_low_c == 0.0
    assert found.storage_temp_high_c == 100.0


def test_lookup_model_ignores_a_malformed_results_payload():
    with patch(NETWORK_TARGET, return_value=FakeResponse({"results": "nope"})):
        assert lookup_model("MX450") is None


def test_lookup_model_sends_the_model_number_search_first():
    # Arrange
    payload = {"results": [{"brand_name": "X"}]}

    # Act
    with patch(NETWORK_TARGET, return_value=FakeResponse(payload)) as fake:
        lookup_model("MX450")

    # Assert
    url = fake.call_args.args[0].full_url
    assert "version_or_model_number" in url
    assert "MX450" in url


def test_lookup_model_uses_a_thirty_second_timeout():
    payload = {"results": [{"brand_name": "X"}]}

    with patch(NETWORK_TARGET, return_value=FakeResponse(payload)) as fake:
        lookup_model("MX450")

    assert fake.call_args.kwargs["timeout"] == 30


# --------------------------------------------------------------------------
# device_registry: support status
# --------------------------------------------------------------------------


def test_is_still_supported_is_false_when_distribution_has_ended():
    # Arrange
    record = DeviceRegistryRecord(
        commercial_distribution_status="Not in Commercial Distribution"
    )

    # Act
    supported = is_still_supported(record)

    # Assert
    assert supported is False


def test_is_still_supported_is_true_while_in_distribution():
    record = DeviceRegistryRecord(
        commercial_distribution_status="In Commercial Distribution"
    )

    assert is_still_supported(record) is True


def test_is_still_supported_is_unknown_when_the_status_is_absent():
    assert is_still_supported(DeviceRegistryRecord()) is None


def test_is_still_supported_is_unknown_for_an_unrecognised_status():
    record = DeviceRegistryRecord(commercial_distribution_status="Something Else")

    assert is_still_supported(record) is None


def test_is_still_supported_is_unknown_when_there_is_no_record_at_all():
    assert is_still_supported(None) is None


# --------------------------------------------------------------------------
# Shape of the committed tables
# --------------------------------------------------------------------------


def test_the_committed_country_table_covers_every_shipping_destination():
    # Arrange
    destinations = ("Uganda", "Haiti", "Nepal", "United States", "Ethiopia")

    # Act
    found = [lookup(name) for name in destinations]

    # Assert
    assert all(record is not None for record in found)
    assert country_power.country_count() > 200


def test_every_record_in_the_committed_country_table_is_usable():
    # Arrange / Act
    records = set(country_power._index().values())

    # Assert
    assert records
    for record in records:
        assert record.mains_voltage_v > 0
        assert record.mains_frequency_hz in (50, 60)
        assert len(record.country_code) == 2


def test_the_committed_device_class_table_loads_every_entry():
    names = device_class.known_classes()

    assert len(names) == len(set(names))
    assert "infant incubator" in [n.lower() for n in names]


def test_dataclasses_are_frozen():
    # Arrange
    record = lookup("Uganda")

    # Act / Assert
    with pytest.raises(Exception):
        record.mains_voltage_v = 110
