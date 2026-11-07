"""List the supply answers and holds the seed didn't write, and which move the seeded demo.

Read-only: it selects and screens, and writes nothing. Run it on the VM from the
live checkout once that checkout has app/data/seeded.py (after the redeploy):

    ssh opc@<vm> 'cd /opt/fitcheck && ./.venv/bin/python -' < deploy/visitor_rows.py

or from a local checkout, against the database its .env names, with
`.venv/bin/python - < deploy/visitor_rows.py`.

With DEMO_PROTECT_SEED on, no visitor can add to either list, so run it with the
setting on and the list is final. It prints:

- the verdict each seeded offer gets for the seeded Kayunga request now, and with
  only the seed's own supply answers;
- each supply answer the seed didn't write, and the seeded verdicts it changes on
  its own (added to the seed's answers);
- each match (hold) on a seeded offer or on the seeded request, with its state.

Removing any of them is a separate, deliberate step; this script never does it.
Verdicts are screened with the registry cache only, so nothing calls the FDA and
no cache row is written.
"""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import select

from app.data import marketplace_repo as repo
from app.data import seeded
from app.data.db import get_engine
from app.data.marketplace_schema import matches as matches_table
from app.data.marketplace_schema import supply_answers as answers_table
from app.domain.marketplace import MatchState
from app.domain.supply_answer import ANY_MODEL, SupplyAnswer
from app.matching.pipeline import screen_offers
from app.matching.supply_order import supply_lookup
from app.reference import gudid
from scripts import seed_demo

# A match in one of these keeps its units off the offer.
HOLDING = {MatchState.PROPOSED, MatchState.AWAITING_DONOR, MatchState.CONFIRMED}


def _same(left: str, right: str) -> bool:
    return " ".join((left or "").split()).lower() == " ".join((right or "").split()).lower()


def _from_seed(answer: SupplyAnswer) -> bool:
    """True when the row is exactly one the seed writes, values and all."""
    fields = ("device_type", "model", "country", "recorded_by", "note", *(
        "consumables_available", "spare_parts_available", "sold_locally", "manufacturer_supported"))
    return any(all(_same(str(getattr(answer, f)), str(getattr(mine, f))) for f in fields)
               for mine in seed_demo.KAYUNGA_ANSWERS)


def loader(answers: list[SupplyAnswer]):
    """repo.load_supply_answer over a list in memory: model first, newest first, then type."""
    newest = sorted(answers, key=lambda a: a.id or 0, reverse=True)

    def load(device_type: str, model: str, country: str) -> SupplyAnswer | None:
        here = [a for a in newest if _same(a.country, country)]
        if " ".join((model or "").split()):
            for answer in here:
                if _same(answer.model, model):
                    return answer
        if not " ".join((device_type or "").split()):
            return None
        for answer in here:
            if answer.model == ANY_MODEL and _same(answer.device_type, device_type):
                return answer
        return None

    return load


def cached_registry(manufacturer: str, model: str):
    """GUDID from the cache only: no live call, no cache write."""
    payload = gudid._cached(model) if (model or "").strip() else None
    if payload is None or payload is gudid.FAILED_MARKER:
        return gudid.RegistryResult(status=gudid.NOT_LOOKED_UP)
    return gudid.read_payload(manufacturer, model, payload)


def seeded_offers() -> list:
    """Every stored seeded offer, with its holds set aside, so a held one still screens."""
    stored = [offer for group in seed_demo._stored_offers_by_key().values() for offer in group]
    return [replace(offer, quantity_reserved=0) for offer in stored if seeded.is_seeded_offer(offer)]


def verdicts(request, facility, offers, answers) -> dict[int, str]:
    load = loader(answers)

    def intel(device, device_type, country):
        return supply_lookup(device, device_type, country, registry=cached_registry, recorded=load)

    found: dict[int, str] = {}
    for line in request.lines:
        wanted = [o for o in offers if repo._type_matches(line.device_type, o.device_type)]
        screening = screen_offers(facility, wanted, intel_for=intel)
        for candidate in (*screening.ranked, *screening.rejected):
            found[candidate.offer.id] = candidate.verdict.outcome.value
    return found


def _changes(before: dict[int, str], after: dict[int, str]) -> list[str]:
    return [f"offer {offer_id} {before.get(offer_id, '-')} -> {after.get(offer_id, '-')}"
            for offer_id in sorted(set(before) | set(after))
            if before.get(offer_id) != after.get(offer_id)]


def main() -> int:
    request_id = seeded.kayunga_request_id()
    if request_id is None:
        print("!! no seeded Kayunga request in this database")
        return 1
    request = repo.load_request(request_id)
    facility = repo.load_facility(request.facility_slug)
    offers = seeded_offers()
    with get_engine().connect() as conn:
        answers = [repo._answer_from_row(row._mapping)
                   for row in conn.execute(select(answers_table).order_by(answers_table.c.id))]
        holds = [repo._match_from_row(row._mapping)
                 for row in conn.execute(select(matches_table).order_by(matches_table.c.created_at))]

    seed_only = [a for a in answers if _from_seed(a)]
    visitors = [a for a in answers if not _from_seed(a)]
    base = verdicts(request, facility, offers, seed_only)
    now = verdicts(request, facility, offers, answers)
    print(f"request {request_id}: Kayunga, {len(offers)} seeded offers")
    for offer in offers:
        if offer.id in base:
            print(f"  offer {offer.id} {offer.device.label}: {now.get(offer.id)}"
                  f" (seed's answers alone: {base[offer.id]})")
    print(f"changed from the seed's answers alone: {'; '.join(_changes(base, now)) or 'nothing'}")

    print(f"supply answers the seed didn't write: {len(visitors)}")
    for answer in visitors:
        moved = _changes(base, verdicts(request, facility, offers, [*seed_only, answer]))
        print(f"  answer {answer.id}: {answer.device_type or '-'} / {answer.model or 'any model'}"
              f" / {answer.country}, by {answer.recorded_by or '?'}"
              f" at {answer.recorded_at}: {'CHANGES ' + '; '.join(moved) if moved else 'changes no seeded verdict'}")

    seeded_ids = {offer.id for offer in offers}
    touching = [m for m in holds if m.offer_id in seeded_ids or m.request_id == request_id]
    print(f"holds on a seeded offer or the seeded request: {len(touching)}")
    for match in touching:
        holding = match.state in HOLDING
        print(f"  match {match.id}: offer {match.offer_id} for request {match.request_id},"
              f" {match.quantity} unit(s), {match.state.value}, made {match.created_at}"
              f"{' - HOLDS STOCK' if holding else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
