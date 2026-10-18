"""The handover card: the manual's own words, cut to one page (design §5.2).

Retrieval first: the model sees only the manual sections whose headings match
what a clinic needs on day one (setup, operation, cleaning, maintenance,
filters, accessories, troubleshooting, warnings). Then a gate in code drops
every line whose numbers or part codes are not printed in the manual, or not
on the page the line cites. The model writes the card; the manual decides
which of its lines survive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from app.ai.client import CLIENT, GenAiClient
from app.ai.packet_prompts import CARD_SYSTEM, CARD_USER

#: The excerpt handed to the model is cut to about this many characters.
EXCERPT_CAP = 14_000
#: A section is only truncated into the last of the budget if this much is left.
MIN_TRUNCATED_SECTION = 600
CARD_MAX_TOKENS = 6000

SECTIONS = ("quick_start", "care_schedule", "consumables", "warnings")

# Heading terms in priority order: the order the budget is spent in.
HEADING_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("quick start", r"quick[\s-]*start|getting started"),
        ("setup", r"set[\s-]*up|setting up|install"),
        ("operation", r"operat"),
        ("cleaning", r"clean|caring|\bcare\b"),
        ("maintenance", r"maint|servic"),
        ("filter", r"filter"),
        ("accessories", r"accessor"),
        ("troubleshooting", r"troubleshoot"),
        ("warnings", r"warning|safety|safeguard|precaution"),
    )
)

# One-word call-outs printed in caps inside a section; never section headings.
CALLOUT_WORDS = frozenset({"DANGER", "WARNING", "CAUTION", "NOTE", "NOTICE"})
MAX_HEADING_INDENT = 3
MAX_HEADING_LENGTH = 90

PAGE_MARKER = re.compile(r"^=== page (\d+) ===[ \t]*$", re.MULTILINE)
DOT_LEADER = re.compile(r"\.{4,}")
SPACES = re.compile(r"[ \t]{2,}")

# Tokens for the gate. Joiners inside a token: hyphen, slash, dot.
TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-/.][A-Za-z0-9]+)*")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
UNITS = frozenset({
    "v", "vac", "vdc", "hz", "w", "kw", "a", "ma", "l", "lpm", "ml", "mm", "cm", "m",
    "km", "kg", "g", "lb", "lbs", "psi", "psig", "kpa", "bar", "mbar", "min", "mins",
    "h", "hr", "hrs", "s", "sec", "db", "dba", "c", "f", "ft", "in", "x",
})
NUMBER_WITH_UNIT = re.compile(r"(\d+(?:\.\d+)?)([A-Za-z]{1,4})")
NUMBER_HYPHEN_WORD = re.compile(r"(\d+(?:\.\d+)?)-([A-Za-z]+)")
MIN_PART_CODE_LENGTH = 4


@dataclass(frozen=True)
class CardLine:
    """One line of the card, with the manual page it came from."""

    text: str
    page: int | None = None
    interval: str = ""
    part_number: str = ""
    # Set on a translated line that shows the English instead (design §5.4).
    note: str = ""


@dataclass(frozen=True)
class Card:
    """The English handover card for one device."""

    label: str = ""
    quick_start: tuple[CardLine, ...] = ()
    care_schedule: tuple[CardLine, ...] = ()
    consumables: tuple[CardLine, ...] = ()
    warnings: tuple[CardLine, ...] = ()
    dropped: int = 0

    @property
    def lines(self) -> tuple[CardLine, ...]:
        return (*self.quick_start, *self.care_schedule, *self.consumables, *self.warnings)

    @property
    def empty(self) -> bool:
        return not self.lines

    def to_dict(self) -> dict[str, Any]:
        """The §5.2 JSON shape, plus the label and the dropped count."""
        def row(line: CardLine, **fields: Any) -> dict[str, Any]:
            out = {**fields, "page": line.page}
            if line.note:
                out["note"] = line.note
            return out

        return {
            "label": self.label,
            "dropped": self.dropped,
            "quick_start": [row(l, text=l.text) for l in self.quick_start],
            "care_schedule": [
                row(l, text=l.text, interval=l.interval) for l in self.care_schedule
            ],
            "consumables": [
                row(l, item=l.text, part_number=l.part_number, interval=l.interval)
                for l in self.consumables
            ],
            "warnings": [row(l, text=l.text) for l in self.warnings],
        }


def card_from_dict(payload: Mapping[str, Any], label: str = "") -> Card:
    """A Card from the model's JSON or a stored card. Malformed lines are skipped."""
    def lines(name: str) -> tuple[CardLine, ...]:
        found = []
        for raw in payload.get(name) or ():
            if not isinstance(raw, Mapping):
                continue
            text = _clean(raw.get("item") if name == "consumables" else raw.get("text"))
            if not text:
                continue
            found.append(CardLine(
                text=text, page=_page(raw.get("page")),
                interval=_clean(raw.get("interval")),
                part_number=_clean(raw.get("part_number")),
                note=_clean(raw.get("note")),
            ))
        return tuple(found)

    dropped = payload.get("dropped")
    return Card(
        label=str(payload.get("label") or label),
        quick_start=lines("quick_start"), care_schedule=lines("care_schedule"),
        consumables=lines("consumables"), warnings=lines("warnings"),
        dropped=dropped if isinstance(dropped, int) else 0,
    )


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return "" if text.lower() in {"none", "null", "n/a", "not stated"} else text


