"""openFDA GUDID lookup: what the US device registry says about a model.

This is the one module here that reaches the network, and it is written so
that the network never matters. Every failure path returns None and logs, so a
clinic running the platform on a dead link sees "unknown", never a crash and
never a wrong answer.

Endpoint: https://api.fda.gov/device/udi.json -- no API key, 1000 requests/day.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

API_URL = "https://api.fda.gov/device/udi.json"
REQUEST_TIMEOUT_SECONDS = 30
RESULT_LIMIT = 1
USER_AGENT = "fitcheck-reference/1.0"

IN_DISTRIBUTION = "In Commercial Distribution"
NOT_IN_DISTRIBUTION = "Not in Commercial Distribution"

# openFDA spells units out in full on the storage entries.
CELSIUS_UNITS = frozenset({"degrees celsius", "celsius", "c"})
FAHRENHEIT_UNITS = frozenset({"degrees fahrenheit", "fahrenheit", "f"})
FAHRENHEIT_OFFSET = 32.0
FAHRENHEIT_RATIO = 5.0 / 9.0

# The storage entry that carries a real numeric range, as opposed to the
# free-text "Special Storage Condition, Specify" rows.
TEMPERATURE_TYPE_HINT = "temperature"

# Tried in order; the model number is far more specific than the description.
SEARCH_FIELDS = ("version_or_model_number", "device_description")


@dataclass(frozen=True)
class DeviceRegistryRecord:
    """One GUDID entry, flattened to the fields a donation decision needs."""

    brand_name: str | None = None
    company_name: str | None = None
    version_or_model_number: str | None = None
    gmdn_name: str | None = None
    gmdn_code: str | None = None
    commercial_distribution_status: str | None = None
    commercial_distribution_end_date: str | None = None
    storage_temp_low_c: float | None = None
    storage_temp_high_c: float | None = None


def _clean(value: Any) -> str | None:
    """Keep only non-empty strings; the registry uses '' and null alike."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _to_celsius(raw_value: Any, raw_unit: Any) -> float | None:
    """Read one {'unit': ..., 'value': ...} pair, in C or F. None if unusable."""
    text = _clean(raw_value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        logger.debug("Unparseable storage temperature %r", raw_value)
        return None
    unit = (_clean(raw_unit) or "").lower()
    if unit in CELSIUS_UNITS:
        return number
    if unit in FAHRENHEIT_UNITS:
        return round((number - FAHRENHEIT_OFFSET) * FAHRENHEIT_RATIO, 1)
    logger.debug("Unrecognised storage temperature unit %r", raw_unit)
    return None


def _bound(entry: dict[str, Any], key: str) -> float | None:
    bound = entry.get(key)
    if not isinstance(bound, dict):
        return None
    return _to_celsius(bound.get("value"), bound.get("unit"))


def _storage_range(result: dict[str, Any]) -> tuple[float | None, float | None]:
    """Pull a numeric storage range out of `storage`. Most records have none.

    The field is `storage` (a list), not `storage_and_handling`, and every
    level of it is optional, so each access is guarded.
    """
    entries = result.get("storage")
    if not isinstance(entries, list):
        return (None, None)
    fallback: tuple[float | None, float | None] = (None, None)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        low, high = _bound(entry, "low"), _bound(entry, "high")
        if low is None and high is None:
            continue
        entry_type = (_clean(entry.get("type")) or "").lower()
        if TEMPERATURE_TYPE_HINT in entry_type:
            return (low, high)
        if fallback == (None, None):
            fallback = (low, high)
    return fallback


def _gmdn(result: dict[str, Any]) -> tuple[str | None, str | None]:
    terms = result.get("gmdn_terms")
    if not isinstance(terms, list):
        return (None, None)
    for term in terms:
        if not isinstance(term, dict):
            continue
        name, code = _clean(term.get("name")), _clean(term.get("code"))
        if name or code:
            return (name, code)
    return (None, None)


def _to_record(result: dict[str, Any]) -> DeviceRegistryRecord:
    gmdn_name, gmdn_code = _gmdn(result)
    low, high = _storage_range(result)
    return DeviceRegistryRecord(
        brand_name=_clean(result.get("brand_name")),
        company_name=_clean(result.get("company_name")),
        version_or_model_number=_clean(result.get("version_or_model_number")),
        gmdn_name=gmdn_name,
        gmdn_code=gmdn_code,
        commercial_distribution_status=_clean(
            result.get("commercial_distribution_status")
        ),
        commercial_distribution_end_date=_clean(
            result.get("commercial_distribution_end_date")
        ),
        storage_temp_low_c=low,
        storage_temp_high_c=high,
    )


def _fetch(field: str, term: str) -> list[dict[str, Any]]:
    """One openFDA query. Returns [] for anything short of a usable answer."""
    query = urllib.parse.urlencode(
        {"search": f'{field}:"{term}"', "limit": RESULT_LIMIT}
    )
    url = f"{API_URL}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # 404 is openFDA's "no match", which is an answer, not a failure.
        level = logging.DEBUG if error.code == 404 else logging.WARNING
        logger.log(level, "openFDA %s on %s:%r", error.code, field, term)
        return []
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        logger.warning("openFDA unreachable for %s:%r (%s)", field, term, error)
        return []
    except (ValueError, UnicodeDecodeError) as error:
        logger.warning("openFDA sent unreadable JSON for %s:%r (%s)", field, term, error)
        return []

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def lookup_model(model_or_brand: str) -> DeviceRegistryRecord | None:
    """Find a GUDID entry by model number, then by description. None on a miss.

    Never raises on a network failure: offline, this returns None like any
    other miss, and the caller carries on with the fact marked unknown.
    """
    term = (model_or_brand or "").strip()
    if not term:
        return None
    for field in SEARCH_FIELDS:
        results = _fetch(field, term)
        if results:
            return _to_record(results[0])
    return None


def is_still_supported(record: DeviceRegistryRecord | None) -> bool | None:
    """Is the model still in commercial distribution? None when unknown."""
    if record is None:
        return None
    status = record.commercial_distribution_status
    if status == IN_DISTRIBUTION:
        return True
    if status == NOT_IN_DISTRIBUTION:
        return False
    return None
