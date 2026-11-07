"""Which writes DEMO_PROTECT_SEED refuses on the shared demo.

With the setting on, no visitor changes a row the seed wrote, or the verdict
a seeded offer gets for the seeded request. So these are refused: asking for
a seeded offer or on the seeded request, answering a hold of a seeded offer,
reviewing a seeded clinic, rebuilding a seeded offer's packet, and every
supply-desk save. The supply lookup falls back from a model to its type and
takes the newest answer, so any saved answer could move a seeded verdict;
the whole desk is closed, not only the seeded keys.

Visitors' own offers, requests and clinics work as usual. With the setting
off (the default) every check here answers False.

The routes call these and answer with shared._refused; the pages that hold
the buttons read them to show a disabled button and SEEDED_MESSAGE instead.
"""

from __future__ import annotations

from app.config import demo_protect_seed
from app.data import marketplace_repo as repo
from app.data import seeded
from app.domain.marketplace import MatchRecord, Offer

SEEDED_MESSAGE = (
    "This is seeded demo data and stays as it is. "
    "Register your own clinic or post your own offer to try this."
)


def protecting() -> bool:
    return demo_protect_seed()


def offer_locked(offer: Offer | None) -> bool:
    return protecting() and offer is not None and seeded.is_seeded_offer(offer)


def request_locked(request_id: int) -> bool:
    """The seeded Kayunga request. Unreadable requests count as locked."""
    if not protecting():
        return False
    try:
        return seeded.is_seeded_request(request_id)
    except repo.RepositoryError:
        return True


def match_locked(match: MatchRecord) -> bool:
    """A hold of a seeded offer, made before the setting went on."""
    return protecting() and offer_locked(repo.load_offer(match.offer_id))


def clinic_locked(slug: str) -> bool:
    return protecting() and seeded.is_seeded_clinic(slug)


def supply_locked() -> bool:
    return protecting()
