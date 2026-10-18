"""The device library: verified, page-cited facts for named models.

The committed file is data/device_library.json, a list of entries, and the
document texts sit under data/library/<slug>/<file>.txt. Both are optional:
with neither present every lookup returns an empty result, never an error,
so the app runs before the library is built.

Each entry's facts carry the DeviceFacts field names and a source label per
fact. `to_device_facts` turns an entry into a DeviceFacts whose every value
points at the document it was read from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from typing import Any

from app.config import PROJECT_ROOT
from app.domain.device import DeviceFacts
from app.domain.language import normalise_languages
from app.domain.provenance import Fact, Provenance, fact

LIBRARY_PATH = PROJECT_ROOT / "data" / "device_library.json"
LIBRARY_DIR = PROJECT_ROOT / "data" / "library"

# Source labels in the library file, mapped to the provenance a fact carries.
# Manuals, spec sheets, service manuals and product pages are the
# manufacturer's own word; GUDID and 510(k) records are the registry's.
SOURCE_LABELS: dict[str, Provenance] = {
    "spec sheet": Provenance.SPEC_SHEET,
    "manual": Provenance.SPEC_SHEET,
    "service manual": Provenance.SPEC_SHEET,
    "product page": Provenance.SPEC_SHEET,
    "fda gudid": Provenance.REGISTRY,
    "gudid": Provenance.REGISTRY,
    "510(k)": Provenance.REGISTRY,
    "registry": Provenance.REGISTRY,
}
NOT_STATED = "not stated"
LANGUAGE_FIELDS = ("manual_languages",)

# Ranks for best_match. Only a model or alias rank can make a hit.
MODEL_RANK = 3
ALIAS_RANK = 2
MANUFACTURER_RANK = 1
NO_RANK = 0

_TOKEN = re.compile(r"[a-z0-9]+")
_FACT_FIELDS = frozenset(
    f.name for f in fields(DeviceFacts) if f.name not in ("raw_input", "documents")
)


@dataclass(frozen=True)
class LibraryEntry:
    """One model in the library, as the library file states it."""

    slug: str
    manufacturer: str
    model: str
    device_type: str = ""
    aliases: tuple[str, ...] = ()
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: tuple[dict[str, Any], ...] = ()
    notes: str = ""

    @property
    def label(self) -> str:
        return f"{self.manufacturer} {self.model}".strip()


def _entry_from_dict(payload: dict[str, Any]) -> LibraryEntry:
    return LibraryEntry(
        slug=str(payload["slug"]),
        manufacturer=str(payload.get("manufacturer") or ""),
        model=str(payload.get("model") or ""),
        device_type=str(payload.get("device_type") or ""),
        aliases=tuple(str(a) for a in payload.get("aliases") or ()),
        facts=dict(payload.get("facts") or {}),
        documents=tuple(dict(d) for d in payload.get("documents") or ()),
        notes=str(payload.get("notes") or ""),
    )


def load_library() -> tuple[LibraryEntry, ...]:
    """Every entry in the library file, or () when there is no file."""
    if not LIBRARY_PATH.exists():
        return ()
    payload = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    entries = payload.get("entries", []) if isinstance(payload, dict) else payload
    return tuple(_entry_from_dict(e) for e in entries)


def entry(slug: str) -> LibraryEntry | None:
    return next((e for e in load_library() if e.slug == slug), None)


def document_text(slug: str, file: str) -> str:
    """The stored text of one document, or "" when it is not in the library.

    `file` may be the full "<slug>/<name>.txt" the entries use or just the
    name. A path that would leave the library directory reads as absent.
    """
    relative = file if file.startswith(f"{slug}/") else f"{slug}/{file}"
    root = LIBRARY_DIR.resolve()
    path = (LIBRARY_DIR / relative).resolve()
    if root not in path.parents or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def _names(e: LibraryEntry) -> list[str]:
    return [e.manufacturer, e.model, *e.aliases]


def search(text: str) -> list[LibraryEntry]:
    """Entries whose manufacturer, model or aliases share a token with `text`.

    Ranked by how many of the query's tokens the entry holds, then by an exact
    model token hit, then by slug, so the order is stable.
    """
    wanted = _tokens(text)
    if not wanted:
        return []
    scored = []
    for e in load_library():
        held = set().union(*(_tokens(n) for n in _names(e)))
        hits = len(wanted & held)
        if hits:
            model_hit = 1 if wanted & _tokens(e.model) else 0
            scored.append((-hits, -model_hit, e.slug, e))
    return [e for *_, e in sorted(scored, key=lambda s: s[:3])]


def _key(text: str) -> str:
    """A model string with spaces, hyphens and case removed, so "525 DS" is "525DS"."""
    return re.sub(r"[\s\-]+", "", text or "").casefold()


def _same(left: str, right: str) -> bool:
    return bool(_key(left)) and _key(left) == _key(right)


def _rank(e: LibraryEntry, manufacturer: str, model: str) -> int:
    if _same(model, e.model):
        return MODEL_RANK
    if any(_same(model, alias) for alias in e.aliases):
        return ALIAS_RANK
    if _same(manufacturer, e.manufacturer):
        return MANUFACTURER_RANK
    return NO_RANK


def best_match(manufacturer: str | None, model: str | None) -> LibraryEntry | None:
    """The one entry the named model picks out, or None.

    Entries rank by an exact case-insensitive match of the model against the
    entry's model, then against an alias, then by manufacturer alone. A hit
    exists only when exactly one entry holds the top rank and that rank is a
    model or alias match. A tie, or a manufacturer-only match, is no hit.
    """
    ranked = [(_rank(e, manufacturer or "", model or ""), e) for e in load_library()]
    if not ranked:
        return None
    top = max(rank for rank, _ in ranked)
    leaders = [e for rank, e in ranked if rank == top]
    if top < ALIAS_RANK or len(leaders) != 1:
        return None
    return leaders[0]


def _document_title(e: LibraryEntry, file: str, url: str = "") -> str:
    """The title of the document a fact cites, by its stored file or its URL."""
    bare_url = (url or "").split("#", 1)[0]
    for doc in e.documents:
        if file and doc.get("file") == file:
            return str(doc.get("title") or file)
    for doc in e.documents:
        if bare_url and str(doc.get("url") or "").split("#", 1)[0] == bare_url:
            return str(doc.get("title") or bare_url)
    return file


def citation(e: LibraryEntry, name: str) -> dict[str, Any]:
    """Where one fact of the entry was read: title, link, page and quote.

    The link carries the PDF page anchor when the library stored one. An
    absent fact gives empty strings, never an error.
    """
    payload = e.facts.get(name)
    if not isinstance(payload, dict):
        return {"title": "", "url": "", "page": None, "quote": "", "note": ""}
    file = str(payload.get("document") or "")
    url = str(payload.get("url") or "")
    return {
        "title": _document_title(e, file, url) if (file or url) else "",
        "url": url,
        "page": payload.get("page"),
        "quote": str(payload.get("quote") or ""),
        "note": str(payload.get("note") or ""),
    }


def _source(label: str) -> Provenance:
    return SOURCE_LABELS.get((label or "").strip().lower(), Provenance.SPEC_SHEET)


def _fact_from_entry(e: LibraryEntry, name: str, payload: Any) -> Fact | None:
    """One library fact as a Fact, or None when the library leaves it unknown."""
    if not isinstance(payload, dict):
        return None
    value = payload.get("value")
    if value is None or NOT_STATED in str(payload.get("quote") or "").lower():
        return None
    if name == "frequencies_hz" and value == []:
        return None
    if name in LANGUAGE_FIELDS:
        value = normalise_languages(value)
        if not value:
            return None
    cited = citation(e, name)
    note = cited["title"]
    if note and cited["page"] not in (None, ""):
        note = f"{note}, p.{cited['page']}"
    if cited["note"]:
        # What the quote does and does not entail travels with the value.
        note = f"{note}; {cited['note']}" if note else cited["note"]
    return fact(value, _source(str(payload.get("source") or "")), note)


def _documents_fact(e: LibraryEntry) -> Fact | None:
    if not e.documents:
        return None
    listed = [
        {
            "slug": e.slug,
            "file": str(doc.get("file") or ""),
            "title": str(doc.get("title") or ""),
            "url": str(doc.get("url") or ""),
            "languages": normalise_languages(doc.get("languages") or ()),
        }
        for doc in e.documents
    ]
    return fact(listed, Provenance.SPEC_SHEET)


def to_device_facts(e: LibraryEntry) -> DeviceFacts:
    """The entry as DeviceFacts: every value with its source and its citation.

    `note` on each fact is "<document title>, p.<page>". Manufacturer and
    model come from the entry's own fields when its facts do not state them.
    """
    values: dict[str, Fact] = {}
    for name, payload in e.facts.items():
        if name in _FACT_FIELDS:
            built = _fact_from_entry(e, name, payload)
            if built is not None:
                values[name] = built
    if "manufacturer" not in values and e.manufacturer:
        values["manufacturer"] = fact(e.manufacturer, Provenance.SPEC_SHEET)
    if "model" not in values and e.model:
        values["model"] = fact(e.model, Provenance.SPEC_SHEET)
    documents = _documents_fact(e)
    if documents is not None:
        values["documents"] = documents
    return DeviceFacts(**values)
