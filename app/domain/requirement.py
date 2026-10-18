"""What must ship alongside a device for it to actually work.

A check that returns MITIGABLE says what would fix it in prose. That prose is
for a human; this is the same fact in a shape the inventory can be searched
with, so "needs a voltage stabiliser" can become "three donors have one".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RequirementKind(str, Enum):
    TRANSFORMER = "transformer"
    STABILISER = "voltage stabiliser"
    BACKUP_POWER = "backup power"
    COOLING = "cooling"
    DEHUMIDIFIER = "dehumidifier"
    AIR_COMPRESSOR = "medical air compressor"
    OXYGEN_SUPPLY = "oxygen supply"
    WATER_SUPPLY = "distilled water supply"
    CONSUMABLES = "consumables"
    SPARE_PARTS = "spare parts"
    TRAINING = "training and service"
    MANUAL = "handover packet"


# What a donor might call each kind, used to match an offer to a requirement
# when no model has categorised it. Lower case, matched as substrings.
SEARCH_TERMS: dict[RequirementKind, tuple[str, ...]] = {
    RequirementKind.TRANSFORMER: ("transformer", "step-down", "step up", "step-up"),
    RequirementKind.STABILISER: ("stabiliser", "stabilizer", "avr", "voltage regulator"),
    RequirementKind.BACKUP_POWER: ("ups", "generator", "battery backup", "inverter"),
    RequirementKind.DEHUMIDIFIER: ("dehumidifier",),
    RequirementKind.CONSUMABLES: ("consumable", "circuit", "filter", "reagent", "cartridge"),
    RequirementKind.SPARE_PARTS: ("spare part", "parts kit", "service kit"),
    RequirementKind.TRAINING: ("training", "service contract"),
    RequirementKind.MANUAL: ("manual", "documentation"),
}

# What ships in the container and plugs in. Only these are add-ons: a verdict
# that lists one says "send this with it", and the bundle looks for it.
ADD_ONS = frozenset(
    {
        RequirementKind.TRANSFORMER,
        RequirementKind.STABILISER,
        RequirementKind.BACKUP_POWER,
        RequirementKind.DEHUMIDIFIER,
        RequirementKind.CONSUMABLES,
        RequirementKind.SPARE_PARTS,
        RequirementKind.MANUAL,
    }
)

# Changing the building or its utilities. Nobody ships these in a crate, so a
# check that would need one fails and says what the clinic lacks. They have
# no search terms: the inventory is never searched for a building works job.
SITE_WORKS = frozenset(
    {
        RequirementKind.COOLING,
        RequirementKind.OXYGEN_SUPPLY,
        RequirementKind.WATER_SUPPLY,
        RequirementKind.AIR_COMPRESSOR,
    }
)

# The add-ons with a rating plate of their own, so the bundle can check that
# the add-on itself runs on the clinic's mains before counting it as a fix.
POWER_ADD_ONS = frozenset(
    {
        RequirementKind.TRANSFORMER,
        RequirementKind.STABILISER,
        RequirementKind.BACKUP_POWER,
        RequirementKind.DEHUMIDIFIER,
    }
)

# Requirements no donor can ship in a crate. Listed so the bundle can say so
# rather than searching the inventory and silently finding nothing.
NOT_SHIPPABLE = frozenset({RequirementKind.TRAINING, RequirementKind.MANUAL})


@dataclass(frozen=True)
class Requirement:
    """One thing that must accompany a device, tied to the check that found it."""

    kind: RequirementKind
    detail: str
    from_check: str = ""

    @property
    def shippable(self) -> bool:
        return self.kind not in NOT_SHIPPABLE

    @property
    def search_terms(self) -> tuple[str, ...]:
        return SEARCH_TERMS.get(self.kind, ())
