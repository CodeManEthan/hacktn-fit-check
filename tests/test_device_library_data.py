"""The committed library (data/device_library.json) against the verified reports."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.domain.language import normalise_languages
from app.domain.provenance import Provenance
from app.reference import device_library as lib

REQUEST_KINDS_FILE = Path(__file__).parents[1] / "app" / "web" / "templates" / "request_form.html"
NOTES = Path("/home/jpluto/notes/jpluto/projects/hospital-surplus")


def _by_slug() -> dict[str, lib.LibraryEntry]:
    return {e.slug: e for e in lib.load_library()}


def test_twelve_entries_load_with_unique_slugs():
    entries = lib.load_library()
    assert len(entries) == 12
    assert len({e.slug for e in entries}) == 12


def test_device_types_are_in_the_request_vocabulary():
    offered = set(re.findall(r'"([a-z ]+)"', REQUEST_KINDS_FILE.read_text()))
    for e in lib.load_library():
        assert e.device_type in offered, e.slug


def test_the_525ds_carries_its_manual_figures():
    facts = lib.to_device_facts(lib.entry("drive-devilbiss-525ds"))
    assert facts.frequencies_hz.value == [60]
    assert (facts.voltage_min_v.value, facts.voltage_max_v.value) == (97, 127)
    assert (facts.temp_min_c.value, facts.temp_max_c.value) == (5, 35)
    assert facts.humidity_max_pct.value == 93
    assert "English" in facts.manual_languages.value
    assert facts.voltage_min_v.source is Provenance.SPEC_SHEET
    assert facts.voltage_min_v.note == "DeVilbiss 525DS Instruction Guide A-525DS Rev A (2022), p.11"


def test_the_525ks_carries_its_manual_figures():
    facts = lib.to_device_facts(lib.entry("drive-devilbiss-525ks"))
    assert facts.frequencies_hz.value == [50]
    assert (facts.voltage_min_v.value, facts.voltage_max_v.value) == (187, 253)
    assert "English" in facts.manual_languages.value
    assert "Chinese (Simplified)" in facts.manual_languages.value
    assert len(facts.manual_languages.value) == 21


@pytest.mark.parametrize("slug", ["drive-devilbiss-525ds", "drive-devilbiss-525ks"])
def test_both_525s_answer_the_utility_and_training_checks(slug):
    e = lib.entry(slug)
    facts = lib.to_device_facts(e)
    assert facts.needs_oxygen.value is False
    assert facts.needs_compressed_air.value is False
    assert facts.needs_distilled_water.value is False
    assert facts.operating_complexity.value == "low"
    assert "separates oxygen from room air" in e.facts["needs_oxygen"]["quote"]
    assert "bubble humidifier is an optional accessory" in facts.needs_distilled_water.note
    assert facts.operating_complexity.note.split("; ", 1)[1].startswith("lead's reading of the intended use")
    assert "nursing homes, patient care facilities" in e.facts["operating_complexity"]["quote"]
    assert facts.consumables.value == [
        "MC44D-605 intake bacteria filter", "303DZ-605 cabinet air filter",
        "PV5LD-651 final bacteria filter", "525DD-626 compressor filter",
    ]
    assert "525D-619" in e.notes  # the sieve bed is a spare part, kept in notes


def test_the_omron_is_a_120_volt_device():
    facts = lib.to_device_facts(lib.entry("omron-hem-907xl"))
    assert (facts.voltage_min_v.value, facts.voltage_max_v.value) == (120, 120)


def test_battery_devices_leave_frequency_unknown():
    for slug in ("edan-sonotrax-basic-a", "roche-accu-chek-performa"):
        facts = lib.to_device_facts(lib.entry(slug))
        assert not facts.frequencies_hz.known, slug
        assert not facts.voltage_min_v.known, slug


def test_not_stated_values_stay_unknown():
    assert not lib.to_device_facts(lib.entry("nellcor-n-560")).frequencies_hz.known
    assert not lib.to_device_facts(lib.entry("laerdal-lsu")).power_watts.known
    assert not lib.to_device_facts(lib.entry("tuttnauer-2540m")).temp_max_c.known
    # The Isolette's 5-99 % range may describe the hood, not the room.
    assert not lib.to_device_facts(lib.entry("draeger-isolette-c2000")).humidity_max_pct.known


def test_year_introduced_never_becomes_a_build_year():
    for e in lib.load_library():
        assert "year_of_manufacture" not in e.facts, e.slug
        assert not lib.to_device_facts(e).year_of_manufacture.known
    assert "2007" in lib.entry("drive-devilbiss-525ds").notes


def test_languages_are_stored_as_names():
    for e in lib.load_library():
        stored = e.facts.get("manual_languages", {}).get("value") or []
        assert stored == normalise_languages(stored), e.slug
        for doc in e.documents:
            assert doc["languages"] == normalise_languages(doc["languages"]), e.slug


def test_every_fact_points_at_a_document():
    for e in lib.load_library():
        for name, payload in e.facts.items():
            assert payload["source"] in lib.SOURCE_LABELS, (e.slug, name)
            assert payload["url"].startswith("https://"), (e.slug, name)
            if payload["document"]:
                assert lib.document_text(e.slug, payload["document"]), (e.slug, name)


def test_to_device_facts_fills_documents_with_readable_files():
    for e in lib.load_library():
        listed = lib.to_device_facts(e).documents.value
        assert listed, e.slug
        files = [d["file"] for d in listed if d["file"]]
        assert files, e.slug
        for file in files:
            assert lib.document_text(e.slug, file), (e.slug, file)


def test_only_text_files_are_copied():
    assert {p.suffix for p in lib.LIBRARY_DIR.rglob("*") if p.is_file()} == {".txt"}


def test_search_finds_both_heroes_first():
    found = [e.slug for e in lib.search("devilbiss 525")]
    assert set(found[:2]) == {"drive-devilbiss-525ds", "drive-devilbiss-525ks"}


def test_best_match_on_the_real_library():
    assert lib.best_match("DeVilbiss", "525DS").slug == "drive-devilbiss-525ds"
    assert lib.best_match("Drive", "525 KS").slug == "drive-devilbiss-525ks"
    assert lib.best_match("DeVilbiss", "525") is None  # an alias of both: a tie
    assert lib.best_match("DeVilbiss Healthcare", "") is None  # manufacturer only
    assert lib.best_match("", "Pulmo-Aide").slug == "devilbiss-pulmo-aide-5650d"


def test_citation_gives_the_page_link():
    cited = lib.citation(lib.entry("drive-devilbiss-525ks"), "voltage_min_v")
    assert cited["page"] == 11
    assert cited["url"].endswith("#page=11")
    assert cited["title"].startswith("DeVilbiss 525KS Instruction Guide SE-525KS-1")
    assert lib.citation(lib.entry("drive-devilbiss-525ks"), "year_of_manufacture")["url"] == ""


@pytest.mark.skipif(not NOTES.exists(), reason="the notes tree is not on this machine")
def test_the_committed_file_is_what_the_build_script_makes(tmp_path):
    from scripts import build_library

    build_library.build(NOTES, tmp_path / "library.json", tmp_path / "library")
    rebuilt = json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))
    assert rebuilt == json.loads(lib.LIBRARY_PATH.read_text(encoding="utf-8"))
