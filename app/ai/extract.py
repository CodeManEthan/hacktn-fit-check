"""Turn whatever the donor gave us into DeviceFacts that point at a source.

Retrieval first (design §6):

1. Name it. The model reads the paste or the plate photo and returns the
   manufacturer, model, kind of device and a transcription of the plate.
2. Library hit. When the library holds exactly one entry for that model
   (device_library.best_match), the facts are that entry's verified,
   page-cited values and the model reads nothing more.
3. No hit. The model fills the schema from the paste and the plate text only,
   and `gate_against_text` keeps a value only when it is printed in the source
   the model points at. "Model knowledge" is not a source any more.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Any

from app.ai import fetch
from app.ai.client import CLIENT, GenAiClient
from app.ai.fetch import Document
from app.ai.prompts import (
    NAMING_SYSTEM,
    NAMING_USER,
    PLATE_RULE_IMAGE,
    PLATE_RULE_TEXT,
    READ_SCHEMA,
    READ_SOURCE_NAMES,
    READ_SYSTEM,
    READ_USER,
)
from app.config import SETTINGS
from app.domain.device import DeviceFacts
from app.domain.language import CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL, ISO_639_1, normalise_language
from app.domain.provenance import UNKNOWN, Fact, Provenance, fact
from app.reference import device_library
from app.reference.device_library import LibraryEntry

MAX_IMAGE_BYTES = 6 * 1024 * 1024
# The most document text the read step is shown, so the prompt stays inside
# the model's window with room for its reasoning.
MAX_DOCUMENT_CHARS = 40_000
# The vision model's own ceiling (4096, below the 6000 default) is applied by
# the client for every call to it; see client.MODEL_MAX_TOKENS.

NOT_FOUND = "model answer not found in the source"
NOT_USABLE = "model answer not usable"

# What the read step may name as a source, and the provenance each becomes.
SOURCE_BY_NAME = {
    "donor": Provenance.DONOR_STATED,
    "nameplate": Provenance.NAMEPLATE,
    "document": Provenance.SPEC_SHEET,
}
# The order the gate looks in after the source the model claimed.
SOURCE_ORDER = (Provenance.DONOR_STATED, Provenance.NAMEPLATE, Provenance.SPEC_SHEET)

FIELDS = (
    "manufacturer",
    "model",
    "year_of_manufacture",
    "voltage_min_v",
    "voltage_max_v",
    "frequencies_hz",
    "power_watts",
    "needs_stable_power",
    "temp_min_c",
    "temp_max_c",
    "humidity_max_pct",
    "needs_oxygen",
    "needs_distilled_water",
    "needs_compressed_air",
    "consumables",
    "manual_languages",
    "operating_complexity",
)

# How the gate checks each field against the text.
NUMBER_FIELDS = frozenset({
    "year_of_manufacture", "voltage_min_v", "voltage_max_v", "power_watts",
    "temp_min_c", "temp_max_c", "humidity_max_pct",
})
NAME_FIELDS = frozenset({"manufacturer", "model"})
# Booleans and the complexity grade cannot be found as a value; the model's
# quote must be in the source instead.
QUOTED_FIELDS = frozenset({
    "needs_stable_power", "needs_oxygen", "needs_distilled_water",
    "needs_compressed_air", "operating_complexity",
})
# A list whose unfound items are dropped: claiming fewer frequencies or
# fewer manual languages only makes a check stricter.
SUBSET_LIST_FIELDS = frozenset({"frequencies_hz", "manual_languages"})
# A list that stands or falls whole: dropping one consumable would hide a
# supply the clinic has to find.
WHOLE_LIST_FIELDS = frozenset({"consumables"})
GATED_FIELDS = NUMBER_FIELDS | NAME_FIELDS | QUOTED_FIELDS | SUBSET_LIST_FIELDS | WHOLE_LIST_FIELDS

COMPLEXITY = ("low", "medium", "high")
MIN_QUOTE_KEY = 6


@dataclass(frozen=True)
class Naming:
    """Step 1: what the donor's material names. None where it shows nothing."""

    manufacturer: str | None = None
    model: str | None = None
    device_type: str | None = None
    plate_text: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "manufacturer": self.manufacturer, "model": self.model,
            "device_type": self.device_type, "plate_text": self.plate_text,
        }


