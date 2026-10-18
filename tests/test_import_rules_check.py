"""Design §2.5: the import and registration check, and the committed Uganda row."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.checks import check_fit
from app.checks.import_rules import LABEL_PROXY, MANUAL_QUESTION, check_import_rules
from app.config import PROJECT_ROOT
from app.domain.provenance import UNKNOWN, Provenance, fact
from app.domain.result import CheckStatus
from app.reference import import_rules
from app.web.presenters import rule_count
from tests.factories import device, facility, intel

TODAY = date(2026, 10, 18)
SPEC = Provenance.SPEC_SHEET
DOCUMENT = [{"slug": "drive-525", "file": "drive-525/manual.txt", "title": "Owner's manual",
             "url": "https://example.org/m.pdf", "languages": ["English"]}]
TABLE = {
    "Ruleland": {
        "source": {"title": "Ruleland guidelines", "url": "https://example.org/r", "date": "2024-11",
                   "pages": "29-30"},
        "label_languages": ["en"],
        "min_remaining_life_years": 2,
        "documents_required": ["no-objection letter", "packing list with serial numbers"],
        "quotes": {"label_languages": "labels in English",
                   "min_remaining_life_years": "a useful life of at least two years"},
    }
}
RULELAND = facility(country="Ruleland")


@pytest.fixture(autouse=True)
def ruleland(tmp_path, monkeypatch):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(TABLE), encoding="utf-8")
    monkeypatch.setattr(import_rules, "RULES_PATH", path)


def judge(dev, site=RULELAND):
    return check_import_rules(dev, site, intel(), today=TODAY)


def test_a_country_not_on_file_is_a_question():
    result = judge(device(), facility(country="Haiti"))

    assert result.status is CheckStatus.NEEDS_INFO
    assert result.question == ("What are Haiti's import and registration rules for donated "
                               "medical equipment?")


def test_too_little_working_life_left_fails_quoting_the_rule():
    # 15 - (2026 - 2012) = 1 year left, under the two the country asks for.
    result = judge(device(year_of_manufacture=fact(2012, Provenance.DONOR_STATED)))

    assert result.status is CheckStatus.FAIL
    assert result.requirement is None
    assert '"a useful life of at least two years"' in result.detail
    assert "Ruleland guidelines, p.29-30" in result.detail
    assert "2013 or later" in result.fix


def test_exactly_the_minimum_left_passes():
    result = judge(device(year_of_manufacture=fact(2013, Provenance.DONOR_STATED)))

    assert result.status is CheckStatus.PASS


def test_an_unknown_year_skips_the_life_rule():
    result = judge(device(year_of_manufacture=UNKNOWN))

    assert result.status is CheckStatus.PASS


def test_no_shared_label_language_fails_with_the_proxy_named():
    result = judge(device(manual_languages=fact(["ja", "ko"], SPEC)))

    assert result.status is CheckStatus.FAIL
    assert result.requirement is None
    assert LABEL_PROXY in result.detail
    assert "Japanese, Korean" in result.detail
    assert "not the label" in result.fix


def test_codes_and_names_meet_in_one_vocabulary():
    result = judge(device(manual_languages=fact(["en", "es", "fr"], SPEC)))

    assert result.status is CheckStatus.PASS
    assert f"Labels: English ({LABEL_PROXY})" in result.detail


def test_manual_languages_unknown_but_a_document_known_skips_the_label_rule():
    result = judge(device(manual_languages=UNKNOWN, documents=fact(DOCUMENT, SPEC)))

    assert result.status is CheckStatus.PASS
    assert "manufacturer's document" in result.detail


def test_manual_languages_and_documents_both_unknown_asks_the_manual_question():
    result = judge(device(manual_languages=UNKNOWN))

    assert result.status is CheckStatus.NEEDS_INFO
    assert result.question == MANUAL_QUESTION
    assert LABEL_PROXY in result.detail


def test_an_empty_document_list_is_not_a_document():
    result = judge(device(manual_languages=UNKNOWN, documents=fact([], SPEC)))

    assert result.status is CheckStatus.NEEDS_INFO


def test_a_life_failure_wins_over_an_unanswered_label():
    result = judge(device(manual_languages=UNKNOWN,
                          year_of_manufacture=fact(2005, Provenance.DONOR_STATED)))

    assert result.status is CheckStatus.FAIL
    assert "working years" in result.detail


def test_a_pass_lists_the_paperwork_and_cites_the_source():
    result = judge(device())

    assert result.status is CheckStatus.PASS
    assert result.requirement is None
    assert "Paperwork to send: no-objection letter; packing list with serial numbers." in result.detail
    assert "(Ruleland guidelines, p.29-30)" in result.detail


def test_the_manual_question_is_asked_once_when_both_checks_need_it():
    verdict = check_fit(device(manual_languages=UNKNOWN), RULELAND, intel())

    assert verdict.questions.count(MANUAL_QUESTION) == 1
    assert {r.key for r in verdict.unknowns} >= {"manual", "import"}


def test_the_check_is_registered_and_the_rule_count_is_fifteen():
    assert rule_count() == 15
    assert "import" in {r.key for r in check_fit(device(), RULELAND, intel()).results}


def test_the_committed_uganda_row_is_the_moh_guideline():
    table = json.loads((PROJECT_ROOT / "data" / "import_rules.json").read_text(encoding="utf-8"))
    uganda = table["Uganda"]

    assert uganda["label_languages"] == ["English"]
    assert uganda["min_remaining_life_years"] == 2
    assert uganda["source"]["pages"] == "29-30"
    assert uganda["source"]["url"].startswith("https://nms.go.ug/")
    assert len(uganda["documents_required"]) == 3
    assert "labels in English" in uganda["quotes"]["label_languages"]
    assert "useful life of at least two years" in uganda["quotes"]["min_remaining_life_years"]
    assert "no-objection" in uganda["quotes"]["documents_required"]
    assert set(table) == {"Uganda"}
