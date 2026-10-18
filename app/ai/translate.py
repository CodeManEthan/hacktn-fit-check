"""Translate the handover card for the clinic's staff (design §5.3, §5.4).

OCI Language's batch translation does the work, with credentials from
build_signer_and_config. If the service answers with a 4xx saying it is not
enabled or not authorised, the chat model translates instead under the same
"machine-translated, verify" label, and the translation (not the card) is
marked MODEL_KNOWLEDGE. Every translated line is re-checked: a line whose
numbers or part codes changed shows the English line in its place, flagged.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

from app.ai.client import CLIENT, GenAiClient, GenAiError, build_signer_and_config
from app.ai.packet import SECTIONS, Card, CardLine, evidence
from app.ai.packet_prompts import TRANSLATE_SYSTEM, TRANSLATE_USER
from app.config import SETTINGS, Settings
from app.domain.language import language_code, normalise_language, normalise_languages
from app.domain.provenance import Provenance

LOGGER = logging.getLogger(__name__)

ENGINE_OCI = "oci-language"
ENGINE_CHAT = "chat-model"
ENGINE_NONE = "none"

ENGLISH = "English"
SOURCE_CODE = "en"
MACHINE_FLAG = "machine-translated, verify"
ENGLISH_KEPT_NOTE = "English shown: the translation changed a number or part code"

#: Target languages OCI Language translates into, as ISO 639-1 base codes.
#: Nepali and Haitian Creole are not on it.
SUPPORTED = frozenset({
    "fr", "es", "pt", "ar", "de", "it", "ja", "ko", "zh", "nl", "pl", "ru", "tr", "el",
    "he", "sv", "da", "fi", "no", "cs", "hu", "ro", "sk", "th", "vi", "id", "ms", "hr",
    "uk", "en",
})

BATCH_DOCUMENTS = 25
NOT_ENABLED = re.compile(r"not[\s_-]*(enabled|authori[sz]ed)", re.IGNORECASE)
DECIMAL_COMMA = re.compile(r"(\d),(\d)")


@dataclass(frozen=True)
class Translation:
    """Translated lines, in the order given, and how they were made."""

    lines: tuple[str, ...]
    engine: str
    flag: str
    language: str = ""
    code: str = ""
    kept_english: tuple[int, ...] = ()
    provenance: Provenance | None = None

    @property
    def translated(self) -> bool:
        return self.engine != ENGINE_NONE


@dataclass(frozen=True)
class Target:
    """The language a clinic's card is translated into, or None for English only."""

    language: str | None
    note: str = ""


def unsupported_flag(language: str) -> str:
    return f"{language} is not supported by OCI Language; English card supplied"


def is_supported(language: str) -> bool:
    code = language_code(normalise_language(language))
    return bool(code) and code.split("-")[0].lower() in SUPPORTED


def pick_target(staff_languages: Iterable[str]) -> Target:
    """English when the staff's first language is English; else the first supported one.

    A first language OCI Language cannot translate into falls through to the
    next supported staff language that is not English; with none, the English
    card is supplied with the unsupported flag.
    """
    staff = normalise_languages(staff_languages)
    if not staff or staff[0] == ENGLISH:
        return Target(None, "English, as requested")
    first = staff[0]
    if is_supported(first):
        return Target(first)
    fallback = next((l for l in staff[1:] if l != ENGLISH and is_supported(l)), None)
    if fallback:
        return Target(fallback, f"{first} is not supported by OCI Language; "
                                f"{fallback} supplied beside English")
    return Target(None, unsupported_flag(first))


# --- the service calls ------------------------------------------------------


def _language_client(settings: Settings) -> tuple[Any, str]:
    from oci.ai_language import AIServiceLanguageClient

    config, signer = build_signer_and_config(settings)
    extra = {"signer": signer} if signer is not None else {}
    client = AIServiceLanguageClient(config, timeout=settings.timeout, **extra)
    return client, settings.compartment_id or config.get("tenancy") or ""


def _oci_translate(lines: Sequence[str], code: str, client: Any, compartment_id: str) -> list[str]:
    from oci.ai_language import models

    out: list[str] = []
    for start in range(0, len(lines), BATCH_DOCUMENTS):
        chunk = lines[start:start + BATCH_DOCUMENTS]
        details = models.BatchLanguageTranslationDetails(
            compartment_id=compartment_id,
            target_language_code=code,
            documents=[
                models.TextDocument(key=str(i), text=text, language_code=SOURCE_CODE)
                for i, text in enumerate(chunk)
            ],
        )
        result = client.batch_language_translation(details).data
        by_key = {doc.key: doc.translated_text for doc in result.documents or ()}
        if len(by_key) != len(chunk):
            errors = "; ".join(str(getattr(e, "error", e)) for e in result.errors or ())
            raise RuntimeError(f"OCI Language translated {len(by_key)} of {len(chunk)} "
                               f"lines. {errors}".strip())
        out.extend(by_key[str(i)] for i in range(len(chunk)))
    return out


