"""app/web/inspector.py: rows, the cleared-field list, and clearing."""

from __future__ import annotations

from app.ai.extract import Citation
from app.domain.device import DeviceFacts
from app.domain.provenance import UNKNOWN, Fact, Provenance
from app.web import inspector

SHEET = Provenance.SPEC_SHEET
DONOR = Provenance.DONOR_STATED


def rows_by_label(facts, citations=None):
    return {r["label"]: r for r in inspector.inspector_rows(facts, citations)}


def test_every_row_is_present_and_unknown_rows_are_unverified():
    rows = inspector.inspector_rows(DeviceFacts())
    assert [r["label"] for r in rows] == list(inspector.ROW_LABELS)
    assert all(r["source"] == "unverified" and not r["known"] and r["source_url"] == "" for r in rows)


def test_values_are_formatted_as_a_plate_prints_them():
    rows = rows_by_label(DeviceFacts(
        manufacturer=Fact("Drive DeVilbiss", SHEET), model=Fact("525DS", SHEET),
        voltage_min_v=Fact(120.0, DONOR), voltage_max_v=Fact(120, DONOR),
        frequencies_hz=Fact([50, 60], DONOR), power_watts=Fact(310.0, SHEET),
        temp_min_c=Fact(5, SHEET), humidity_max_pct=Fact(93, SHEET),
        consumables=Fact(["MC44D-605", "303DZ-605"], SHEET), manual_languages=Fact([], SHEET),
        needs_oxygen=Fact(False, SHEET), operating_complexity=Fact("low", SHEET),
    ))
    assert rows["Model"]["value"] == "Drive DeVilbiss 525DS"
    assert rows["Voltage"]["value"] == "120 V"
    assert rows["Frequency"]["value"] == "50/60 Hz"
    assert rows["Power"]["value"] == "310 W"
    assert rows["Operating temp"]["value"] == "from 5 °C"
    assert rows["Humidity"]["value"] == "up to 93 %"
    assert rows["Consumables"]["value"] == "MC44D-605, 303DZ-605"
    assert rows["Manual languages"]["value"] == "none"
    assert rows["Needs oxygen"]["value"] == "no"
    assert rows["Operating complexity"]["value"] == "low"


def test_a_cited_row_links_to_its_page_and_shows_the_source_of_what_it_shows():
    facts = DeviceFacts(manufacturer=Fact("Acme", DONOR), model=Fact("X1", SHEET, "manual, p.4"))
    rows = rows_by_label(facts, {"model": Citation("X1 manual", "https://x/m.pdf", 4)})
    assert rows["Model"]["source"] == "spec sheet"
    assert rows["Model"]["source_url"] == "https://x/m.pdf#page=4"
    assert rows["Model"]["page"] == 4 and rows["Model"]["document"] == "X1 manual"
    assert rows["Model"]["field"] == "manufacturer,model"


def test_an_unknown_row_carries_the_gate_note():
    rows = rows_by_label(DeviceFacts(power_watts=Fact(note="model answer not found in the source")))
    assert rows["Power"]["note"] == "model answer not found in the source"
    assert rows["Power"]["known"] is False


def test_parse_cleared_keeps_only_row_fields_once():
    assert inspector.parse_cleared("voltage_min_v, voltage_max_v,voltage_min_v raw_input documents x") == (
        "voltage_min_v", "voltage_max_v",
    )
    assert inspector.parse_cleared("") == ()
    assert inspector.parse_cleared(["model,manufacturer"]) == ("model", "manufacturer")


def test_clear_fields_sets_exactly_the_named_fields_unknown():
    facts = DeviceFacts(voltage_min_v=Fact(97, SHEET), frequencies_hz=Fact([60], SHEET),
                        documents=Fact([{"slug": "s"}], SHEET), raw_input="paste")
    cleared = inspector.clear_fields(facts, ["voltage_min_v", "documents", "raw_input"])
    assert cleared.voltage_min_v is UNKNOWN
    assert cleared.frequencies_hz.value == [60]
    assert cleared.documents.known and cleared.raw_input == "paste"
    assert inspector.clear_fields(facts, []) is facts
