"""§5.1: the manual check never fails; a packet is built when no language is shared."""

from __future__ import annotations

from app.checks.documentation import check_manual_language
from app.domain.provenance import UNKNOWN, Provenance, fact
from app.domain.requirement import RequirementKind
from app.domain.result import CheckStatus
from tests.factories import device, facility, intel

DOC = {"slug": "drive-525ks", "file": "drive-525ks/manual.txt",
       "title": "525KS instruction guide", "url": "https://example.org/525ks.pdf",
       "languages": ["English"]}


def test_a_shared_language_passes():
    result = check_manual_language(
        device(manual_languages=fact(["en", "fr"], Provenance.SPEC_SHEET)),
        facility(staff_languages=("French", "Haitian Creole")), intel())

    assert result.status is CheckStatus.PASS
    assert result.requirement is None


def test_no_shared_language_is_a_handover_packet_not_a_failure():
    result = check_manual_language(
        device(manual_languages=fact(["en"], Provenance.SPEC_SHEET),
               documents=fact([DOC], Provenance.SPEC_SHEET)),
        facility(staff_languages=("fr",)), intel())

    assert result.status is CheckStatus.MITIGABLE
    assert result.requirement.kind is RequirementKind.MANUAL
    assert result.requirement.kind.value == "handover packet"
    assert result.detail == (
        "The manual is in English; staff read French. Fit-Check generates a handover "
        "packet in French from the manufacturer's manual.")


def test_unknown_languages_with_a_document_on_file_still_build_the_packet():
    result = check_manual_language(
        device(manual_languages=UNKNOWN,
               documents=fact([DOC], Provenance.SPEC_SHEET)),
        facility(staff_languages=("Nepali",)), intel())

    assert result.status is CheckStatus.MITIGABLE
    assert result.requirement.kind is RequirementKind.MANUAL
    assert "525KS instruction guide" in result.detail
    assert result.source is Provenance.SPEC_SHEET


def test_no_document_and_no_language_asks():
    result = check_manual_language(
        device(manual_languages=UNKNOWN), facility(), intel())

    assert result.status is CheckStatus.NEEDS_INFO
    assert "No manual could be found" in result.detail
    assert result.question


def test_an_empty_document_list_counts_as_no_document():
    result = check_manual_language(
        device(manual_languages=UNKNOWN, documents=fact([], Provenance.SPEC_SHEET)),
        facility(), intel())

    assert result.status is CheckStatus.NEEDS_INFO


def test_no_shared_language_and_no_document_asks_for_the_manual():
    """[F9]: needs info only when no manual can be found. Different languages
    with nothing on file is that case: the packet has no source yet."""
    result = check_manual_language(
        device(manual_languages=fact(["en"], Provenance.SPEC_SHEET)),
        facility(staff_languages=("fr",)), intel())

    assert result.status is CheckStatus.NEEDS_INFO
    assert result.requirement is None
    assert "manual" in result.question.lower()