@dataclass(frozen=True)
class Citation:
    """Where one fact can be checked: a document title, its link, and a page."""

    title: str
    url: str = ""
    page: int | None = None

    @property
    def link(self) -> str:
        """The URL, pointed at the page when it is a PDF without an anchor."""
        if self.url and self.page and "#" not in self.url and ".pdf" in self.url.lower():
            return f"{self.url}#page={self.page}"
        return self.url

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.link, "page": self.page}


@dataclass(frozen=True)
class Extraction:
    """The facts, the document they were read from, and the naming that found it."""

    facts: DeviceFacts
    document: Document | None
    naming: Naming
    library_slug: str = ""
    device_type: str = ""
    citations: dict[str, Citation] = field(default_factory=dict)

    @property
    def library_hit(self) -> bool:
        return bool(self.library_slug)

    def stored_document(self) -> dict[str, Any]:
        """What the extraction cache keeps beside the facts."""
        return {
            "document": self.document.to_dict() if self.document else None,
            "library_slug": self.library_slug,
            "device_type": self.device_type,
            "naming": self.naming.to_dict(),
            "citations": {k: c.to_dict() for k, c in self.citations.items()},
        }


def image_to_data_url(raw: bytes, content_type: str) -> str:
    """Encode an uploaded photo for the vision model, refusing oversized files."""
    if not raw:
        raise ValueError("The uploaded image was empty.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"That image is {len(raw) // 1024 // 1024} MB. Keep it under "
            f"{MAX_IMAGE_BYTES // 1024 // 1024} MB."
        )
    media_type = content_type if content_type.startswith("image/") else "image/jpeg"
    return f"data:{media_type};base64,{base64.b64encode(raw).decode('ascii')}"


# ---- the pipeline -----------------------------------------------------------

def extract_device(
    *,
    typed_text: str = "",
    image_data_url: str = "",
    client: GenAiClient = CLIENT,
) -> DeviceFacts:
    """The facts alone. Raises GenAiError when the model cannot answer."""
    return extract(typed_text=typed_text, image_data_url=image_data_url, client=client).facts


def extract(
    *,
    typed_text: str = "",
    image_data_url: str = "",
    client: GenAiClient = CLIENT,
) -> Extraction:
    """Name the device, take the library's facts on a hit, else read and gate."""
    source_text = typed_text.strip()
    if not source_text and not image_data_url:
        raise ValueError("Give a device name, some spec text, or a nameplate photo.")

    naming = name_device(source_text, image_data_url, client)
    entry = device_library.best_match(naming.manufacturer, naming.model)
    if entry is not None:
        return Extraction(
            facts=replace(device_library.to_device_facts(entry), raw_input=source_text),
            document=None,
            naming=naming,
            library_slug=entry.slug,
            device_type=entry.device_type or naming.device_type or "",
            citations=library_citations(entry),
        )

    document = _live_document(naming)
    facts = read_facts(naming, source_text, client, document=document)
    texts: dict[Provenance, str | Document] = {
        Provenance.DONOR_STATED: source_text,
        Provenance.NAMEPLATE: naming.plate_text or "",
    }
    if document is not None:
        texts[Provenance.SPEC_SHEET] = document
    gated = replace(gate_against_text(facts, texts), raw_input=source_text)
    citations: dict[str, Citation] = {}
    if document is not None:
        gated = replace(gated, documents=fact(
            [{"slug": "", "file": "", "title": document.title, "url": document.url,
              "languages": []}],
            Provenance.SPEC_SHEET,
        ))
        citations = _document_citations(gated, document)
    return Extraction(facts=gated, document=document, naming=naming,
                      device_type=naming.device_type or "", citations=citations)


def name_device(typed_text: str, image_data_url: str, client: GenAiClient) -> Naming:
    """Step 1: the manufacturer, model, kind and plate transcription. Vision with an image."""
    prompt = NAMING_USER.format(
        plate_rule=PLATE_RULE_IMAGE if image_data_url else PLATE_RULE_TEXT,
        source=typed_text.strip() or "(no text supplied - read the image)",
    )
    options: dict[str, Any] = {}
    if image_data_url:
        options = {"model": SETTINGS.vision_model, "image_data_url": image_data_url}
    payload = client.complete_json(prompt, system=NAMING_SYSTEM, **options)
    return Naming(
        manufacturer=_text_or_none(payload.get("manufacturer")),
        model=_text_or_none(payload.get("model")),
        device_type=_text_or_none(payload.get("device_type")),
        plate_text=_text_or_none(payload.get("plate_text")) if image_data_url else None,
    )


def read_facts(
    naming: Naming,
    typed_text: str,
    client: GenAiClient,
    *,
    document: Document | None = None,
) -> DeviceFacts:
    """Step 2b: the schema filled from the paste, the plate text and a fetched document.

    Each fact carries the source the model named and, in `note`, the words it
    quoted; `gate_against_text` decides what stays.
    """
    names = ["donor"] + (["nameplate"] if naming.plate_text else []) + (["document"] if document else [])
    schema = READ_SCHEMA.replace(
        "{source_names}", "|".join(f'"{n}"' for n in [*names, "unknown"])
    )
    source_lines = "\n".join(f'  "{n}" = {READ_SOURCE_NAMES[n]},' for n in names)
    blocks = [f"DONOR TEXT:\n<<<\n{typed_text.strip() or '(none)'}\n>>>"]
    if naming.plate_text:
        blocks.append(f"RATING PLATE TRANSCRIPTION:\n<<<\n{naming.plate_text}\n>>>")
    if document is not None:
        blocks.append(
            f"MANUFACTURER DOCUMENT ({document.title or document.url}):\n<<<\n"
            f"{document.text[:MAX_DOCUMENT_CHARS]}\n>>>"
        )
    device = " ".join(p for p in (naming.manufacturer, naming.model) if p) or "unnamed"
    if naming.device_type:
        device = f"{device} ({naming.device_type})"
    prompt = READ_USER.format(device=device, schema=schema, source_lines=source_lines,
                              sources="\n\n".join(blocks))
    payload = client.complete_json(prompt, system=READ_SYSTEM)
    return _facts_from_payload(payload, allowed=frozenset(names))


def _facts_from_payload(payload: dict[str, Any], *, allowed: frozenset[str]) -> DeviceFacts:
    sources = payload.get("sources") or {}
    quotes = payload.get("quotes") or {}
    if not isinstance(sources, dict):
        sources = {}
    if not isinstance(quotes, dict):
        quotes = {}
    values: dict[str, Fact] = {}
    for name in FIELDS:
        raw = payload.get(name)
        if raw is None:
            continue
        source_name = str(sources.get(name) or "unknown").strip().lower()
        if source_name not in allowed:
            values[name] = Fact(note=NOT_FOUND)
            continue
        value = _coerce(name, raw)
        if value is None:
            values[name] = Fact(note=NOT_USABLE)
            continue
        quote = quotes.get(name)
        values[name] = Fact(value=value, source=SOURCE_BY_NAME[source_name],
                            note=str(quote).strip() if isinstance(quote, str) else "")
    return DeviceFacts(**values)


def _coerce(name: str, raw: Any) -> Any:
    """The model's value in the type the field holds, or None when it is not one."""
    if name in NUMBER_FIELDS:
        number = _number(raw)
        if number is None:
            return None
        return int(number) if name == "year_of_manufacture" or number.is_integer() else number
    if name == "frequencies_hz":
        if isinstance(raw, str):
            raw = _numbers_in(raw)
        elif not isinstance(raw, list):
            raw = [raw]
        numbers = [_number(v) for v in raw]
        if not numbers and raw:
            return None
        if any(n is None for n in numbers):
            return None
        return [int(n) if n.is_integer() else n for n in numbers]
    if name in ("consumables", "manual_languages"):
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return None
        return [str(v).strip() for v in raw if str(v).strip()]
    if name == "operating_complexity":
        text = str(raw).strip().lower()
        return text if text in COMPLEXITY else None
    if name in QUOTED_FIELDS:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        return {"true": True, "yes": True, "false": False, "no": False}.get(text)
    text = str(raw).strip()
    return text or None


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    found = _numbers_in(str(raw))
    return found[0] if len(found) == 1 else None


def _text_or_none(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return None if not text or text.lower() in ("null", "none", "unknown", "n/a") else text


def _live_document(naming: Naming) -> Document | None:
    """A non-library document for the named model, only when a live search is set up."""
    if not fetch.live_search_configured():
        return None
    query = " ".join(p for p in (naming.manufacturer, naming.model) if p)
    if not query:
        return None
    try:
        hits = [h for h in fetch.search(query) if h.source != fetch.LIBRARY]
        return fetch.fetch(hits[0]) if hits else None
    except (fetch.NotConfigured, fetch.FetchError):
        return None


# ---- citations --------------------------------------------------------------

def library_citations(entry: LibraryEntry) -> dict[str, Citation]:
    """For each fact the entry states, the document it was read from and the page."""
    documents = {str(d.get("file") or ""): d for d in entry.documents}
    citations: dict[str, Citation] = {}
    for name, payload in entry.facts.items():
        if not isinstance(payload, dict):
            continue
        doc = documents.get(str(payload.get("document") or ""))
        if doc is None:
            continue
        page = payload.get("page")
        # The fact's own URL already points at its page (#page=N); the
        # document's URL is the fallback for a fact stored without one.
        citations[name] = Citation(
            title=str(doc.get("title") or doc.get("file") or ""),
            url=str(payload.get("url") or doc.get("url") or ""),
            page=int(page) if isinstance(page, int) or str(page or "").isdigit() else None,
        )
    return citations


def _document_citations(facts: DeviceFacts, document: Document) -> dict[str, Citation]:
    citations = {}
    for name in FIELDS:
        current = getattr(facts, name)
        if current.known and current.source is Provenance.SPEC_SHEET:
            page = _page_from_note(current.note)
            citations[name] = Citation(title=document.title, url=document.url, page=page)
    return citations


def _page_from_note(note: str) -> int | None:
    match = re.search(r", p\.(\d+)$", note or "")
    return int(match.group(1)) if match else None


# ---- the gate ---------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\d.,])(-?)(\d+)(?:([.,])(\d+))?")
_DASHES = str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-"})
_NOT_ALNUM = re.compile(r"[\W_]+", re.UNICODE)
_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)?", re.UNICODE)
# Two-letter codes are read only as part of a list ("en, es, fr", "EN/FR"):
# alone, "it", "no" and "de" are ordinary words.
_CODE_LIST = re.compile(
    r"(?<![^\W\d_])[a-z]{2}(?:-[a-z]{2,4})?(?:\s*[,/;|]\s*[a-z]{2}(?:-[a-z]{2,4})?)+(?![^\W\d_])",
    re.IGNORECASE,
)
_KNOWN_LANGUAGES = frozenset(ISO_639_1.values()) | {CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL}


def _numbers_in(text: str) -> list[float]:
    """Every number printed in `text`, in the forms a spec prints them.

    "115-120" is 115 and 120 (a dash after a digit is a range, not a sign);
    "1,55" reads both as 1.55 and as 1 and 55; "1,200" also as 1200.
    """
    found: list[float] = []
    for sign, whole, sep, frac in _NUMBER.findall(text.translate(_DASHES)):
        negative = -1.0 if sign else 1.0
        if not sep:
            found.append(negative * float(whole))
            continue
        found.append(negative * float(f"{whole}.{frac}"))
        if sep == ",":
            found.extend((negative * float(whole), float(frac)))
            if len(frac) == 3:
                found.append(negative * float(whole + frac))
    return found


def _key(text: str) -> str:
    """Text with case, spacing and punctuation removed, so "525 DS" is "525ds"."""
    return _NOT_ALNUM.sub("", (text or "").translate(_DASHES).casefold())


@dataclass(frozen=True)
class _Source:
    provenance: Provenance
    text: str
    document: Document | None = None

    @cached_property
    def key(self) -> str:
        return _key(self.text)

    @cached_property
    def numbers(self) -> frozenset[float]:
        return frozenset(_numbers_in(self.text))

    @cached_property
    def languages(self) -> frozenset[str]:
        """Every language the text names in a form normalise_language knows."""
        names = set()
        for word in _WORD.findall(self.text):
            name = normalise_language(word)
            # A three-letter word counts only as the name itself ("Ido"), not
            # as an ISO 639-2 code, which would read "per" as Persian.
            if name in _KNOWN_LANGUAGES and (len(word) > 3 or word.title() == name):
                names.add(name)
        for run in _CODE_LIST.findall(self.text):
            for code in re.split(r"\s*[,/;|]\s*", run):
                name = normalise_language(code)
                if name in _KNOWN_LANGUAGES:
                    names.add(name)
        return frozenset(names)

    def has_text(self, wanted: str, *, minimum: int = 1) -> bool:
        wanted_key = _key(wanted)
        return len(wanted_key) >= minimum and wanted_key in self.key

    def note_for(self, quote: str) -> str:
        if self.document is None:
            return quote
        page = self.document.page_of(quote) if quote else None
        title = self.document.title or self.document.url
        return f"{title}, p.{page}" if page else title


def _item_found(name: str, item: Any, quote: str, source: _Source) -> bool:
    if name in NUMBER_FIELDS or name == "frequencies_hz":
        return any(abs(float(item) - n) < 1e-9 for n in source.numbers)
    if name == "manual_languages":
        return normalise_language(str(item)) in source.languages or source.has_text(str(item), minimum=3)
    if name in QUOTED_FIELDS:
        return source.has_text(quote, minimum=MIN_QUOTE_KEY)
    return source.has_text(str(item), minimum=2)


def gate_against_text(
    facts: DeviceFacts, texts: Mapping[Provenance, str | Document]
) -> DeviceFacts:
    """Keep a value only where it is printed in the source text (design §6.2).

    Numbers must occur as printed numbers ("115" in "115V", "115-120"; "35"
    as "35.0"); names and list strings must occur as words; a boolean or the
    complexity grade needs the model's quote to occur. The claimed source is
    searched first, then the others, and a kept value takes the provenance of
    the text it was found in: NAMEPLATE for the plate transcription,
    DONOR_STATED for the paste, SPEC_SHEET (note "<title>, p.<page>") for a
    fetched document. A value found nowhere is UNKNOWN with
    note "model answer not found in the source".
    """
    sources = {
        provenance: _Source(provenance, value.text, value) if isinstance(value, Document)
        else _Source(provenance, value or "")
        for provenance, value in texts.items()
    }
    updates: dict[str, Fact] = {}
    for name in GATED_FIELDS:
        current: Fact = getattr(facts, name)
        if current.known:
            updates[name] = _gate_one(name, current, sources)
    return replace(facts, **updates)


def _gate_one(name: str, current: Fact, sources: Mapping[Provenance, _Source]) -> Fact:
    quote = current.note
    ordered = [sources[p] for p in (current.source, *SOURCE_ORDER) if p in sources]
    ordered = list(dict.fromkeys(ordered))
    is_list = isinstance(current.value, list)
    items = list(current.value) if is_list else [current.value]

    if is_list and not items:
        # An empty list is a claim of "none"; it stands only on a quote saying so.
        if name == "frequencies_hz":
            return Fact(note=NOT_FOUND)
        for source in ordered:
            if source.has_text(quote, minimum=MIN_QUOTE_KEY):
                return Fact(value=[], source=source.provenance, note=source.note_for(quote))
        return Fact(note=NOT_FOUND)

    best: tuple[_Source, list[Any]] | None = None
    for source in ordered:
        kept = [item for item in items if _item_found(name, item, quote, source)]
        if best is None or len(kept) > len(best[1]):
            best = (source, kept)
    if best is None or not best[1]:
        return Fact(note=NOT_FOUND)
    source, kept = best
    if not is_list:
        return Fact(value=current.value, source=source.provenance, note=source.note_for(quote))
    if len(kept) < len(items) and name in WHOLE_LIST_FIELDS:
        return Fact(note=NOT_FOUND)
    if name == "manual_languages":
        kept = [normalise_language(str(v)) for v in kept]
    return Fact(value=kept, source=source.provenance, note=source.note_for(quote))

