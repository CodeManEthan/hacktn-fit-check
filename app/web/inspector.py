"""The offer page's inspector: extracted facts as rows, and the donor's "wrong" marks.

Each row shows one reading (some readings span two fields, such as a voltage
range), where it came from, and a link to the page it was read from. The
donor may mark a row wrong; the form then posts the row's field names in
`cleared_fields`, and `clear_fields` turns exactly those facts back to
unknown. The client never posts a value: a field name is all it can send.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import fields, replace
from typing import Any, Protocol

from app.domain.device import DeviceFacts
from app.domain.provenance import UNKNOWN, Fact

UNVERIFIED = "unverified"

# (label, the DeviceFacts fields the row shows), in the order a plate and a
# spec sheet print them.
ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Model", ("manufacturer", "model")),
    ("Voltage", ("voltage_min_v", "voltage_max_v")),
    ("Frequency", ("frequencies_hz",)),
    ("Power", ("power_watts",)),
    ("Operating temp", ("temp_min_c", "temp_max_c")),
    ("Humidity", ("humidity_max_pct",)),
    ("Year made", ("year_of_manufacture",)),
    ("Consumables", ("consumables",)),
    ("Manual languages", ("manual_languages",)),
    ("Needs oxygen", ("needs_oxygen",)),
    ("Needs compressed air", ("needs_compressed_air",)),
    ("Needs distilled water", ("needs_distilled_water",)),
    ("Needs stable power", ("needs_stable_power",)),
    ("Operating complexity", ("operating_complexity",)),
)
ROW_LABELS = tuple(label for label, _ in ROWS)
CLEARABLE = frozenset(name for _, names in ROWS for name in names)
_FACT_FIELDS = frozenset(f.name for f in fields(DeviceFacts) if f.name != "raw_input")
_SPLIT = re.compile(r"[\s,]+")


class _Cited(Protocol):
    title: str
    page: int | None

    @property
    def link(self) -> str: ...


def inspector_rows(
    facts: DeviceFacts, citations: Mapping[str, _Cited] | None = None
) -> list[dict[str, Any]]:
    """One dict per row: label, value, source, source_url, page, field, known, note.

    `field` is the comma-joined field names the row's "wrong" mark clears. An
    unknown reading is still a row, tagged unverified: the gap is the finding.
    """
    citations = citations or {}
    rows = []
    for label, names in ROWS:
        shown = _value_text(facts, names)
        used_name = _name_shown(facts, names)
        used = getattr(facts, used_name) if used_name else UNKNOWN
        # The link cites the same fact as the source and note, so all three agree
        # (the 525DS Model row once linked p.44, the maker's address, beside "p.11").
        cited = citations.get(used_name) if used_name else None
        if not shown:
            note = next((getattr(facts, n).note for n in names if getattr(facts, n).note), "")
        else:
            note = used.note
        rows.append({
            "label": label,
            "value": shown,
            "source": used.source.value if shown else UNVERIFIED,
            "source_url": cited.link if (shown and cited) else "",
            "page": cited.page if (shown and cited) else None,
            "document": cited.title if (shown and cited) else "",
            "field": ",".join(names),
            "known": bool(shown),
            "note": note,
        })
    return rows


def _name_shown(facts: DeviceFacts, names: tuple[str, ...]) -> str | None:
    """The field whose source the row shows: the first known one ("Model" prefers the model)."""
    ordered = ("model", "manufacturer") if names == ("manufacturer", "model") else names
    return next((n for n in ordered if getattr(facts, n).known), None)


def _fact_shown(facts: DeviceFacts, names: tuple[str, ...]) -> Fact:
    """The fact whose source the row shows: the first known one ("Model" prefers the model)."""
    name = _name_shown(facts, names)
    return getattr(facts, name) if name else UNKNOWN


def _value_text(facts: DeviceFacts, names: tuple[str, ...]) -> str:
    first = getattr(facts, names[0])
    if names == ("manufacturer", "model"):
        return facts.label if (facts.model.known or facts.manufacturer.known) else ""
    if names == ("voltage_min_v", "voltage_max_v"):
        return _range_text(facts.voltage_min_v, facts.voltage_max_v, "V")
    if names == ("temp_min_c", "temp_max_c"):
        return _range_text(facts.temp_min_c, facts.temp_max_c, "°C")
    if not first.known:
        return ""
    value = first.value
    name = names[0]
    if name == "frequencies_hz":
        return "/".join(_number(v) for v in value) + " Hz" if value else "none stated"
    if name == "power_watts":
        return f"{_number(value)} W"
    if name == "humidity_max_pct":
        return f"up to {_number(value)} %"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "none"
    return str(value)


def _number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _range_text(low: Fact, high: Fact, unit: str) -> str:
    if low.known and high.known:
        if low.value == high.value:
            return f"{_number(low.value)} {unit}"
        return f"{_number(low.value)}–{_number(high.value)} {unit}"
    if low.known:
        return f"from {_number(low.value)} {unit}"
    if high.known:
        return f"to {_number(high.value)} {unit}"
    return ""


def parse_cleared(text: str | Iterable[str]) -> tuple[str, ...]:
    """The field names a form posted as wrong, keeping only names a row can clear."""
    parts = _SPLIT.split(text) if isinstance(text, str) else [
        p for chunk in text for p in _SPLIT.split(str(chunk))
    ]
    seen: list[str] = []
    for part in parts:
        if part in CLEARABLE and part in _FACT_FIELDS and part not in seen:
            seen.append(part)
    return tuple(seen)


def clear_fields(facts: DeviceFacts, names: Iterable[str]) -> DeviceFacts:
    """The facts with each named field back to unknown."""
    cleared = {name: UNKNOWN for name in parse_cleared(list(names))}
    return replace(facts, **cleared) if cleared else facts
