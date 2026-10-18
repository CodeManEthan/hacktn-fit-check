"""Library and import-rule access, against fixture files and against no files."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.provenance import Provenance
from app.reference import device_library as lib
from app.reference import import_rules as rules

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def library(monkeypatch):
    monkeypatch.setattr(lib, "LIBRARY_PATH", FIXTURES / "device_library.json")
    monkeypatch.setattr(lib, "LIBRARY_DIR", FIXTURES / "library")


@pytest.fixture
def no_library(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, "LIBRARY_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(lib, "LIBRARY_DIR", tmp_path / "absent")


def test_with_no_library_file_everything_is_empty(no_library):
    assert lib.load_library() == ()
    assert lib.entry("drive-525ks") is None
    assert lib.search("525KS") == []
    assert lib.best_match("Drive DeVilbiss", "525KS") is None
    assert lib.document_text("drive-525ks", "drive-525ks/manual.txt") == ""


def test_the_library_loads_its_entries(library):
    entries = lib.load_library()

    assert [e.slug for e in entries] == ["drive-525ks", "drive-525ds"]
    assert lib.entry("drive-525ds").aliases == ("DeVilbiss 525", "525DS-Q")


def test_document_text_reads_by_full_path_or_bare_name(library):
    full = lib.document_text("drive-525ks", "drive-525ks/manual.txt")

    assert "230 VAC" in full
    assert lib.document_text("drive-525ks", "manual.txt") == full
    assert lib.document_text("drive-525ks", "../../device_library.json") == ""


def test_search_matches_tokens_and_ranks_the_model_hit_first(library):
    assert [e.slug for e in lib.search("devilbiss 525ks")] == ["drive-525ks", "drive-525ds"]
    assert [e.slug for e in lib.search("525DS")] == ["drive-525ds"]
    assert lib.search("ventilator") == []


@pytest.mark.parametrize("manufacturer, model, slug", [
    ("Drive DeVilbiss", "525KS", "drive-525ks"),
    (None, "525ks", "drive-525ks"),
    ("Anyone", "525DS-Q", "drive-525ds"),
    ("Drive DeVilbiss", "525 DS", "drive-525ds"),
    ("Drive DeVilbiss", "525-DS", "drive-525ds"),
    (None, "525 ks", "drive-525ks"),
    ("Drive DeVilbiss", "DeVilbiss 525", None),
    ("Drive DeVilbiss", None, None),
    ("Drive DeVilbiss", "999", None),
    (None, None, None),
])
def test_best_match_needs_one_top_model_or_alias_hit(library, manufacturer, model, slug):
    hit = lib.best_match(manufacturer, model)

    assert (hit.slug if hit else None) == slug


def test_to_device_facts_cites_the_document_and_page(library):
    facts = lib.to_device_facts(lib.entry("drive-525ks"))

    assert facts.voltage_min_v.value == 230
    assert facts.voltage_min_v.source is Provenance.SPEC_SHEET
    assert facts.voltage_min_v.note == "525KS owner's manual, p.32"
    assert facts.frequencies_hz.value == [50]


def test_to_device_facts_maps_gudid_to_registry_and_normalises_languages(library):
    facts = lib.to_device_facts(lib.entry("drive-525ks"))

    assert facts.model.source is Provenance.REGISTRY
    assert facts.model.note == "GUDID record 525KS"
    assert facts.manual_languages.value == ["English", "Spanish", "French"]
    assert facts.manufacturer.value == "Drive DeVilbiss"


def test_to_device_facts_leaves_not_stated_and_empty_frequencies_unknown(library):
    ks = lib.to_device_facts(lib.entry("drive-525ks"))
    ds = lib.to_device_facts(lib.entry("drive-525ds"))

    assert not ks.humidity_max_pct.known
    assert not ks.year_of_manufacture.known
    assert not ds.frequencies_hz.known
    assert not ds.documents.known


def test_to_device_facts_fills_documents(library):
    documents = lib.to_device_facts(lib.entry("drive-525ks")).documents

    assert documents.source is Provenance.SPEC_SHEET
    assert documents.value[0] == {
        "slug": "drive-525ks", "file": "drive-525ks/manual.txt",
        "title": "525KS owner's manual", "url": "https://example.org/525ks.pdf",
        "kind": "manual",  # [B6] §4.4's key list gains kind (B5 check L2)
        "languages": ["English", "Spanish", "French"],
    }


def test_import_rules_are_empty_when_the_file_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(rules, "RULES_PATH", tmp_path / "absent.json")

    assert rules.load_import_rules() == {}
    assert rules.rule_for("Uganda") is None
    assert rules.documents_required("Uganda") == []


def test_import_rules_read_a_country_and_normalise_its_label_languages(monkeypatch):
    monkeypatch.setattr(rules, "RULES_PATH", FIXTURES / "import_rules.json")

    row = rules.rule_for(" testland ")

    assert row["label_languages"] == ["English", "French"]
    assert row["min_remaining_life_years"] == 2
    assert rules.documents_required("Testland") == [
        "no-objection letter", "packing list with serial numbers"]
    assert rules.rule_for("Nepal") is None
    assert rules.documents_required("Nepal") == []

