"""[B8] §8 CSV screen: parsing the column contract and screening each row.

The library is the committed data/device_library.json. No database, no
model, no network: the supply lookup is a fake that records its calls.
"""

from __future__ import annotations

import csv
import inspect
import io

import pytest

from app.domain.device import SupplyIntel
from app.domain.provenance import Provenance
from app.domain.result import Outcome
from app.matching import csv_screen
from app.matching.supply_order import supply_lookup
from tests.factories import facility, intel

CLINIC = facility()

HEADER = ("Device Type,Manufacturer,MODEL,Quantity,year,voltage_min_v,voltage_max_v,"
          "frequencies_hz,power_watts,temp_min_c,temp_max_c,humidity_max_pct,"
          "manual_languages,expiry_date,city,country")
FIVE_ROWS = "\n".join([
    HEADER,
    # 1. a library model: the library's facts, the row's year, quantity and place
    ",DeVilbiss Healthcare,525KS,2,2019,,,,,,,,,,Leeds,United Kingdom",
    # 2. a 60 Hz device, the donor's own figures
    "suction pump,Acme,S60,1,2020,110,120,60,200,5,40,90,en;fr,,Houston,United States",
    # 3. an all-blank row
    ",,,,,,,,,,,,,,,",
    # 4. a malformed row: the quantity is not a number
    "oxygen concentrator,Acme,X1,lots,2020,220,240,50,300,5,40,90,en,,Leeds,United Kingdom",
    # 5. a plain row, dual frequency, two manual languages, something dated
    "patient monitor,Acme,M5,1,2021,100,240,50/60,80,0,40,95,en;fr,2027-03-31,Kampala,Uganda",
])


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, device, device_type, country):
        self.calls.append((device, device_type, country))
        return intel(country=country)


@pytest.fixture
def screened():
    lookup = Recorder()
    result = csv_screen.screen_csv(FIVE_ROWS.encode(), CLINIC, intel_for=lookup,
                                   donor_name="Mercy", donor_email="m@example.org")
    return result, lookup


def test_five_rows_give_five_results_in_file_order(screened):
    result, lookup = screened
    assert [r.line for r in result.rows] == [2, 3, 4, 5, 6]
    assert [r.status for r in result.rows] == ["screened", "screened", "blank", "error", "screened"]
    # Only the three readable rows were screened, each through the lookup.
    assert len(lookup.calls) == 3
    assert result.facility_slug == CLINIC.slug


def test_headers_are_case_insensitive_and_spaces_read_as_underscores(screened):
    result, _ = screened
    assert result.missing_columns == ()
    assert result.ignored_columns == ()
    assert result.rows[1].values["device_type"] == "suction pump"
    assert result.rows[1].values["model"] == "S60"


def test_a_library_model_takes_the_library_facts_and_the_row_donor_facts(screened):
    row = screened[0].rows[0]
    assert row.library_slug == "drive-devilbiss-525ks"
    device = row.offer.device
    assert device.voltage_min_v.value == 187 and device.voltage_min_v.source is Provenance.SPEC_SHEET
    assert device.frequencies_hz.value == [50]
    assert "p.11" in device.voltage_min_v.note
    assert device.year_of_manufacture.value == 2019
    assert device.year_of_manufacture.source is Provenance.DONOR_STATED
    assert row.offer.quantity_offered == 2
    assert row.offer.origin.city == "Leeds" and row.offer.origin.country == "United Kingdom"
    # The origin's coordinates come from the country, as on the offer form.
    assert screened[0].rows[4].offer.origin.located
    # A blank device_type takes the library's.
    assert row.offer.device_type == "oxygen concentrator"
    # A blank expiry is the donor's "nothing dated".
    assert device.dated_items.value is False
    assert row.offer.id is None and row.offer.donor_name == "Mercy"


