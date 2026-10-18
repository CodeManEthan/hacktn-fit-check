"""Turn domain objects into the few strings the templates render.

Keeps formatting decisions out of Jinja, where they are hard to test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from app.domain.device import DeviceFacts
from app.domain.result import CheckStatus, Outcome

if TYPE_CHECKING:  # pragma: no cover - the pipeline imports nothing from here
    from app.matching.pipeline import Candidate

TONE_BY_STATUS = {
    CheckStatus.PASS: "good",
    CheckStatus.FAIL: "bad",
    CheckStatus.MITIGABLE: "hold",
    CheckStatus.NEEDS_INFO: "ask",
}

TONE_BY_OUTCOME = {
    Outcome.WORKS: "good",
    Outcome.WORKS_IF: "hold",
    Outcome.WONT_WORK: "bad",
    Outcome.NEEDS_INFO: "ask",
}

# The log reads outcomes back as stored strings, not enum members.
TONE_BY_OUTCOME_VALUE = {o.value: tone for o, tone in TONE_BY_OUTCOME.items()}


def _range(low, high, unit: str) -> str | None:
    if not (low.known and high.known):
        return None
    if low.value == high.value:
        return f"{low.value:g} {unit}"
    return f"{low.value:g}–{high.value:g} {unit}"


def identity_pairs(device: DeviceFacts) -> list[tuple[str, str]]:
    """Label and value pairs for the rating-plate block. Unknowns are left out."""
    pairs: list[tuple[str, str]] = []

    supply = _range(device.voltage_min_v, device.voltage_max_v, "V")
    if supply:
        pairs.append(("Supply", supply))

    if device.frequencies_hz.known:
        shown = "/".join(str(v) for v in device.frequencies_hz.value)
        pairs.append(("Frequency", f"{shown} Hz"))

    if device.power_watts.known:
        pairs.append(("Draw", f"{device.power_watts.value:g} VA"))

    climate = _range(device.temp_min_c, device.temp_max_c, "°C")
    if climate:
        pairs.append(("Rated for", climate))

    if device.humidity_max_pct.known:
        pairs.append(("Humidity", f"to {device.humidity_max_pct.value:g}%"))

    if device.year_of_manufacture.known:
        pairs.append(("Made", str(device.year_of_manufacture.value)))

    return pairs


def device_kind(device: DeviceFacts) -> str:
    """What the donor called it, minus the name already shown above it."""
    raw = (device.raw_input or "").strip().splitlines()
    if not raw:
        return ""
    line = raw[0].strip()
    label = device.label
    if label and label.lower() in line.lower():
        start = line.lower().index(label.lower())
        line = (line[:start] + line[start + len(label):]).strip(" ,.-\u2013")
    return line[:90]


LOCAL_PRICES_PATH = Path(__file__).resolve().parents[2] / "data" / "local_prices.json"

# The one plate check whose failure a local purchase answers today. Voltage
# is MITIGABLE (a transformer ships), so it never reaches a rejected offer.
ADVISED_FAILURES = frozenset({"frequency"})


@dataclass(frozen=True)
class Advisory:
    """What buying locally would cost instead, shown under a rejected offer.

    It reads the verdict and never writes one. `rows` are the price rows,
    each a dict with `what`, `price_text`, `source` and `url` (plus `role`:
    "instead", "add-on" or "with it"); `as_of` is the date the prices were
    read; `sold_locally_text` is the sold-locally fact in words; and
    `supply_url` is where the nonprofit records an answer, set only while
    that fact is unknown.
    """

    rows: tuple[dict, ...] = ()
    as_of: str = ""
    sold_locally_text: str = ""
    supply_url: str = ""


def _type_key(device_type: str) -> str:
    return " ".join((device_type or "").lower().split())


def load_local_prices(path: Path | None = None) -> dict[str, dict]:
    """The price table by device type (lower case). Empty when the file is absent."""
    path = path or LOCAL_PRICES_PATH
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        _type_key(kind): entry for kind, entry in table.items()
        if not kind.startswith("_") and isinstance(entry, dict)
    }


def _dollars(amount: float) -> str:
    return f"${amount:,.0f}"


def price_text(row: dict) -> str:
    """'about $290' for an indicative price, '$1,319 to $2,273' for a range, '$205' else."""
    if "price_usd_min" in row and "price_usd_max" in row:
        return f"{_dollars(row['price_usd_min'])} to {_dollars(row['price_usd_max'])}"
    if "price_usd" not in row:
        return str(row.get("price_text", ""))
    if row.get("indicative"):
        return f"about {_dollars(round(row['price_usd'], -1))}"
    return _dollars(row["price_usd"])


def sold_locally_line(sold, country: str) -> str:
    where = country or "the country"
    if not sold.known:
        return f"Sold in {where}: don't know, ask the nonprofit"
    answer = "yes" if sold.value else "no"
    return f"Sold in {where}: {answer}" + (f" ({sold.note})" if sold.note else "")


def supply_url(country: str, device_type: str) -> str:
    """The form where the nonprofit records the answer. The template adds `back`."""
    query = urlencode({"device_type": device_type, "model": ""})
    return f"/supply/{quote(country)}?{query}"


def advisory_for(candidate: "Candidate", prices: dict[str, dict] | None = None) -> Advisory | None:
    """The buy-locally advisory for one rejected candidate, or None.

    Only for a WONT_WORK verdict that a frequency failure is part of, and
    only for a device type the price table knows.
    """
    verdict = candidate.verdict
    if verdict.outcome is not Outcome.WONT_WORK:
        return None
    if not any(r.key in ADVISED_FAILURES for r in verdict.failures):
        return None
    device_type = candidate.offer.device_type
    entry = (load_local_prices() if prices is None else prices).get(_type_key(device_type))
    if not entry or not entry.get("rows"):
        return None

    rows = tuple(
        {**row, "price_text": price_text(row)} for row in entry["rows"]
    )
    country = candidate.intel.country
    sold = candidate.intel.sold_locally
    return Advisory(
        rows=rows,
        as_of=str(entry.get("as_of", "")),
        sold_locally_text=sold_locally_line(sold, country),
        supply_url="" if sold.known or not country else supply_url(country, device_type),
    )


def rule_count() -> int:
    """How many deterministic checks are registered.

    Rendered into the top bar. Read from the registry rather than written as a
    number, because the number has already been wrong once in this interface.
    """
    from app.checks.runner import ALL_CHECKS, ROUTE_CHECKS

    return len(ALL_CHECKS) + len(ROUTE_CHECKS)
