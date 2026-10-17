"""Typical consumables and utilities for a class of device.

Built from WHO-template procurement specifications published by NHSRC for the
Indian Ministry of Health, for neonatal care and operation theatre equipment.
The committed table is data/device_class.json.

Matching is EXACT on the normalised name and nothing else. Fuzzy matching was
tried and rejected: it paired "haemodialysis system" with "basic diagnostic
x-ray system", and a tool whose whole value is that it does not guess cannot
afford a confident wrong answer. A miss returns None.

Two things the source has that this module deliberately does not expose: the
voltage/frequency columns and the temperature/humidity columns. Those are one
country's procurement demands, near-identical on every sheet, and presenting
them as facts about a specific machine would be a fabrication.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DATA_PATH = PROJECT_ROOT / "data" / "device_class.json"
DEVICES_KEY = "devices"
NAME_KEY = "normalised_name"
WHITESPACE = re.compile(r"\s+")


class DeviceClassDataError(RuntimeError):
    """Raised when the committed device class table is missing or unreadable."""


@dataclass(frozen=True)
class DeviceClass:
    """What a class of device typically consumes and needs plumbed in.

    A utility flag is True only where the source specification shows the
    device needs that supply, and False only where it says outright that it
    needs none. Silence stays None, because "not mentioned" is not "no".
    """

    name: str
    typical_consumables: tuple[str, ...] = ()
    needs_oxygen: bool | None = None
    needs_water: bool | None = None
    needs_compressed_air: bool | None = None
    complexity: str | None = None


def normalise_name(gmdn_name: str) -> str:
    """Lowercase, collapse whitespace, drop trailing periods. Nothing else.

    This is normalisation, not matching: it makes " Infant  Warmer. " and
    "infant warmer" the same string without making any two different names
    the same string.
    """
    collapsed = WHITESPACE.sub(" ", gmdn_name).strip()
    return collapsed.rstrip(".").strip().lower()


def _as_flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _build(entry: Mapping[str, Any]) -> DeviceClass | None:
    name = _as_text(entry.get("name")) or _as_text(entry.get(NAME_KEY))
    if name is None:
        logger.warning("Skipping device class entry with no name: %r", entry)
        return None
    raw_consumables = entry.get("typical_consumables")
    consumables = (
        tuple(c.strip() for c in raw_consumables if isinstance(c, str) and c.strip())
        if isinstance(raw_consumables, list)
        else ()
    )
    return DeviceClass(
        name=name,
        typical_consumables=consumables,
        needs_oxygen=_as_flag(entry.get("needs_oxygen")),
        needs_water=_as_flag(entry.get("needs_water")),
        needs_compressed_air=_as_flag(entry.get("needs_compressed_air")),
        complexity=_as_text(entry.get("complexity")),
    )


@lru_cache(maxsize=1)
def _index() -> Mapping[str, DeviceClass]:
    """Load the table once, keyed by normalised name."""
    path = Path(DATA_PATH)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DeviceClassDataError(
            f"The device class table is missing at {path}. "
            "It ships with the repo; restore it from version control."
        ) from error
    except OSError as error:
        raise DeviceClassDataError(
            f"The device class table at {path} could not be read: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise DeviceClassDataError(
            f"The device class table at {path} is not valid JSON: {error}"
        ) from error

    entries = payload.get(DEVICES_KEY) if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise DeviceClassDataError(
            f"The device class table at {path} has no '{DEVICES_KEY}' list."
        )

    index: dict[str, DeviceClass] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            logger.warning("Skipping non-object device class entry: %r", entry)
            continue
        record = _build(entry)
        if record is None:
            continue
        key = _as_text(entry.get(NAME_KEY)) or record.name
        index.setdefault(normalise_name(key), record)
    return index


def lookup_class(gmdn_name: str) -> DeviceClass | None:
    """Find a device class by exact normalised name. None on anything else.

    There is no fallback. A near miss is a miss: see the module docstring.
    """
    if not gmdn_name or not gmdn_name.strip():
        return None
    return _index().get(normalise_name(gmdn_name))


def known_classes() -> tuple[str, ...]:
    """Every name that resolves, so a caller can offer them rather than guess."""
    return tuple(sorted(record.name for record in _index().values()))
