"""Screen a donor's whole inventory, one CSV row per offer (design §8).

Each row becomes an Offer that is never saved here, and goes through the same
`screen_offers` the matches page uses, against one chosen clinic, with the
same supply order (`supply_lookup`). A row that names a library model takes
the library's cited facts; the row adds only what is about the donor's units
(build year, quantity, where they are). Every other row's values are the
donor's word (DONOR_STATED). A blank expiry is the donor's "nothing dated"
answer, applied last as on the offer form.

A malformed row is reported in its own result row with what is wrong, never
raised: one bad line must not hide the verdicts on the rest of the file.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Iterable

from app.ai.categorise import keyword_category
from app.domain.device import DeviceFacts
from app.domain.facility import FacilityProfile
from app.domain.language import normalise_languages
from app.domain.marketplace import GeoPoint, Offer
from app.domain.provenance import Fact, Provenance, fact
from app.domain.result import Outcome, Verdict
from app.matching.pipeline import IntelLookup, screen_offers
from app.matching.supply_order import supply_lookup
from app.reference import device_library
from app.reference.country_power import country_centroid
from app.web import forms
from app.web.forms import FormError

# The §8 column contract, in the order the download writes them.
COLUMNS = (
    "device_type", "manufacturer", "model", "quantity", "year",
    "voltage_min_v", "voltage_max_v", "frequencies_hz", "power_watts",
    "temp_min_c", "temp_max_c", "humidity_max_pct", "manual_languages",
    "expiry_date", "city", "country",
)
RESULT_COLUMNS = ("verdict", "blockers", "send_with", "questions")

# The plate and climate columns. On a library row these are the library's,
# never the row's: a cited value is not edited by hand (§4.2).
SPEC_COLUMNS = (
    "voltage_min_v", "voltage_max_v", "frequencies_hz", "power_watts",
    "temp_min_c", "temp_max_c", "humidity_max_pct", "manual_languages",
)
NUMBER_COLUMNS = {
    "voltage_min_v": "Voltage min (V)",
    "voltage_max_v": "Voltage max (V)",
    "power_watts": "Power (W)",
    "temp_min_c": "Temp min (C)",
    "temp_max_c": "Temp max (C)",
    "humidity_max_pct": "Humidity max (%)",
}

MAX_ROWS = 500
MAX_BYTES = 1_000_000
EARLIEST_BUILD_YEAR = 1950
LIST_SEPARATORS = (";", "/", ",", "|")

STATUS_SCREENED = "screened"
STATUS_ERROR = "error"
STATUS_BLANK = "blank"


class CsvError(ValueError):
    """The file as a whole cannot be read: no header, nothing we recognise, too big."""


@dataclass(frozen=True)
class RowResult:
    """One input row and what the screening said about it.

    `line` is the row's line number in the file (the header is line 1).
    `values` holds the row's cells under the §8 column names, as typed.
    `offer` is the unsaved Offer the row became, None for an error or blank row.
    """

    line: int
    values: dict[str, str]
    status: str
    offer: Offer | None = None
    verdict: Verdict | None = None
    library_slug: str = ""
    library_label: str = ""
    error: str = ""
    notes: tuple[str, ...] = ()

    @property
    def outcome(self) -> str:
        if self.status == STATUS_ERROR:
            return "error"
        if self.status == STATUS_BLANK:
            return "blank row"
        return self.verdict.outcome.value if self.verdict else ""

    @property
    def stamp(self) -> str:
        if self.verdict is None:
            return "Not read" if self.status == STATUS_ERROR else "Skipped"
        return self.verdict.stamp

    @property
    def blockers(self) -> list[str]:
        if self.verdict is None:
            return []
        return [f"{r.label}: {r.detail}" for r in self.verdict.failures]

    @property
    def send_with(self) -> list[str]:
        return list(self.verdict.requirements) if self.verdict else []

    @property
    def questions(self) -> list[str]:
        """Each open question once: its question text, else the check's detail."""
        if self.verdict is None:
            return []
        seen: dict[str, None] = {}
        for result in self.verdict.unknowns:
            text = result.question or result.detail
            if text:
                seen.setdefault(text, None)
        return list(seen)

    @property
    def name(self) -> str:
        if self.offer is None:
            return " ".join(p for p in (self.values.get("manufacturer", ""),
                                        self.values.get("model", "")) if p) or "(no name)"
        device = self.offer.device
        if device.model.known or device.manufacturer.known:
            return device.label
        return f"Unnamed {self.offer.device_type or 'device'}"


