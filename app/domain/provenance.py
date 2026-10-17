"""Where a device fact came from, and a value carrying that label.

The whole design rests on this: a verdict is only as trustworthy as the
provenance of the facts under it, so every fact says where it came from and
an unknown fact stays unknown instead of being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Provenance(str, Enum):
    NAMEPLATE = "nameplate"
    SPEC_SHEET = "spec sheet"
    DONOR_STATED = "stated by the donor"
    FACILITY_STATED = "clinic profile"
    MODEL_KNOWLEDGE = "model knowledge"
    STANDARD_DEFAULT = "IEC 60601-1 default"
    UNKNOWN = "unknown"


TRUSTED_SOURCES = frozenset(
    {
        Provenance.NAMEPLATE,
        Provenance.SPEC_SHEET,
        Provenance.DONOR_STATED,
        Provenance.FACILITY_STATED,
    }
)


@dataclass(frozen=True)
class Fact:
    """A single device fact plus the label saying how much to trust it."""

    value: Any = None
    source: Provenance = Provenance.UNKNOWN
    note: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None

    @property
    def trusted(self) -> bool:
        return self.known and self.source in TRUSTED_SOURCES

    def __str__(self) -> str:
        if not self.known:
            return "unknown"
        return f"{self.value} ({self.source.value})"


UNKNOWN = Fact()


def fact(value: Any, source: Provenance, note: str = "") -> Fact:
    """Build a Fact, collapsing a None value to UNKNOWN so sources stay honest."""
    if value is None:
        return UNKNOWN
    return Fact(value=value, source=source, note=note)
