"""Stored records the overnight round caches: registry lookups, packets, extractions.

Plain values handed out by app/data/marketplace_repo.py, so no caller sees a
row or a JSON blob.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.device import DeviceFacts


@dataclass(frozen=True)
class RegistryLookup:
    """One cached answer from a public device registry for one model."""

    model: str
    status: str
    record_url: str = ""
    raw_json: str = "{}"
    fetched_at: datetime | None = None


@dataclass(frozen=True)
class StoredPacket:
    """A built handover packet: the English card and, if any, its translation."""

    offer_id: int
    facility_slug: str
    language: str
    card: dict[str, Any] = field(default_factory=dict)
    translated: dict[str, Any] | None = None
    built_at: datetime | None = None


@dataclass(frozen=True)
class StoredExtraction:
    """Facts extracted for a donor, held until they confirm the offer."""

    id: str
    facts: DeviceFacts
    document: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