def _chat_translate(lines: Sequence[str], language: str, chat: GenAiClient) -> list[str]:
    payload = chat.complete_json(
        TRANSLATE_USER.format(lines=json.dumps(list(lines), ensure_ascii=False),
                              target=language),
        system=TRANSLATE_SYSTEM.format(target=language),
    )
    translated = payload.get("lines")
    if not isinstance(translated, list) or len(translated) != len(lines):
        raise GenAiError("The chat model did not return one translation per line.")
    return [str(t) for t in translated]


def is_not_enabled(exc: BaseException) -> bool:
    """A 4xx whose code or message says the service is not enabled or not authorised."""
    status = getattr(exc, "status", None)
    if not isinstance(status, int) or not 400 <= status < 500:
        return False
    said = f"{getattr(exc, 'code', '')} {getattr(exc, 'message', '')}"
    return bool(NOT_ENABLED.search(said))


def _same_figures(english: str, translated: str) -> bool:
    before = evidence(english)
    after = evidence(DECIMAL_COMMA.sub(r"\1.\2", translated))
    return before.numbers == after.numbers and before.codes == after.codes


def translate(
    lines: Sequence[str], target_name: str, client: Any = None, *,
    chat: GenAiClient | None = None, compartment_id: str | None = None,
    settings: Settings = SETTINGS,
) -> Translation:
    """Translate English lines into `target_name`.

    `client` is an OCI Language client (built from the settings when None);
    `chat` is the fallback chat client. Never raises: a failed translation
    comes back with engine "none" and the English lines.
    """
    language = normalise_language(target_name)
    code = language_code(language) or ""
    english = tuple(lines)
    if not is_supported(language):
        return Translation(english, ENGINE_NONE, unsupported_flag(language), language, code)
    if language == ENGLISH or not english:
        return Translation(english, ENGINE_NONE, "", language, code)

    engine, provenance = ENGINE_OCI, None
    try:
        if client is None:
            client, built_compartment = _language_client(settings)
            compartment_id = compartment_id or built_compartment
        translated = _oci_translate(english, code, client,
                                    compartment_id or settings.compartment_id or "")
    except Exception as exc:  # noqa: BLE001 - every failure leaves an English card
        if not is_not_enabled(exc):
            LOGGER.warning("OCI Language translation failed: %s", exc)
            return Translation(english, ENGINE_NONE,
                               f"Translation into {language} failed; English card supplied",
                               language, code)
        LOGGER.warning("OCI Language not enabled, using the chat model: %s", exc)
        try:
            translated = _chat_translate(english, language, chat or CLIENT)
        except Exception as chat_exc:  # noqa: BLE001
            LOGGER.warning("chat-model translation failed: %s", chat_exc)
            return Translation(english, ENGINE_NONE,
                               f"Translation into {language} failed; English card supplied",
                               language, code)
        engine, provenance = ENGINE_CHAT, Provenance.MODEL_KNOWLEDGE

    kept: list[int] = []
    checked: list[str] = []
    for i, (before, after) in enumerate(zip(english, translated)):
        if _same_figures(before, after):
            checked.append(after)
        else:
            checked.append(before)
            kept.append(i)
    return Translation(tuple(checked), engine, MACHINE_FLAG, language, code,
                       tuple(kept), provenance)


# --- the card ---------------------------------------------------------------


def _card_strings(card: Card) -> list[tuple[str, int, str]]:
    """(section, line index, field) for each translatable string, in card order."""
    slots = []
    for name in SECTIONS:
        for i, line in enumerate(getattr(card, name)):
            slots.append((name, i, "text"))
            if line.interval:
                slots.append((name, i, "interval"))
    return slots


def translate_card(
    card: Card, target_name: str, client: Any = None, **kwargs: Any
) -> tuple[Card | None, Translation]:
    """The card in `target_name`, or None when no translation was made.

    A line any of whose strings came back with changed figures shows its
    English text and interval, with a note.
    """
    slots = _card_strings(card)
    strings = [getattr(getattr(card, n)[i], f) for n, i, f in slots]
    result = translate(strings, target_name, client, **kwargs)
    if not result.translated:
        return None, result

    kept_lines = {(slots[k][0], slots[k][1]) for k in result.kept_english}
    sections: dict[str, list[CardLine]] = {n: list(getattr(card, n)) for n in SECTIONS}
    for (name, i, fieldname), text in zip(slots, result.lines):
        if (name, i) not in kept_lines:
            sections[name][i] = replace(sections[name][i], **{fieldname: text})
    for name, i in kept_lines:
        sections[name][i] = replace(getattr(card, name)[i], note=ENGLISH_KEPT_NOTE)
    translated = replace(card, **{n: tuple(v) for n, v in sections.items()})
    return translated, result