def test_a_60_hz_device_wont_work_on_a_50_hz_grid(screened):
    row = screened[0].rows[1]
    device = row.offer.device
    assert device.frequencies_hz.value == [60]
    assert device.frequencies_hz.source is Provenance.DONOR_STATED
    assert device.manual_languages.value == ["English", "French"]
    assert row.verdict.outcome is Outcome.WONT_WORK
    assert any(b.startswith("Supply frequency:") for b in row.blockers)
    assert row.outcome == "won't work"


def test_an_all_blank_row_is_reported_and_not_screened(screened):
    row = screened[0].rows[2]
    assert row.status == "blank" and row.offer is None and row.verdict is None
    assert row.outcome == "blank row"


def test_a_malformed_row_is_reported_not_fatal(screened):
    row = screened[0].rows[3]
    assert row.status == "error" and row.offer is None
    assert "quantity must be a number" in row.error and "'lots'" in row.error
    # The rows after it were still screened.
    assert screened[0].rows[4].status == "screened"


def test_a_plain_row_reads_every_column(screened):
    row = screened[0].rows[4]
    device = row.offer.device
    assert device.frequencies_hz.value == [50, 60]
    assert (device.voltage_min_v.value, device.voltage_max_v.value) == (100, 240)
    assert device.power_watts.value == 80
    assert device.temp_max_c.value == 40 and device.humidity_max_pct.value == 95
    assert device.manual_languages.value == ["English", "French"]
    assert device.dated_items.value is True
    assert device.expiry_date.value.isoformat() == "2027-03-31"
    assert device.manufacturer.value == "Acme" and device.model.value == "M5"
    assert row.library_slug == ""


def test_result_rows_carry_verdict_blockers_send_with_and_questions(screened):
    for row in screened[0].screened:
        assert row.verdict is not None
        assert isinstance(row.blockers, list)
        assert isinstance(row.send_with, list)
        assert isinstance(row.questions, list)
    library_row = screened[0].rows[0]
    # The 525KS at a clinic with no stated consumables answer: the filters go with it.
    assert library_row.verdict.requirements == tuple(library_row.send_with)


def test_the_lookup_is_called_as_the_candidate_page_calls_it(screened):
    _, lookup = screened
    device, device_type, country = lookup.calls[0]
    assert device.model.value == "525KS"
    assert device_type == "oxygen concentrator"
    assert country == CLINIC.country


def test_the_default_lookup_is_supply_lookup():
    default = inspect.signature(csv_screen.screen_csv).parameters["intel_for"].default
    assert default is supply_lookup


def test_the_download_has_the_input_columns_then_the_four_results_and_notes(screened):
    text = csv_screen.to_csv(screened[0])
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0].keys()) == [*csv_screen.COLUMNS, "verdict", "blockers",
                                    "send_with", "questions", "notes"]
    assert rows[0]["notes"] == "library: DeVilbiss Healthcare 525KS"
    assert rows[1]["notes"] == ""  # a donor-stated row
    assert rows[2]["notes"] == "" and rows[3]["notes"] == ""
    assert len(rows) == 5
    assert rows[1]["frequencies_hz"] == "60"
    assert rows[1]["verdict"] == "won't work"
    assert "Supply frequency" in rows[1]["blockers"]
    assert rows[2]["verdict"] == "blank row"
    assert rows[3]["verdict"] == "error" and "quantity" in rows[3]["blockers"]


# ---- the cell readers --------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("50/60", [50, 60]), ("50;60", [50, 60]), ("60", [60]), ("50 / 60 Hz", [50, 60]),
    ("", None), ("  ", None),
])
def test_frequencies(raw, expected):
    assert csv_screen.parse_frequencies(raw) == expected


@pytest.mark.parametrize("raw", ["fifty", "50.5", "-50"])
def test_bad_frequencies_are_a_row_error(raw):
    with pytest.raises(csv_screen.FormError):
        csv_screen.parse_frequencies(raw)


@pytest.mark.parametrize("raw, expected", [
    ("en;fr", ["English", "French"]), ("English; fr", ["English", "French"]),
    ("en;EN;english", ["English"]), ("zh-Hans", ["Chinese (Simplified)"]), ("", None),
])
def test_languages_go_through_the_one_vocabulary(raw, expected):
    assert csv_screen.parse_languages(raw) == expected


