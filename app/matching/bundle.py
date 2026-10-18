"""Turn a verdict's requirements into things a clinic can actually obtain.

A check says "this needs a voltage stabiliser". On its own that is advice.
Searching the open offers for one turns it into "and three donors have one",
which is the difference between a warning and a working machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from app.domain.facility import FacilityProfile
from app.domain.marketplace import Offer
from app.domain.requirement import Requirement
from app.domain.result import Verdict

OfferFinder = Callable[[str, tuple[str, ...]], Iterable[Offer]]


def no_offers(kind_value: str, terms: tuple[str, ...]) -> list[Offer]:
    """Default finder, so a bundle can be built without a database."""
    return []


@dataclass(frozen=True)
class BundleItem:
    """One requirement and whatever the inventory can offer against it."""

    requirement: Requirement
    offers: tuple[Offer, ...] = ()
    # Power add-on offers whose own plate fails at this clinic's mains.
    rejected_offers: tuple[Offer, ...] = ()
    # Power add-on offers whose plate cannot be checked here yet.
    unverified_offers: tuple[Offer, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.offers)

    @property
    def total_units(self) -> int:
        return sum(o.quantity_available for o in self.offers)

    @property
    def status(self) -> str:
        if not self.requirement.shippable:
            return "arranged, not shipped"
        return "available" if self.available else "nobody has one"


@dataclass(frozen=True)
class Bundle:
    """Everything that must accompany a device, and where it might come from."""

    items: tuple[BundleItem, ...] = ()

    @property
    def shippable(self) -> tuple[BundleItem, ...]:
        return tuple(i for i in self.items if i.requirement.shippable)

    @property
    def covered(self) -> tuple[BundleItem, ...]:
        return tuple(i for i in self.shippable if i.available)

    @property
    def gaps(self) -> tuple[BundleItem, ...]:
        return tuple(i for i in self.shippable if not i.available)

    @property
    def complete(self) -> bool:
        return bool(self.shippable) and not self.gaps

    @property
    def summary(self) -> str:
        if not self.items:
            return "Nothing needs to ship alongside it."
        if not self.shippable:
            return "What it needs cannot be shipped, only arranged."
        if self.complete:
            return f"All {len(self.shippable)} items can be sourced from open offers."
        return f"{len(self.covered)} of {len(self.shippable)} items can be sourced."


def build_bundle(
    verdict: Verdict, facility: FacilityProfile, find_offers: OfferFinder = no_offers
) -> Bundle:
    """Search the inventory once per requirement this verdict raised.

    `facility` is the clinic the add-ons must run at.
    """
    items = []
    for requirement in verdict.needs:
        offers = ()
        if requirement.shippable:
            offers = tuple(find_offers(requirement.kind.value, requirement.search_terms))
        items.append(BundleItem(requirement=requirement, offers=offers))
    return Bundle(items=tuple(items))
