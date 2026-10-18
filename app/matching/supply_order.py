"""Where the supply facts for one device in one country come from, in order.

1. The FDA's registry (GUDID): it can affirm that the manufacturer still
   supports the model. It never denies it.
2. What the nonprofit recorded for this model in this country, else for this
   device type in this country (`supply_answers`).
3. Otherwise unknown, and the supply checks ask.

Each source fills only the fields still unknown. Every fact names its source
in `note`: the registry record URL, or who recorded the answer and when. The
model's memory is never one of the sources.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable

from app.data import marketplace_repo as repo
from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.provenance import Provenance, fact
from app.domain.supply_answer import SupplyAnswer
from app.reference import gudid
from app.reference.gudid import RegistryResult

LOG = logging.getLogger(__name__)

RegistryCheck = Callable[[str, str], RegistryResult]
AnswerLoader = Callable[[str, str, str], "SupplyAnswer | None"]

# The SupplyIntel fields a recorded answer can fill, in the answer's names.
RECORDED_FIELDS = (
    "consumables_available",
    "spare_parts_available",
    "sold_locally",
    "manufacturer_supported",
)


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def recorded_note(answer: SupplyAnswer) -> str:
    """'recorded by <who>, <date>', with whichever half is known."""
    who = answer.recorded_by.strip() or "the nonprofit"
    when = answer.recorded_at.date().isoformat() if answer.recorded_at else ""
    return f"recorded by {who}, {when}" if when else f"recorded by {who}"


def _from_registry(intel: SupplyIntel, device: DeviceFacts, registry: RegistryCheck) -> SupplyIntel:
    model = _text(device.model.value)
    if not model or intel.manufacturer_supported.known:
        return intel
    result = registry(_text(device.manufacturer.value), model)
    if result.supported is not True:
        return intel
    note = result.record_url or f"FDA GUDID record for {model}"
    return replace(intel, manufacturer_supported=fact(True, Provenance.REGISTRY, note))


def _from_answer(intel: SupplyIntel, answer: SupplyAnswer | None) -> SupplyIntel:
    if answer is None:
        return intel
    note = recorded_note(answer)
    filled = {
        name: fact(getattr(answer, name), Provenance.RECORDED, note)
        for name in RECORDED_FIELDS
        if not getattr(intel, name).known and getattr(answer, name) is not None
    }
    return replace(intel, **filled) if filled else intel


def supply_lookup(
    device: DeviceFacts, device_type: str, country: str, *,
    registry: RegistryCheck | None = None, recorded: AnswerLoader | None = None,
) -> SupplyIntel:
    """The IntelLookup the routes use: registry, then recorded answer, then unknown.

    `registry` and `recorded` default to the GUDID client and the repo
    loader; tests pass fakes. A storage failure reads as "no answer
    recorded", so a lookup never breaks a screening.
    """
    registry = registry or gudid.lookup_support
    recorded = recorded or repo.load_supply_answer

    intel = _from_registry(SupplyIntel(country=country), device, registry)
    try:
        answer = recorded(device_type or "", _text(device.model.value), country)
    except repo.RepositoryError as exc:
        LOG.warning("recorded supply answer unreadable: %s", exc)
        answer = None
    return _from_answer(intel, answer)
