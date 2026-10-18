"""Does the FDA's device registry (GUDID) say a model is still on sale?

One question, asked of openFDA's UDI endpoint: is this model "In Commercial
Distribution" under a company whose name carries the manufacturer's first
word? A yes affirms that the manufacturer still supports the model. Anything
else (another status, no record, a timeout, an error) leaves the answer
unknown and never turns into a no, because GUDID covers one market: a model
withdrawn from the US can still be sold and serviced elsewhere (the 525KS is
exactly that).

Answers are cached in `registry_lookups` for seven days, so the demo never
waits on the FDA twice. Errors are not cached: a timeout tonight should not
hide a record tomorrow.

This module never raises. Every failure path returns a result with
`supported=None` and logs why.

Under pytest the live call is off unless GUDID_LOOKUP=on is set, so no test
reaches the network by accident through a route; tests that exercise the
fetch pass their own `fetch`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app.data import marketplace_repo as repo

LOG = logging.getLogger(__name__)

API_URL = "https://api.fda.gov/device/udi.json"
RECORD_URL = "https://accessgudid.nlm.nih.gov/devices/{di}"
RESULT_LIMIT = 5
TIMEOUT_SECONDS = 5
CACHE_DAYS = 7
USER_AGENT = "fit-check/0.1 (medical equipment donation screening)"

IN_DISTRIBUTION = "In Commercial Distribution"

# Statuses this module writes itself, beside GUDID's own distribution statuses.
NO_MODEL = "no model"
NO_RECORD = "no record"
NOT_LOOKED_UP = "not looked up"
FAILED = "lookup failed"

#: fetch(url, params, timeout) -> (HTTP status code, parsed JSON body)
Fetch = Callable[[str, dict[str, Any], float], tuple[int, Any]]


@dataclass(frozen=True)
class RegistryResult:
    """What GUDID said about one model, for one manufacturer.

    `supported` is True only on an in-distribution record from the right
    company, and None otherwise. It is never False.
    """

    status: str
    record_url: str = ""
    supported: bool | None = None
    raw: Any = field(default_factory=dict)


def _squash(text: str) -> str:
    """Lower case with spaces and hyphens removed: '525 KS' and '525-ks' agree."""
    return re.sub(r"[\s\-]+", "", (text or "").lower())


def _first_word(manufacturer: str) -> str:
    words = re.findall(r"[a-z0-9]+", (manufacturer or "").lower())
    return words[0] if words else ""


def _record_url(record: dict) -> str:
    for ident in record.get("identifiers") or ():
        if ident.get("type") == "Primary" and ident.get("id"):
            return RECORD_URL.format(di=ident["id"])
    return ""


def read_payload(manufacturer: str, model: str, payload: Any) -> RegistryResult:
    """Judge an openFDA UDI response for this manufacturer and model.

    A record counts only when its model number equals the asked model (case,
    spaces and hyphens ignored), so a "525DS-Q" record says nothing about a
    "525DS", and when its company name contains the manufacturer's first
    word. Among counting records an in-distribution one wins.
    """
    results = payload.get("results") if isinstance(payload, dict) else None
    word = _first_word(manufacturer)
    wanted = _squash(model)
    records = [
        r for r in (results or ())
        if isinstance(r, dict)
        and _squash(str(r.get("version_or_model_number") or "")) == wanted
        and word and word in str(r.get("company_name") or "").lower()
    ]
    if not records:
        return RegistryResult(status=NO_RECORD, raw=payload)
    records.sort(key=lambda r: r.get("commercial_distribution_status") != IN_DISTRIBUTION)
    best = records[0]
    status = str(best.get("commercial_distribution_status") or NO_RECORD)
    return RegistryResult(
        status=status,
        record_url=_record_url(best),
        supported=True if status == IN_DISTRIBUTION else None,
        raw=payload,
    )


def _http_get(url: str, params: dict[str, Any], timeout: float) -> tuple[int, Any]:
    response = httpx.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": USER_AGENT})
    return response.status_code, response.json()


#: A failed lookup (timeout, connection error, 5xx, bad JSON) is remembered for
#: this long so a cold matches page does not wait on the FDA for every offer on
#: every load. It is short: a transient failure tonight should not hide a record
#: for a week.
FAILED_CACHE_MINUTES = 10


def live_enabled() -> bool:
    """GUDID_LOOKUP=off turns the live call off; under pytest it is off unless =on."""
    setting = os.getenv("GUDID_LOOKUP", "").strip().lower()
    if setting:
        return setting not in ("off", "0", "false", "no")
    return "PYTEST_CURRENT_TEST" not in os.environ


def _cached(model: str) -> Any | None:
    """The cached GUDID payload, or FAILED_MARKER for a recent failure, or None."""
    try:
        stored = repo.cached_registry_lookup(model, CACHE_DAYS)
    except repo.RepositoryError as exc:
        LOG.warning("registry cache unreadable: %s", exc)
        return None
    if stored is None:
        return None
    if stored.status == FAILED:
        fetched = stored.fetched_at or datetime.now()
        if datetime.now() - fetched <= timedelta(minutes=FAILED_CACHE_MINUTES):
            return FAILED_MARKER
        return None
    try:
        return json.loads(stored.raw_json or "{}")
    except ValueError:
        return None


FAILED_MARKER = object()


def _store(model: str, result: RegistryResult) -> None:
    try:
        repo.store_registry_lookup(model, result.status, result.record_url, result.raw)
    except repo.RepositoryError as exc:
        LOG.warning("registry cache not written: %s", exc)


def lookup_support(
    manufacturer: str, model: str, *, timeout: float = TIMEOUT_SECONDS,
    fetch: Fetch | None = None,
) -> RegistryResult:
    """Ask GUDID whether this model is still in commercial distribution.

    Reads the seven-day cache first. The cache holds GUDID's whole answer
    for the model, so the manufacturer test is made again on every read.
    """
    model = (model or "").strip()
    if not model:
        return RegistryResult(status=NO_MODEL)

    payload = _cached(model)
    if payload is FAILED_MARKER:
        return RegistryResult(status=FAILED)
    if payload is not None:
        return read_payload(manufacturer, model, payload)

    if fetch is None:
        if not live_enabled():
            return RegistryResult(status=NOT_LOOKED_UP)
        fetch = _http_get

    # Quotes inside the model would end the phrase search early.
    phrase = model.replace('"', "")
    params = {"search": f'version_or_model_number:"{phrase}"', "limit": RESULT_LIMIT}
    try:
        code, payload = fetch(API_URL, params, timeout)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        LOG.warning("GUDID lookup for %r failed: %s", model, exc)
        failed = RegistryResult(status=FAILED)
        _store(model, failed)
        return failed

    if code == 404:
        # openFDA answers "No matches found!" with a 404; that is an answer.
        payload = {"results": []}
    elif code != 200 or not isinstance(payload, dict):
        LOG.warning("GUDID lookup for %r answered HTTP %s", model, code)
        failed = RegistryResult(status=FAILED)
        _store(model, failed)
        return failed

    result = read_payload(manufacturer, model, payload)
    _store(model, result)
    return result