def test_a_row_with_only_type_and_place_needs_info_and_reads_nothing_dated():
    text = "device_type,country\noxygen concentrator,United Kingdom\n"
    result = csv_screen.screen_csv(text, CLINIC, intel_for=lambda d, t, c: SupplyIntel(country=c))
    row = result.rows[0]
    assert row.status == "screened"
    assert row.verdict.outcome is Outcome.NEEDS_INFO
    assert row.offer.device.dated_items.value is False
    assert row.questions
    assert "manufacturer" in result.missing_columns


@pytest.mark.parametrize("line, words", [
    ("oxygen concentrator,Acme,X,1,,abc,240,50,,,,,,,,Leeds", "Voltage min (V) must be a number"),
    ("oxygen concentrator,Acme,X,1,,240,100,50,,,,,,,,Leeds", "is above voltage max"),
    ("oxygen concentrator,Acme,X,1,1890,,,,,,,,,,,Leeds", "Year must be between"),
    ("oxygen concentrator,Acme,X,1,,,,,,,,,,31/03/2027,,Leeds", "Expiry date must look like"),
    (",Acme,X,1,,,,,,,,,,,,Leeds", "device_type is needed"),
    ("oxygen concentrator,Acme,X,1,,,,,,,,,,,,", "country is needed"),
    ("oxygen concentrator,Acme,X,1,,,,,,,,,,,Leeds,UK,extra", "17 cells"),
])
def test_malformed_cells_are_row_errors(line, words):
    text = ",".join(csv_screen.COLUMNS) + "\n" + line
    result = csv_screen.screen_csv(text, CLINIC, intel_for=lambda d, t, c: intel(country=c))
    row = result.rows[0]
    assert row.status == "error", row
    assert words in row.error


def test_a_library_row_ignores_typed_specs_but_says_so():
    text = ("device_type,manufacturer,model,voltage_min_v,frequencies_hz,country\n"
            "oxygen concentrator,DeVilbiss,525 KS,110,60,United Kingdom\n")
    row = csv_screen.screen_csv(text, CLINIC, intel_for=lambda d, t, c: intel(country=c)).rows[0]
    assert row.library_slug == "drive-devilbiss-525ks"
    assert row.offer.device.voltage_min_v.value == 187
    assert row.offer.device.frequencies_hz.value == [50]
    assert row.notes == ("the library's documented voltage_min_v, frequencies_hz used, not the row's",)


def test_a_library_row_whose_cells_agree_with_the_library_has_no_note():
    text = ("manufacturer,model,voltage_min_v,voltage_max_v,frequencies_hz,manual_languages,country\n"
            "DeVilbiss,525DS,97,127,60,en;es;fr,United States\n")
    row = csv_screen.screen_csv(text, CLINIC, intel_for=lambda d, t, c: intel(country=c)).rows[0]
    assert row.library_slug == "drive-devilbiss-525ds" and row.notes == ()


def test_a_tied_model_is_not_a_library_row():
    text = "device_type,manufacturer,model,country\noxygen concentrator,DeVilbiss,525,United Kingdom\n"
    row = csv_screen.screen_csv(text, CLINIC, intel_for=lambda d, t, c: intel(country=c)).rows[0]
    assert row.library_slug == ""
    assert row.offer.device.model.source is Provenance.DONOR_STATED


@pytest.mark.parametrize("text, words", [
    ("", "empty"),
    ("name,colour\nx,y\n", "None of the header's columns"),
])
def test_an_unreadable_file_is_one_clean_error(text, words):
    with pytest.raises(csv_screen.CsvError, match=words):
        csv_screen.screen_csv(text, CLINIC)


def test_unknown_columns_are_listed_and_excel_bom_is_read():
    data = "﻿DEVICE_TYPE,Country,Notes\nsuction pump,Uganda,spare\n".encode("utf-8")
    result = csv_screen.screen_csv(data, CLINIC, intel_for=lambda d, t, c: intel(country=c))
    assert result.ignored_columns == ("notes",)
    assert result.rows[0].offer.device_type == "suction pump"


