"""Ask the model what can be bought and serviced in the recipient's country."""

from __future__ import annotations

from typing import Any

from app.ai.client import CLIENT, GenAiClient
from app.ai.prompts import RESEARCH_SYSTEM, RESEARCH_USER
from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.provenance import UNKNOWN, Provenance, fact


def research_supply(
    device: DeviceFacts, country: str, *, client: GenAiClient = CLIENT
) -> SupplyIntel:
    """World knowledge, clearly labelled as such so the UI can show it."""
    consumables = device.consumables.value if device.consumables.known else []
    named = ", ".join(consumables) if consumables else "none listed"

    payload = client.complete_json(
        RESEARCH_USER.format(
            device=device.label, consumables=named, country=country
        ),
        system=RESEARCH_SYSTEM,
    )
    return _to_intel(payload, country)


def _flag(payload: dict[str, Any], key: str):
    value = payload.get(key)
    if value is None:
        return UNKNOWN
    return fact(bool(value), Provenance.MODEL_KNOWLEDGE)


def _to_intel(payload: dict[str, Any], country: str) -> SupplyIntel:
    return SupplyIntel(
        country=country,
        consumables_available=_flag(payload, "consumables_available"),
        spare_parts_available=_flag(payload, "spare_parts_available"),
        manufacturer_supported=_flag(payload, "manufacturer_supported"),
        reasoning=str(payload.get("reasoning", "")).strip(),
    )