@dataclass(frozen=True)
class CsvScreening:
    facility_slug: str
    facility_name: str
    rows: tuple[RowResult, ...]
    ignored_columns: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()

    @property
    def screened(self) -> tuple[RowResult, ...]:
        return tuple(r for r in self.rows if r.status == STATUS_SCREENED)

    @property
    def errors(self) -> tuple[RowResult, ...]:
        return tuple(r for r in self.rows if r.status == STATUS_ERROR)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for r in self.screened if r.verdict and r.verdict.outcome is outcome)


# ---- reading the file ----------------------------------------------------

def header_key(raw: str | None) -> str:
    """A header cell in the contract's spelling: case, spaces and hyphens ignored."""
    text = (raw or "").replace("﻿", "").strip().casefold()
    for mark in (" ", "-"):
        text = text.replace(mark, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text


def decode(data: bytes) -> str:
    """UTF-8 (with or without Excel's byte-order mark), else Windows-1252."""
    if len(data) > MAX_BYTES:
        raise CsvError(f"The file is {len(data):,} bytes; the limit is {MAX_BYTES:,}.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_rows(text: str) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """The header's keys and each later line's cells with its line number."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise CsvError("The file is empty: it needs a header row naming its columns.") from exc
    except csv.Error as exc:
        raise CsvError(f"The header row cannot be read as CSV: {exc}.") from exc
    keys = [header_key(cell) for cell in header]
    if not any(key in COLUMNS for key in keys):
        raise CsvError(
            "None of the header's columns are ones this screen reads. The first row must "
            "name columns such as device_type, manufacturer, model, quantity, country."
        )
    rows: list[tuple[int, list[str]]] = []
    while True:
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            rows.append((reader.line_num, [f"\0{exc}"]))
            continue
        if not cells:
            continue  # a truly empty line: nothing to report
        rows.append((reader.line_num, cells))
        if len(rows) > MAX_ROWS:
            raise CsvError(f"The file has more than {MAX_ROWS} rows; split it and upload each part.")
    return keys, rows


# ---- one row ---------------------------------------------------------------

def _number(raw: str, label: str) -> float | None:
    value = forms.decimal(raw, label)
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def _split(raw: str) -> list[str]:
    parts = [raw]
    for mark in LIST_SEPARATORS:
        parts = [piece for part in parts for piece in part.split(mark)]
    return [p.strip() for p in parts if p.strip()]


def parse_frequencies(raw: str) -> list[int] | None:
    """ "50/60" or "50;60" or "60" → [50, 60]; blank → None (unknown)."""
    if not (raw or "").strip():
        return None
    found: list[int] = []
    for part in _split(raw.lower().replace("hz", "")):
        try:
            number = float(part)
        except ValueError as exc:
            raise FormError(f"Frequency must look like 50/60. You gave {raw!r}.") from exc
        if not number.is_integer() or number <= 0:
            raise FormError(f"Frequency must be whole hertz such as 50 or 60. You gave {raw!r}.")
        if int(number) not in found:
            found.append(int(number))
    return found


def parse_languages(raw: str) -> list[str] | None:
    """ "en;fr" → ["English", "French"], through the one language vocabulary (§2.9)."""
    names = normalise_languages(_split(raw or ""))
    return names or None


def parse_year(raw: str) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise FormError(f"Year must be a year such as 2016. You gave {value!r}.")
    year = int(value)
    if not EARLIEST_BUILD_YEAR <= year <= date.today().year:
        raise FormError(f"Year must be between {EARLIEST_BUILD_YEAR} and {date.today().year}.")
    return year


def _stated(value) -> Fact:
    return fact(value, Provenance.DONOR_STATED)


def donor_device(values: dict[str, str]) -> DeviceFacts:
    """The row's own words as DONOR_STATED facts; a blank cell stays unknown."""
    facts: dict[str, Fact] = {}
    for name in ("manufacturer", "model"):
        if values.get(name, "").strip():
            facts[name] = _stated(forms.text(values[name], name, limit=200))
    for name, label in NUMBER_COLUMNS.items():
        number = _number(values.get(name, ""), label)
        if number is not None:
            facts[name] = _stated(number)
    low, high = facts.get("voltage_min_v"), facts.get("voltage_max_v")
    if low and high and low.value > high.value:
        raise FormError(f"Voltage min ({low.value}) is above voltage max ({high.value}).")
    frequencies = parse_frequencies(values.get("frequencies_hz", ""))
    if frequencies is not None:
        facts["frequencies_hz"] = _stated(frequencies)
    languages = parse_languages(values.get("manual_languages", ""))
    if languages is not None:
        facts["manual_languages"] = _stated(languages)
    return DeviceFacts(**facts)


def _same_value(left, right) -> bool:
    """Equal, with a list compared as a set (50/60 is 60/50; en;fr is fr;en)."""
    if isinstance(left, list) and isinstance(right, list):
        return set(left) == set(right)
    return left == right


def _origin(country: str, city: str) -> GeoPoint:
    latitude, longitude = country_centroid(country) or (None, None)
    return GeoPoint(country=country, city=city, latitude=latitude, longitude=longitude)


def build_offer(values: dict[str, str], donor_name: str = "", donor_email: str = "",
                ) -> tuple[Offer, device_library.LibraryEntry | None, tuple[str, ...]]:
    """The unsaved Offer one row describes, the library entry it named, and notes.

    Raises FormError for a cell that cannot be read; the caller reports it.
    """
    notes: list[str] = []
    manufacturer = values.get("manufacturer", "").strip()
    model = values.get("model", "").strip()
    picked = device_library.best_match(manufacturer, model) if model else None
    year = parse_year(values.get("year", ""))
    if picked is not None:
        device = device_library.to_device_facts(picked)
        # The row's own plate cells are read (so a malformed one is still
        # reported) and compared, never used: a cited value is not edited here.
        typed = donor_device(values)
        differ = [c for c in SPEC_COLUMNS
                  if getattr(typed, c).known
                  and _same_value(getattr(typed, c).value, getattr(device, c).value) is False]
        if differ:
            notes.append("the library's documented " + ", ".join(differ)
                         + " used, not the row's")
    else:
        device = donor_device(values)
    if year is not None:
        device = replace(device, year_of_manufacture=_stated(year))
    raw = "; ".join(f"{k}={v}" for k, v in values.items() if v.strip())
    device = replace(device, raw_input=f"csv: {raw}"[:2000])
    # The donor's expiry answer is the last word on the date, as on the form.
    device = forms.apply_dated_items(device, values.get("expiry_date", ""))

    device_type = values.get("device_type", "").strip() or (picked.device_type if picked else "")
    device_type = forms.text(device_type, "device_type", required=True, limit=120)
    country = forms.text(values.get("country", ""), "country", required=True, limit=100)
    city = forms.text(values.get("city", ""), "city", limit=100)
    label = picked.label if picked else " ".join(p for p in (manufacturer, model) if p)
    kind = keyword_category(f"{device_type} {label}".strip())
    offer = Offer(
        donor_name=donor_name.strip(),
        donor_email=donor_email.strip(),
        device=device,
        device_type=device_type,
        support_kind=kind.value if kind else "",
        origin=_origin(country, city),
        quantity_offered=forms.whole_number(values.get("quantity", ""), "quantity",
                                            default=1, minimum=1),
    )
    return offer, picked, tuple(notes)


def row_values(keys: list[str], cells: list[str]) -> dict[str, str]:
    """The row's cells under the §8 names; unknown columns dropped."""
    values = {name: "" for name in COLUMNS}
    for key, cell in zip(keys, cells):
        if key in values and not values[key]:
            values[key] = (cell or "").strip()
    return values


def screen_row(line: int, keys: list[str], cells: list[str], facility: FacilityProfile,
               *, intel_for: IntelLookup = supply_lookup, donor_name: str = "",
               donor_email: str = "", today: date | None = None) -> RowResult:
    if len(cells) == 1 and cells[0].startswith("\0"):
        return RowResult(line=line, values=row_values([], []), status=STATUS_ERROR,
                         error=f"This line cannot be read as CSV: {cells[0][1:]}.")
    values = row_values(keys, cells)
    if not any(cell.strip() for cell in cells):
        return RowResult(line=line, values=values, status=STATUS_BLANK)
    if len(cells) > len(keys) and any(c.strip() for c in cells[len(keys):]):
        return RowResult(
            line=line, values=values, status=STATUS_ERROR,
            error=f"This row has {len(cells)} cells but the header names {len(keys)} "
                  "columns; a value may hold an unquoted comma.")
    try:
        offer, picked, notes = build_offer(values, donor_name, donor_email)
    except (FormError, ValueError) as exc:
        return RowResult(line=line, values=values, status=STATUS_ERROR, error=str(exc))
    screening = screen_offers(facility, [offer], intel_for=intel_for, today=today)
    found = [*screening.ranked, *screening.rejected]
    if not found:
        return RowResult(line=line, values=values, status=STATUS_ERROR, offer=offer,
                         error="This row could not be screened (nothing left to offer).")
    return RowResult(
        line=line, values=values, status=STATUS_SCREENED, offer=offer,
        verdict=found[0].verdict,
        library_slug=picked.slug if picked else "",
        library_label=picked.label if picked else "",
        notes=notes,
    )


def screen_csv(data: bytes | str, facility: FacilityProfile, *,
               intel_for: IntelLookup = supply_lookup, donor_name: str = "",
               donor_email: str = "", today: date | None = None) -> CsvScreening:
    """Every row of the file screened against `facility`. Nothing is saved.

    Raises CsvError only when the file as a whole cannot be read; a bad row is
    a result row with `status == "error"`.
    """
    text = decode(data) if isinstance(data, bytes) else data
    keys, rows = read_rows(text)
    results = tuple(
        screen_row(line, keys, cells, facility, intel_for=intel_for,
                   donor_name=donor_name, donor_email=donor_email, today=today)
        for line, cells in rows
    )
    ignored = tuple(dict.fromkeys(k for k in keys if k and k not in COLUMNS))
    missing = tuple(c for c in COLUMNS if c not in keys)
    return CsvScreening(facility_slug=facility.slug, facility_name=facility.name,
                        rows=results, ignored_columns=ignored, missing_columns=missing)


# ---- writing the results ---------------------------------------------------

def _joined(items: Iterable[str]) -> str:
    return " | ".join(items)


def result_cells(row: RowResult) -> dict[str, str]:
    if row.status == STATUS_ERROR:
        return {"verdict": "error", "blockers": row.error, "send_with": "", "questions": ""}
    if row.status == STATUS_BLANK:
        return {"verdict": "blank row", "blockers": "", "send_with": "", "questions": ""}
    return {
        "verdict": row.outcome,
        "blockers": _joined(row.blockers),
        "send_with": _joined(row.send_with),
        "questions": _joined(row.questions),
    }


def to_csv(screening: CsvScreening) -> str:
    """The input columns plus verdict, blockers, send_with, questions (§8)."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=[*COLUMNS, *RESULT_COLUMNS], lineterminator="\r\n")
    writer.writeheader()
    for row in screening.rows:
        writer.writerow({**row.values, **result_cells(row)})
    return out.getvalue()