def test_too_many_rows_is_refused(monkeypatch):
    monkeypatch.setattr(csv_screen, "MAX_ROWS", 2)
    text = "device_type,country\n" + "x,Uganda\n" * 3
    with pytest.raises(csv_screen.CsvError, match="more than 2 rows"):
        csv_screen.screen_csv(text, CLINIC, intel_for=lambda d, t, c: intel(country=c))


# ---- check 1 L1: a model hit must also be the entry's maker and device ------

L1_HEADER = "device_type,manufacturer,model,quantity,year,voltage_min_v,voltage_max_v,frequencies_hz,country\n"


def _one_row(line: str):
    text = L1_HEADER + line + "\n"
    return csv_screen.screen_csv(text, CLINIC, intel_for=lambda d, t, c: intel(country=c)).rows[0]


def test_a_model_hit_from_another_maker_and_device_is_donor_stated_with_a_note():
    row = _one_row("suction pump,Medela,C2000,1,2020,220,240,50,Uganda")
    assert row.status == "screened"
    assert row.library_slug == "" and row.library_label == ""
    device = row.offer.device
    assert device.manufacturer.value == "Medela"
    assert device.model.value == "C2000" and device.model.source is Provenance.DONOR_STATED
    assert device.voltage_min_v.value == 220 and device.voltage_min_v.source is Provenance.DONOR_STATED
    assert row.offer.device_type == "suction pump"
    assert row.notes == ("resembles library model Draeger Medical Systems Isolette C2000; "
                         "row facts used",)
    assert row.name == "Medela C2000"


def test_a_right_maker_but_another_device_type_is_not_a_library_row():
    row = _one_row("suction pump,Draeger,C2000,1,,,,,Uganda")
    assert row.library_slug == ""
    assert "resembles library model Draeger Medical Systems Isolette C2000" in row.notes[0]


def test_a_shared_generic_word_is_not_the_same_maker():
    # "Medical" alone does not make Laerdal Medical the Draeger Medical Systems.
    row = _one_row(",Laerdal Medical,C2000,1,,,,,Uganda")
    assert row.library_slug == ""


@pytest.mark.parametrize("line", [
    "oxygen concentrator,DeVilbiss,525KS,1,,,,,United Kingdom",
    ",,525KS,1,,,,,United Kingdom",
    "Oxygen-Concentrator,DEVILBISS HEALTHCARE,525KS,1,,,,,United Kingdom",
    "infant incubator,Dräger,C2000,1,,,,,Uganda",
])
def test_the_right_maker_and_device_or_blanks_still_take_the_library(line):
    row = _one_row(line)
    assert row.library_slug, row
    assert not any(n.startswith("resembles") for n in row.notes)
    assert row.offer.device.voltage_min_v.source is Provenance.SPEC_SHEET


# ---- check 1 L2: the download says when the library's facts were used -------

def test_the_download_names_the_library_entry_and_the_cells_it_overrode():
    text = (L1_HEADER
            + "oxygen concentrator,DeVilbiss,525KS,2,2019,100,120,60,United Kingdom\n"
            + "suction pump,Acme,S60,1,2020,110,120,60,United States\n"
            + "suction pump,Medela,C2000,1,2020,220,240,50,Uganda\n")
    result = csv_screen.screen_csv(text, CLINIC, intel_for=lambda d, t, c: intel(country=c))
    rows = list(csv.DictReader(io.StringIO(csv_screen.to_csv(result))))
    library, donor, resembles = rows
    # The typed plate cells are echoed as typed; the notes say they were not used.
    assert library["voltage_min_v"] == "100" and library["frequencies_hz"] == "60"
    assert library["notes"] == ("library: DeVilbiss Healthcare 525KS | the library's documented "
                                "voltage_min_v, voltage_max_v, frequencies_hz used, not the row's")
    assert donor["notes"] == ""
    assert resembles["notes"] == ("resembles library model Draeger Medical Systems Isolette "
                                  "C2000; row facts used")
