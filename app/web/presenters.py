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


def supply_url(country: str, device_type: str, model: str = "", back: str = "") -> str:
    """The form where the nonprofit records the answer.

    `back` is the page to return to once it is saved; a template that knows
    its own path may add it instead.
    """
    params = {"device_type": device_type, "model": model}
    if back:
        params["back"] = back
    return f"/supply/{quote(country)}?{urlencode(params)}"


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


# ---- the candidate verdict page (§7.2) -------------------------------------

# The facts each check reads, as (where, field): "device" is the offer's
# DeviceFacts, "intel" the SupplyIntel. The page links each one's citation.
CHECK_FACTS: dict[str, tuple[tuple[str, str], ...]] = {
    "frequency": (("device", "frequencies_hz"),),
    "voltage": (("device", "voltage_min_v"), ("device", "voltage_max_v")),
    "temperature": (("device", "temp_min_c"), ("device", "temp_max_c")),
    "humidity": (("device", "humidity_max_pct"),),
    "grid": (("device", "needs_stable_power"),),
    "oxygen": (("device", "needs_oxygen"),),
    "water": (("device", "needs_distilled_water"),),
    "air": (("device", "needs_compressed_air"),),
    "consumables": (("device", "consumables"), ("intel", "consumables_available")),
    "spare_parts": (("intel", "spare_parts_available"),),
    "support": (("device", "year_of_manufacture"), ("intel", "manufacturer_supported")),
    "manual": (("device", "manual_languages"),),
    "training": (("device", "operating_complexity"),),
    "import": (("device", "manual_languages"), ("device", "year_of_manufacture")),
    "shelf_life": (("device", "dated_items"), ("device", "expiry_date")),
}

# Questions the nonprofit answers on /supply, by check key and the intel
# field the answer fills. The consumables check asks the donor first (what
# does it consume) and only then the country (are they sold here).
SUPPLY_QUESTION_FIELDS = {
    "consumables": "consumables_available",
    "spare_parts": "spare_parts_available",
}


@dataclass(frozen=True)
class SourceLink:
    """One citation under a check row: the text shown and where it goes."""

    text: str
    url: str = ""
    aside: str = ""
    tag: str = ""


def _documents(device: DeviceFacts) -> list[dict]:
    held = device.documents
    if not held.known or not isinstance(held.value, (list, tuple)):
        return []
    return [d for d in held.value if isinstance(d, dict)]


def _library_url(device: DeviceFacts, name: str, title: str) -> str:
    """The URL a library-cited fact points at: its own (with #page=N), else its document's."""
    from app.reference import device_library

    documents = _documents(device)
    slug = next((str(d.get("slug") or "") for d in documents if d.get("slug")), "")
    entry = device_library.entry(slug) if slug else None
    if entry is not None:
        cited = device_library.citation(entry, name)
        if cited.get("url"):
            return str(cited["url"])
    return next((str(d.get("url") or "") for d in documents
                 if title and str(d.get("title") or "") == title), "")


def source_link(device: DeviceFacts, name: str, held) -> SourceLink | None:
    """The fact's note as a link: a URL as itself, "title, p.N" to its page.

    Any other note (who recorded an answer and when) is shown as text.
    Returns None for a fact with no note.
    """
    note = (held.note or "").strip()
    if not held.known or not note:
        return None
    if note.startswith(("http://", "https://")):
        return SourceLink(text=note, url=note, tag=held.source.value)
    cited, _, aside = note.partition("; ")
    title = cited.rsplit(", p.", 1)[0] if ", p." in cited else cited
    url = _library_url(device, name, title)
    if url:
        return SourceLink(text=cited, url=url, aside=aside.strip(), tag=held.source.value)
    return SourceLink(text=note, tag=held.source.value)


def _rule_link(country: str) -> SourceLink | None:
    from app.reference.import_rules import rule_for

    rule = rule_for(country) if country else None
    source = (rule or {}).get("source") if isinstance(rule, dict) else None
    if not source or not source.get("url"):
        return None
    pages = str(source.get("pages") or "")
    first = pages.split("-", 1)[0].strip()
    url = str(source["url"]) + (f"#page={first}" if first.isdigit() and "#" not in source["url"] else "")
    text = str(source.get("title") or "import rule") + (f", pp.{pages}" if pages else "")
    return SourceLink(text=text, url=url, tag="import rule")


def check_rows(verdict, device: DeviceFacts, intel, country: str = "") -> list[dict]:
    """Every check result with the citations of the facts it read.

    One dict per result: `result` (the CheckResult) and `links` (SourceLink,
    deduplicated, in fact order). The support row carries the registry
    record when the GUDID affirmed support, whatever decided the row.
    """
    rows = []
    for result in verdict.results:
        links: list[SourceLink] = []
        for where, name in CHECK_FACTS.get(result.key, ()):
            holder = device if where == "device" else intel
            held = getattr(holder, name, None)
            if held is None:
                continue
            link = source_link(device, name, held)
            if link is not None and all((l.text, l.url) != (link.text, link.url) for l in links):
                links.append(link)
        if result.key == "import":
            rule = _rule_link(country)
            if rule is not None:
                links.insert(0, rule)
        rows.append({"result": result, "links": links})
    return rows


def open_questions(verdict, device: DeviceFacts, intel, device_type: str,
                   country: str, back: str = "") -> list[dict]:
    """The verdict's open questions, each once; supply ones link to /supply.

    A dict per question: `text`, `url` (the /supply form, or "") and `who`
    ("the nonprofit" or "the donor").
    """
    model = str(device.model.value) if device.model.known else ""
    seen: dict[str, dict] = {}
    for result in verdict.unknowns:
        text = result.question or result.detail
        if not text or text in seen:
            continue
        field = SUPPLY_QUESTION_FIELDS.get(result.key)
        asks_country = field is not None and not getattr(intel, field).known and (
            result.key != "consumables" or device.consumables.known)
        url = supply_url(country, device_type, model, back) if asks_country and country else ""
        seen[text] = {"text": text, "url": url,
                      "who": "the nonprofit" if url else "the donor"}
    return list(seen.values())


def offer_name(offer) -> str:
    """The offer's device name; "an unnamed <type>" when neither maker nor model is known."""
    device = offer.device
    if device.model.known or device.manufacturer.known:
        return device.label
    kind = (offer.device_type or "device").strip()
    return f"Unnamed {kind}"


def addon_states(bundle) -> list[dict]:
    """Each send-with item with its offers in the three self-check states (§2.4).

    `covers` (counts), `rejected` ("won't work here itself") and `unverified`
    (listed, not counting). Non-power add-ons are never self-checked and
    their offers cover as found.
    """
    items = []
    for item in getattr(bundle, "items", ()):
        items.append({
            "item": item,
            "covers": list(item.offers),
            "rejected": list(item.rejected_offers),
            "unverified": list(item.unverified_offers),
        })
    return items
