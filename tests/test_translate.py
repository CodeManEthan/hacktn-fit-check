"""§5.3-§5.4: translation of the card, its fallback and its number re-check.

No service is called: the OCI Language client and the chat client are fakes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from oci.exceptions import ServiceError

from app.ai import translate as tr
from app.ai.packet import Card, CardLine
from app.domain.provenance import Provenance

FRENCH = {
    "Wipe the cabinet every 7 days.": "Essuyez le boîtier tous les 7 jours.",
    "Keep it 2 m (6.5 feet) from flames.": "Gardez-le à 2 m (6,5 pieds) des flammes.",
    "Allow up to 15 minutes for start up.": "Prévoyez jusqu'à 50 minutes pour le démarrage.",
    "7 days": "7 jours",
}


class FakeLanguage:
    """Answers batch_language_translation from a phrase table, or raises."""

    def __init__(self, table=None, error=None):
        self.table = table or FRENCH
        self.error = error
        self.calls = []

    def batch_language_translation(self, details):
        self.calls.append(details)
        if self.error:
            raise self.error
        docs = [SimpleNamespace(key=d.key, translated_text=self.table[d.text])
                for d in details.documents]
        return SimpleNamespace(data=SimpleNamespace(documents=docs, errors=[]))


class FakeChat:
    def __init__(self, lines=None):
        self.lines = lines
        self.prompts = []

    def complete_json(self, prompt, **kwargs):
        self.prompts.append((prompt, kwargs))
        return {"lines": self.lines}


def not_enabled():
    return ServiceError(404, "NotAuthorizedOrNotFound", {},
                        "Authorization failed or requested resource not found.")


def test_oci_language_translates_with_the_target_code_and_the_label():
    client = FakeLanguage()

    result = tr.translate(["Wipe the cabinet every 7 days."], "fr", client,
                          compartment_id="ocid1.compartment.test")

    details = client.calls[0]
    assert details.target_language_code == "fr"
    assert details.compartment_id == "ocid1.compartment.test"
    assert details.documents[0].language_code == "en"
    assert result.engine == tr.ENGINE_OCI
    assert result.flag == "machine-translated, verify"
    assert result.lines == ("Essuyez le boîtier tous les 7 jours.",)
    assert result.provenance is None


def test_a_decimal_comma_is_the_same_number_but_a_changed_number_keeps_the_english():
    lines = ["Keep it 2 m (6.5 feet) from flames.", "Allow up to 15 minutes for start up."]

    result = tr.translate(lines, "French", FakeLanguage(), compartment_id="c")

    assert result.lines[0] == "Gardez-le à 2 m (6,5 pieds) des flammes."
    assert result.lines[1] == lines[1]
    assert result.kept_english == (1,)


@pytest.mark.parametrize("language", ["Nepali", "ne", "Haitian Creole"])
def test_an_unsupported_target_makes_no_call_and_says_so(language):
    client = FakeLanguage()

    result = tr.translate(["Wipe the cabinet every 7 days."], language, client)

    assert client.calls == []
    assert result.engine == tr.ENGINE_NONE
    assert result.flag.endswith("is not supported by OCI Language; English card supplied")
    assert result.lines == ("Wipe the cabinet every 7 days.",)


def test_a_not_enabled_4xx_falls_back_to_the_chat_model_marked_model_knowledge():
    chat = FakeChat(["Essuyez le boîtier tous les 7 jours."])

    result = tr.translate(["Wipe the cabinet every 7 days."], "French",
                          FakeLanguage(error=not_enabled()), chat=chat, compartment_id="c")

    assert result.engine == tr.ENGINE_CHAT
    assert result.flag == "machine-translated, verify"
    assert result.provenance is Provenance.MODEL_KNOWLEDGE
    assert "French" in chat.prompts[0][1]["system"]


@pytest.mark.parametrize("error", [
    ServiceError(500, "InternalServerError", {}, "Service is not enabled right now"),
    ServiceError(400, "InvalidParameter", {}, "Text too long"),
    RuntimeError("connection reset"),
])
def test_any_other_failure_supplies_the_english_card_without_the_chat_model(error):
    chat = FakeChat(["never used"])

    result = tr.translate(["Wipe the cabinet every 7 days."], "French",
                          FakeLanguage(error=error), chat=chat, compartment_id="c")

    assert chat.prompts == []
    assert result.engine == tr.ENGINE_NONE
    assert result.flag == "Translation into French failed; English card supplied"


def test_a_chat_reply_with_the_wrong_line_count_is_a_failure():
    result = tr.translate(["a 7", "b 8"], "French", FakeLanguage(error=not_enabled()),
                          chat=FakeChat(["un 7"]), compartment_id="c")

    assert result.engine == tr.ENGINE_NONE


def test_not_enabled_reads_the_status_and_the_words():
    assert tr.is_not_enabled(not_enabled())
    assert tr.is_not_enabled(ServiceError(403, "Forbidden", {}, "Language is not enabled"))
    assert not tr.is_not_enabled(ServiceError(503, "x", {}, "not enabled"))
    assert not tr.is_not_enabled(RuntimeError("not authorized"))


def test_pick_target_per_clinic():
    assert tr.pick_target(["English"]) == tr.Target(None, "English, as requested")
    assert tr.pick_target(["French", "Haitian Creole"]).language == "French"
    haiti = tr.pick_target(["Haitian Creole", "fr"])
    assert haiti.language == "French" and "Haitian Creole is not supported" in haiti.note
    assert tr.pick_target(["Nepali", "English"]) == tr.Target(
        None, "Nepali is not supported by OCI Language; English card supplied")


def test_supported_codes_cover_chinese_scripts_and_exclude_nepali_and_creole():
    assert tr.is_supported("Chinese (Simplified)") and tr.is_supported("zh-TW")
    assert not tr.is_supported("Nepali") and not tr.is_supported("Haitian Creole")


def test_translate_card_rebuilds_the_card_and_notes_a_kept_line():
    card = Card(
        label="525KS",
        care_schedule=(CardLine("Wipe the cabinet every 7 days.", 9, interval="7 days"),),
        quick_start=(CardLine("Allow up to 15 minutes for start up.", 8),),
    )

    translated, result = tr.translate_card(card, "French", FakeLanguage(), compartment_id="c")

    assert translated.care_schedule[0].text == "Essuyez le boîtier tous les 7 jours."
    assert translated.care_schedule[0].interval == "7 jours"
    assert translated.care_schedule[0].page == 9
    assert translated.quick_start[0].text == "Allow up to 15 minutes for start up."
    assert translated.quick_start[0].note == tr.ENGLISH_KEPT_NOTE
    assert result.engine == tr.ENGINE_OCI


def test_translate_card_into_an_unsupported_language_gives_no_card():
    translated, result = tr.translate_card(
        Card(warnings=(CardLine("Do not smoke.", 8),)), "Nepali", FakeLanguage())

    assert translated is None and result.engine == tr.ENGINE_NONE