def _page(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


# --- pages and sections ---------------------------------------------------


def split_pages(text: str) -> dict[int, str]:
    """Page number -> page text, from "=== page N ===" markers or form feeds.

    {} when the text carries neither: the pages are unknown and the whole text
    is one page (design §4.1).
    """
    markers = list(PAGE_MARKER.finditer(text or ""))
    if markers:
        pages: dict[int, str] = {}
        for i, marker in enumerate(markers):
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            number = int(marker.group(1))
            pages[number] = pages.get(number, "") + text[marker.end():end]
        return pages
    if "\f" in (text or ""):
        return {i + 1: chunk for i, chunk in enumerate(text.split("\f"))}
    return {}


def _is_heading(raw: str) -> bool:
    line = raw.strip()
    if not line or len(line) > MAX_HEADING_LENGTH or DOT_LEADER.search(line):
        return False
    if len(raw) - len(raw.lstrip()) > MAX_HEADING_INDENT:
        return False
    words = line.split()
    long_words = [w for w in words if sum(c.isalpha() for c in w) >= 4]
    if words[0].strip(":–-").upper() in CALLOUT_WORDS and len(long_words) <= 1:
        return False
    letters = [c for c in line if c.isalpha()]
    visible = [c for c in line if not c.isspace()]
    if not letters or len(letters) < 0.6 * len(visible):
        return False
    if sum(c.isupper() for c in letters) < 0.8 * len(letters):
        return False
    return bool(long_words)


@dataclass
class _Section:
    heading: str
    lines: list[tuple[int | None, str]] = field(default_factory=list)


def _sections(text: str, pages: Mapping[int, str]) -> list[_Section]:
    numbered: Iterable[tuple[int | None, str]]
    if pages:
        numbered = ((n, line) for n in sorted(pages) for line in pages[n].splitlines())
    else:
        numbered = ((None, line) for line in (text or "").splitlines())
    sections = [_Section("")]
    for page, line in numbered:
        if _is_heading(line):
            sections.append(_Section(line.strip()))
        sections[-1].lines.append((page, line))
    return sections


def _render(lines: list[tuple[int | None, str]]) -> list[str]:
    out: list[str] = []
    marked: int | None = None
    for number, raw in lines:
        line = SPACES.sub("  ", raw.strip())
        if not line:
            continue
        if number is not None and number != marked:
            out.append(f"=== page {number} ===")
            marked = number
        out.append(line)
    return out


def select_sections(text: str, pages: Mapping[int, str] | None = None) -> str:
    """The manual sections a handover card needs, capped at EXCERPT_CAP chars.

    Picks, for each heading term in priority order, the first section whose
    heading matches; spends the budget in that order; returns the chosen
    sections in document order with "=== page N ===" markers. With no
    matching heading, the start of the text is used.
    """
    pages = pages if pages is not None else split_pages(text)
    sections = _sections(text, pages)
    chosen: list[int] = []
    for _, pattern in HEADING_TERMS:
        hit = next((i for i, s in enumerate(sections)
                    if s.heading and i not in chosen and pattern.search(s.heading)), None)
        if hit is not None:
            chosen.append(hit)
    if not chosen:
        every = [line for s in sections for line in s.lines]
        return _truncate(_render(every), EXCERPT_CAP)

    budget = EXCERPT_CAP
    kept: dict[int, list[str]] = {}
    for index in chosen:
        rendered = _render(sections[index].lines)
        size = sum(len(l) + 1 for l in rendered)
        if size <= budget:
            kept[index] = rendered
            budget -= size
        elif budget >= MIN_TRUNCATED_SECTION:
            kept[index] = _truncate(rendered, budget).splitlines()
            budget = 0
    return "\n".join(line for i in sorted(kept) for line in kept[i])


def _truncate(lines: list[str], cap: int) -> str:
    out: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > cap:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


# --- the model call ---------------------------------------------------------


def build_card(
    text: str, pages: Mapping[int, str] | None, label: str,
    client: GenAiClient = CLIENT,
) -> Card:
    """Ask the reasoning model for the card. Ungated: pass the result to gate().

    Raises GenAiError when the model cannot be reached or returns no JSON.
    """
    excerpt = select_sections(text, pages)
    if not excerpt.strip():
        return Card(label=label)
    payload = client.complete_json(
        CARD_USER.format(label=label or "(not given)", excerpt=excerpt),
        system=CARD_SYSTEM, max_tokens=CARD_MAX_TOKENS,
    )
    return card_from_dict(payload, label)


# --- the gate ---------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    numbers: frozenset[str]
    codes: frozenset[str]


def evidence(text: str) -> Evidence:
    numbers: set[str] = set()
    codes: set[str] = set()
    for token in TOKEN.findall(text or ""):
        kind, values = _classify(token)
        (codes if kind == "code" else numbers).update(values)
    return Evidence(frozenset(numbers), frozenset(codes))


def _classify(token: str) -> tuple[str, tuple[str, ...]]:
    """("code", (code,)) for a part code, else ("number", numbers in it)."""
    if not any(c.isdigit() for c in token):
        return "number", ()
    if NUMBER.fullmatch(token):
        return "number", (_number(token),)
    with_unit = NUMBER_WITH_UNIT.fullmatch(token)
    if with_unit and with_unit.group(2).lower() in UNITS:
        return "number", (_number(with_unit.group(1)),)
    hyphen_word = NUMBER_HYPHEN_WORD.fullmatch(token)
    if hyphen_word:
        return "number", (_number(hyphen_word.group(1)),)
    has_letter = any(c.isalpha() for c in token)
    if len(token) >= MIN_PART_CODE_LENGTH and (has_letter or "-" in token):
        return "code", (token.upper(),)
    return "number", tuple(_number(n) for n in NUMBER.findall(token))


def _number(text: str) -> str:
    """"05" and "5" are one number; "5.0" and "5" are not merged (a manual prints one)."""
    whole, _, fraction = text.partition(".")
    whole = whole.lstrip("0") or "0"
    return f"{whole}.{fraction}" if fraction else whole


def line_evidence(line: CardLine) -> Evidence:
    """The numbers and part codes a card line states, in every field."""
    return evidence(" ".join((line.text, line.interval, line.part_number)))


def _survives(line: CardLine, document: Evidence, pages: Mapping[int, Evidence]) -> bool:
    stated = line_evidence(line)
    if not stated.numbers <= document.numbers or not stated.codes <= document.codes:
        return False
    if not pages:
        return True
    page = pages.get(line.page) if line.page is not None else None
    if page is None:
        return False
    if not stated.numbers and not stated.codes:
        return True
    return bool(stated.codes & page.codes) or bool(stated.numbers & page.numbers)


def gate(card: Card, text: str, pages: Mapping[int, str] | None = None) -> Card:
    """Drop every line the manual does not bear out, counting them in `dropped`.

    Every number and part code in a line must occur in the document text.
    When pages are known, the cited page must exist and the line's part code
    or at least one of its numbers must occur on that page.
    """
    pages = pages if pages is not None else split_pages(text)
    document = evidence(text)
    per_page = {n: evidence(t) for n, t in pages.items()}
    kept: dict[str, tuple[CardLine, ...]] = {}
    dropped = 0
    for name in SECTIONS:
        lines = getattr(card, name)
        survivors = tuple(l for l in lines if _survives(l, document, per_page))
        dropped += len(lines) - len(survivors)
        kept[name] = survivors
    return replace(card, dropped=card.dropped + dropped, **kept)
