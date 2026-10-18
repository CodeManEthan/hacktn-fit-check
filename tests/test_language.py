"""One language vocabulary: codes, names and variants all read as English names."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.checks.documentation import check_manual_language
from app.domain.language import (
    ISO_639_1,
    language_code,
    normalise_language,
    normalise_languages,
)
from app.domain.provenance import Provenance, fact
from app.domain.result import CheckStatus
from tests.factories import device, facility, intel

# Every language value in device-docs/library-a/library-a.json,
# device-docs/library-b/library-b.json and the hero JSON block of
# reports/verify-hero-device.md, as those files gave them on 2026-10-18.
DOCUMENT_LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "nl": "Dutch", "tr": "Turkish", "pt": "Portuguese",
    "pl": "Polish", "ar": "Arabic", "el": "Greek", "da": "Danish",
    "sv": "Swedish", "fi": "Finnish", "no": "Norwegian", "cs": "Czech",
    "sk": "Slovak", "zh-Hans": "Chinese (Simplified)", "sl": "Slovenian",
    "ro": "Romanian", "ms": "Malay",
    "English": "English", "Spanish": "Spanish", "Italian": "Italian",
    "French": "French", "Dutch": "Dutch", "German": "German",
    "Simplified Chinese": "Chinese (Simplified)",
    "Traditional Chinese": "Chinese (Traditional)",
    "Norwegian": "Norwegian", "Portuguese": "Portuguese", "Russian": "Russian",
    "Finnish": "Finnish", "Swedish": "Swedish", "Czech": "Czech", "Polish": "Polish",
}

NOTES = Path("/home/jpluto/notes/jpluto/projects/hospital-surplus")


@pytest.mark.parametrize("given, name", sorted(DOCUMENT_LANGUAGES.items()))
def test_every_language_the_documents_use_resolves_to_its_name(given, name):
    assert normalise_language(given) == name
    assert language_code(name) is not None


def _languages_in_source_files() -> set[str]:
    found: set[str] = set()
    for rel in ("device-docs/library-a/library-a.json", "device-docs/library-b/library-b.json"):
        for entry in json.loads((NOTES / rel).read_text()):
            for key in ("manual_languages", "ui_languages"):
                found.update(entry.get(key) or [])
    hero = (NOTES / "reports/verify-hero-device.md").read_text()
    block = re.search(r"```json\n(.*?)```", hero, re.S).group(1)
    for match in re.finditer(r'"manual_languages":\s*(\[[^\]]*\])', block):
        found.update(json.loads(match.group(1)))
    return found


@pytest.mark.skipif(not NOTES.exists(), reason="the notes tree is on the operator's machine only")
def test_the_source_files_hold_no_language_outside_the_table():
    found = _languages_in_source_files()

    assert found <= set(DOCUMENT_LANGUAGES)
    for value in found:
        assert normalise_language(value) in set(ISO_639_1.values()) | {
            "Chinese (Simplified)", "Chinese (Traditional)"}


def test_the_table_is_the_full_iso_639_1_list():
    assert len(ISO_639_1) == 183
    assert len(set(ISO_639_1.values())) == len(ISO_639_1)
    assert all(re.fullmatch(r"[a-z]{2}", code) for code in ISO_639_1)


@pytest.mark.parametrize("given, name", [
    ("en", "English"), ("English", "English"), ("english", "English"),
    ("ENGLISH", "English"), ("fr", "French"), ("es", "Spanish"),
    ("ht", "Haitian Creole"), ("Haitian Creole", "Haitian Creole"),
    ("ne", "Nepali"), ("zh-Hans", "Chinese (Simplified)"),
    ("zh-Hant", "Chinese (Traditional)"), ("pt-BR", "Portuguese"),
    ("en_GB", "English"), ("ESPAÑOL", "Spanish"), (" french ", "French"),
    ("Kiswahili", "Swahili"), ("eng", "English"),
])
def test_codes_names_and_variants_normalise(given, name):
    assert normalise_language(given) == name


def test_an_unknown_language_is_title_cased_and_kept():
    assert normalise_language("old norse runes") == "Old Norse Runes"
    assert language_code("Old Norse Runes") is None


def test_language_code_gives_the_iso_code_for_a_name_in_any_form():
    assert language_code("English") == "en"
    assert language_code("french") == "fr"
    assert language_code("Nepali") == "ne"
    assert language_code("Haitian Creole") == "ht"
    assert language_code("Chinese (Simplified)") == "zh-CN"


def test_a_list_is_normalised_deduplicated_and_kept_in_order():
    assert normalise_languages(["en", "English", "", "fr", "FRENCH"]) == ["English", "French"]
    assert normalise_languages(None) == []


def test_the_manual_check_matches_a_code_against_a_staff_name():
    coded = device(manual_languages=fact(["en", "es", "fr"], Provenance.SPEC_SHEET))

    result = check_manual_language(coded, facility(staff_languages=("English",)), intel())

    assert result.status is CheckStatus.PASS
    assert "English" in result.detail


def test_the_manual_check_normalises_the_staff_side_too():
    named = device(manual_languages=fact(["French"], Provenance.SPEC_SHEET))

    result = check_manual_language(named, facility(staff_languages=("fr", "ht")), intel())

    assert result.status is CheckStatus.PASS


def test_the_manual_check_names_languages_not_codes_when_none_is_shared():
    coded = device(manual_languages=fact(["en"], Provenance.SPEC_SHEET))

    result = check_manual_language(coded, facility(staff_languages=("ne",)), intel())

    # No document on file, so the check asks for the manual; the names still read as names.
    assert result.status is CheckStatus.NEEDS_INFO
    assert "English" in result.detail and "Nepali" in result.detail
