"""All marketplace database access, behind named operations.

Same shape as app/data/repository.py: module-level functions, one error type,
every database call wrapped. Domain objects go in and come back out, so no
caller outside this module ever sees a row or a JSON blob.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, insert, or_, select, update

from app.data.db import get_engine
from app.data.marketplace_schema import (
    EXTRACTION_ID_LENGTH,
    MATCH_ID_LENGTH,
    extractions,
    facilities_v2,
    matches,
    offers,
    packets,
    registry_lookups,
    request_lines,
    requests,
    supply_answers,
)
from app.data.serialize import (
    device_from_dict,
    device_to_dict,
    intel_from_dict,
    intel_to_dict,
    verdict_from_dict,
    verdict_to_dict,
)
from app.domain.device import DeviceFacts
from app.domain.facility import FacilityProfile
from app.domain.facility_trust import Verification, VerificationStatus
from app.domain.marketplace import (
    OPEN_STATES,
    GeoPoint,
    MatchRecord,
    MatchState,
    Offer,
    OfferStatus,
    Request,
    RequestLine,
    RequestStatus,
)
from app.domain.records import RegistryLookup, StoredExtraction, StoredPacket
from app.domain.supply_answer import ANY_MODEL, SupplyAnswer

NO_STOCK = 0
MINIMUM_SWEEP_HOURS = 0
LIKE_ESCAPE = "\\"
LIKE_WILDCARDS = ("%", "_")
EMPTY_JSON_OBJECT = "{}"

# FacilityProfile holds these as tuples; JSON hands them back as lists.
FACILITY_TUPLE_FIELDS = ("plug_types", "staff_languages")
FACILITY_FIELD_NAMES = frozenset(f.name for f in fields(FacilityProfile))

REGISTRY_CACHE_DAYS = 7
EXTRACTION_TTL = timedelta(hours=1)

OPEN_MATCH_STATES = tuple(state.value for state in OPEN_STATES)


class RepositoryError(RuntimeError):
    """Any storage failure, with a message safe to show a user."""


def _first_row(statement: Any, failure: str) -> Any:
    try:
        with get_engine().connect() as conn:
            return conn.execute(statement).mappings().first()
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"{failure}: {exc}") from exc


def _all_rows(statement: Any, failure: str) -> list[Any]:
    try:
        with get_engine().connect() as conn:
            return list(conn.execute(statement).mappings().all())
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"{failure}: {exc}") from exc


def _write(statement: Any, failure: str, params: Any = None) -> None:
    """Run a write. With params this is one executemany, not a loop of inserts."""
    try:
        with get_engine().begin() as conn:
            conn.execute(statement) if params is None else conn.execute(statement, params)
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"{failure}: {exc}") from exc


def _write_returning_id(statement: Any, failure: str) -> int:
    try:
        with get_engine().begin() as conn:
            return int(conn.execute(statement).inserted_primary_key[0])
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"{failure}: {exc}") from exc


def _new_match_id() -> str:
    return uuid.uuid4().hex[:MATCH_ID_LENGTH]


def _contains_clause(column: Any, needle: str) -> Any:
    """Case-insensitive substring match that survives % and _ inside the needle."""
    pattern = needle.lower().replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    for wildcard in LIKE_WILDCARDS:
        pattern = pattern.replace(wildcard, LIKE_ESCAPE + wildcard)
    return func.lower(column).like(f"%{pattern}%", escape=LIKE_ESCAPE)


def _type_matches(wanted: str, stored: str) -> bool:
    """True when either device type contains the other.

    A request for "ventilator" should find an offer typed "transport
    ventilator", and a request for "transport ventilator" should still find a
    plainly typed "ventilator". Done in Python because the SQL for a
    bidirectional match is not portable between SQLite and MySQL.
    """
    left, right = wanted.strip().lower(), stored.strip().lower()
    if not left or not right:
        return False
    return left in right or right in left


# --- Facilities -----------------------------------------------------------


def _facility_values(profile: FacilityProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "country": profile.country,
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "profile_json": json.dumps(asdict(profile)),
    }


def _facility_from_row(row: Any) -> FacilityProfile:
    payload = dict(json.loads(row["profile_json"]))
    for name in FACILITY_TUPLE_FIELDS:
        payload[name] = tuple(payload.get(name) or ())
    return FacilityProfile(**{k: v for k, v in payload.items() if k in FACILITY_FIELD_NAMES})


def load_verification(slug: str) -> VerificationStatus:
    """How far this facility's claim has been checked. Unknown slugs read as self-declared."""
    row = _first_row(
        select(facilities_v2.c.verification, facilities_v2.c.verification_note)
        .where(facilities_v2.c.slug == slug),
        "Could not load the verification status",
    )
    if row is None:
        return VerificationStatus()
    try:
        tier = Verification(row["verification"])
    except ValueError:
        tier = Verification.SELF_DECLARED
    return VerificationStatus(tier=tier, note=row["verification_note"] or "")


def set_verification(slug: str, tier: Verification, note: str = "") -> None:
    """Record a reviewer's decision about a facility."""
    _write(
        update(facilities_v2)
        .where(facilities_v2.c.slug == slug)
        .values(verification=tier.value, verification_note=note),
        "Could not record the verification",
    )


def verifications_for(slugs: list[str]) -> dict[str, VerificationStatus]:
    """Verification for many facilities in one query, for list pages."""
    if not slugs:
        return {}
    rows = _all_rows(
        select(
            facilities_v2.c.slug,
            facilities_v2.c.verification,
            facilities_v2.c.verification_note,
        ).where(facilities_v2.c.slug.in_(slugs)),
        "Could not load the verification statuses",
    )
    found = {}
    for row in rows:
        try:
            tier = Verification(row["verification"])
        except ValueError:
            tier = Verification.SELF_DECLARED
        found[row["slug"]] = VerificationStatus(tier=tier, note=row["verification_note"] or "")
    return found


def save_facility(profile: FacilityProfile) -> str:
    """Insert the profile, or replace whatever is already stored under its slug."""
    values = _facility_values(profile)
    slug_match = facilities_v2.c.slug == profile.slug
    try:
        with get_engine().begin() as conn:
            stored = conn.execute(select(facilities_v2.c.slug).where(slug_match)).first()
            statement = (
                update(facilities_v2).where(slug_match).values(**values)
                if stored
                else insert(facilities_v2).values(
                    slug=profile.slug, created_at=datetime.now(), **values
                )
            )
            conn.execute(statement)
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not save the facility: {exc}") from exc
    return profile.slug


def load_facility(slug: str) -> FacilityProfile | None:
    row = _first_row(
        select(facilities_v2).where(facilities_v2.c.slug == slug),
        "Could not load the facility",
    )
    return None if row is None else _facility_from_row(row)


def list_facilities() -> list[FacilityProfile]:
    rows = _all_rows(
        select(facilities_v2).order_by(facilities_v2.c.name),
        "Could not list the facilities",
    )
    return [_facility_from_row(row) for row in rows]


# --- Offers ---------------------------------------------------------------


def _offer_values(offer: Offer) -> dict[str, Any]:
    return {
        "donor_name": offer.donor_name,
        "donor_email": offer.donor_email,
        "device_label": offer.device.label,
        "device_type": offer.device_type,
        "support_kind": offer.support_kind,
        "device_json": json.dumps(device_to_dict(offer.device)),
        "country": offer.origin.country,
        "city": offer.origin.city,
        "latitude": offer.origin.latitude,
        "longitude": offer.origin.longitude,
        "quantity_offered": offer.quantity_offered,
        "quantity_reserved": offer.quantity_reserved,
        "quantity_shipped": offer.quantity_shipped,
        "available_until": offer.available_until,
        "status": offer.status.value,
    }


def _offer_from_row(row: Any) -> Offer:
    return Offer(
        donor_name=row["donor_name"],
        donor_email=row["donor_email"],
        device=device_from_dict(json.loads(row["device_json"])),
        device_type=row["device_type"] or "",
        support_kind=row["support_kind"] or "",
        origin=GeoPoint(
            country=row["country"],
            city=row["city"] or "",
            latitude=row["latitude"],
            longitude=row["longitude"],
        ),
        quantity_offered=row["quantity_offered"],
        quantity_reserved=row["quantity_reserved"],
        quantity_shipped=row["quantity_shipped"],
        available_until=row["available_until"],
        status=OfferStatus(row["status"]),
        id=row["id"],
        created_at=row["created_at"],
    )


def save_offer(offer: Offer) -> int:
    statement = insert(offers).values(
        created_at=offer.created_at or datetime.now(), **_offer_values(offer)
    )
    return _write_returning_id(statement, "Could not save the offer")


def load_offer(offer_id: int) -> Offer | None:
    row = _first_row(
        select(offers).where(offers.c.id == offer_id), "Could not load the offer"
    )
    return None if row is None else _offer_from_row(row)


def update_offer(offer: Offer) -> None:
    """Write an offer back. The caller holds a new Offer; nothing is mutated here."""
    if offer.id is None:
        raise RepositoryError("Cannot update an offer that was never saved.")
    statement = update(offers).where(offers.c.id == offer.id).values(**_offer_values(offer))
    _write(statement, "Could not update the offer")


def known_device_types() -> list[str]:
    """Every device type currently on an open offer, sorted.

    Fed to the free-text intake as a controlled vocabulary, so a clinic writing
    "concentrator" can be read as the stocked "oxygen concentrator" rather than
    a line that matches nothing. Copied from upstream 3236e78 without the
    substitute terms, which this tree does not carry (design §13).
    """
    rows = _all_rows(
        select(offers.c.device_type)
        .where(offers.c.status == OfferStatus.OPEN.value)
        .distinct(),
        "Could not list the stocked device types",
    )
    return sorted({str(row["device_type"]).strip() for row in rows if row["device_type"]})


def open_offers_for_type(device_type: str, today: date | None = None) -> list[Offer]:
    """Offers that can still be matched against a request for this device type.

    Open, with stock left, and not past the donor's cut-off date. Status, stock
    and date are filtered in SQL; the type is matched against the stored label,
    because an offer carries a whole device and not a type. A blank type
    matches nothing rather than everything.
    """
    if not device_type.strip():
        return []
    available = (
        offers.c.quantity_offered - offers.c.quantity_reserved - offers.c.quantity_shipped
    )
    criteria = and_(
        offers.c.status == OfferStatus.OPEN.value,
        available > NO_STOCK,
        or_(offers.c.available_until.is_(None), offers.c.available_until >= (today or date.today())),
    )
    rows = _all_rows(
        select(offers).where(criteria).order_by(offers.c.created_at),
        "Could not list the open offers",
    )
    rows = [row for row in rows if _type_matches(device_type, row["device_type"] or "")]
    return [_offer_from_row(row) for row in rows]


# --- Requests -------------------------------------------------------------


def _request_from_row(row: Any, lines: tuple[RequestLine, ...] = ()) -> Request:
    return Request(
        facility_slug=row["facility_slug"],
        lines=lines,
        note=row["note"] or "",
        status=RequestStatus(row["status"]),
        id=row["id"],
        created_at=row["created_at"],
    )


def _lines_for(request_ids: list[int]) -> dict[int, tuple[RequestLine, ...]]:
    """Every line for these requests, in one query rather than one per request."""
    if not request_ids:
        return {}
    rows = _all_rows(
        select(request_lines)
        .where(request_lines.c.request_id.in_(request_ids))
        .order_by(request_lines.c.request_id, request_lines.c.position),
        "Could not load the request lines",
    )
    grouped: dict[int, list[RequestLine]] = {rid: [] for rid in request_ids}
    for row in rows:
        grouped[row["request_id"]].append(
            RequestLine(
                device_type=row["device_type"],
                quantity=row["quantity"],
                id=row["id"],
            )
        )
    return {rid: tuple(found) for rid, found in grouped.items()}


def save_request(request: Request) -> int:
    """Store the request and its lines. A request with no lines is refused."""
    if not request.lines:
        raise RepositoryError("A request must ask for at least one kind of equipment.")

    statement = insert(requests).values(
        facility_slug=request.facility_slug,
        note=request.note,
        status=request.status.value,
        created_at=request.created_at or datetime.now(),
    )
    request_id = _write_returning_id(statement, "Could not save the request")

    rows = [
        {
            "request_id": request_id,
            "device_type": line.device_type,
            "quantity": line.quantity,
            "position": position,
        }
        for position, line in enumerate(request.lines)
    ]
    _write(insert(request_lines), "Could not save the request lines", rows)
    return request_id


def load_request(request_id: int) -> Request | None:
    row = _first_row(
        select(requests).where(requests.c.id == request_id), "Could not load the request"
    )
    if row is None:
        return None
    return _request_from_row(row, _lines_for([request_id]).get(request_id, ()))


def list_open_requests() -> list[Request]:
    rows = _all_rows(
        select(requests)
        .where(requests.c.status == RequestStatus.OPEN.value)
        .order_by(requests.c.created_at),
        "Could not list the open requests",
    )
    by_request = _lines_for([row["id"] for row in rows])
    return [_request_from_row(row, by_request.get(row["id"], ())) for row in rows]


# --- Matches --------------------------------------------------------------


def _match_from_row(row: Any) -> MatchRecord:
    return MatchRecord(
        request_id=row["request_id"],
        offer_id=row["offer_id"],
        verdict=verdict_from_dict(json.loads(row["verdict_json"])),
        intel=intel_from_dict(json.loads(row["intel_json"] or EMPTY_JSON_OBJECT)),
        quantity=row["quantity"],
        distance_km=row["distance_km"],
        state=MatchState(row["state"]),
        token=row["token"],
        id=row["id"],
        created_at=row["created_at"],
        responded_at=row["responded_at"],
    )


def save_match(match: MatchRecord) -> str:
    """Store a proposed match and return its id, generated here when absent.

    created_at is written from Python, not left to the server default, so that
    open_matches_older_than compares two readings of the same clock.
    """
    match_id = match.id or _new_match_id()
    statement = insert(matches).values(
        id=match_id,
        request_id=match.request_id,
        offer_id=match.offer_id,
        quantity=match.quantity,
        distance_km=match.distance_km,
        state=match.state.value,
        token=match.token,
        verdict_json=json.dumps(verdict_to_dict(match.verdict)),
        intel_json=json.dumps(intel_to_dict(match.intel)),
        created_at=match.created_at or datetime.now(),
        responded_at=match.responded_at,
    )
    _write(statement, "Could not save the match")
    return match_id


def load_match(match_id: str) -> MatchRecord | None:
    row = _first_row(
        select(matches).where(matches.c.id == match_id), "Could not load the match"
    )
    return None if row is None else _match_from_row(row)


def load_match_by_token(token: str) -> MatchRecord | None:
    """Find the match behind a donor's confirm link. A blank token matches nothing."""
    if not token:
        return None
    row = _first_row(
        select(matches).where(matches.c.token == token), "Could not load the match"
    )
    return None if row is None else _match_from_row(row)


def update_match_state(
    match_id: str, state: MatchState, responded_at: datetime | None = None
) -> None:
    statement = (
        update(matches)
        .where(matches.c.id == match_id)
        .values(state=state.value, responded_at=responded_at or datetime.now())
    )
    _write(statement, "Could not update the match")


def matches_for_request(request_id: int) -> list[MatchRecord]:
    rows = _all_rows(
        select(matches)
        .where(matches.c.request_id == request_id)
        .order_by(matches.c.created_at),
        "Could not list the matches",
    )
    return [_match_from_row(row) for row in rows]


def open_matches_older_than(hours: int, now: datetime | None = None) -> list[MatchRecord]:
    """Matches still waiting on a donor past the confirmation window. For the sweep."""
    if hours < MINIMUM_SWEEP_HOURS:
        raise RepositoryError("The timeout window cannot be negative.")
    cutoff = (now or datetime.now()) - timedelta(hours=hours)
    criteria = and_(
        matches.c.state.in_(OPEN_MATCH_STATES), matches.c.created_at <= cutoff
    )
    rows = _all_rows(
        select(matches).where(criteria).order_by(matches.c.created_at),
        "Could not list the timed-out matches",
    )
    return [_match_from_row(row) for row in rows]


def offers_for_support(kind_value: str, terms: tuple[str, ...], today: date | None = None) -> list[Offer]:
    """Open offers that could satisfy a support requirement.

    Matches the categorised support kind first, then falls back to the
    requirement's own search terms against the donor's free-text device type,
    so an uncategorised offer is still findable.
    """
    available = (
        offers.c.quantity_offered - offers.c.quantity_reserved - offers.c.quantity_shipped
    )
    criteria = and_(
        offers.c.status == OfferStatus.OPEN.value,
        available > 0,
        or_(offers.c.available_until.is_(None),
            offers.c.available_until >= (today or date.today())),
    )
    rows = _all_rows(
        select(offers).where(criteria).order_by(offers.c.created_at),
        "Could not list the support offers",
    )
    found = []
    for row in rows:
        stored_kind = (row["support_kind"] or "").strip().lower()
        haystack = f"{row['device_type']} {row['device_label']}".lower()
        if stored_kind == kind_value.lower() or any(t in haystack for t in terms):
            found.append(_offer_from_row(row))
    return found


# --- Recorded supply answers ---------------------------------------------

_ANSWER_FIELDS = (
    "consumables_available", "spare_parts_available", "sold_locally",
    "manufacturer_supported",
)


def _answer_from_row(row: Any) -> SupplyAnswer:
    return SupplyAnswer(
        id=row["id"],
        device_type=row["device_type"] or "",
        model=row["model"] or ANY_MODEL,
        country=row["country"],
        recorded_by=row["recorded_by"] or "",
        note=row["note"] or "",
        recorded_at=row["recorded_at"],
        **{name: (None if row[name] is None else bool(row[name])) for name in _ANSWER_FIELDS},
    )


def save_supply_answer(answer: SupplyAnswer) -> int:
    """Insert the answer, or replace the one stored for its (type, model, country)."""
    values = {
        "device_type": answer.device_type.strip(),
        "model": answer.model.strip(),
        "country": answer.country.strip(),
        "recorded_by": answer.recorded_by,
        "note": answer.note,
        "recorded_at": answer.recorded_at or datetime.now(),
        **{name: getattr(answer, name) for name in _ANSWER_FIELDS},
    }
    key = and_(
        supply_answers.c.device_type == values["device_type"],
        supply_answers.c.model == values["model"],
        supply_answers.c.country == values["country"],
    )
    try:
        with get_engine().begin() as conn:
            stored = conn.execute(select(supply_answers.c.id).where(key)).first()
            if stored:
                conn.execute(update(supply_answers).where(key).values(**values))
                return int(stored[0])
            return int(conn.execute(insert(supply_answers).values(**values)).inserted_primary_key[0])
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not save the supply answer: {exc}") from exc


def load_supply_answer(device_type: str, model: str, country: str) -> SupplyAnswer | None:
    """The answer for this exact model in this country, else for its type.

    The model lookup ignores the device type, so a caller that knows only
    the model still finds a model-specific answer. The type fallback reads
    only rows recorded for any model (model ''). Matching ignores case.
    """
    in_country = func.lower(supply_answers.c.country) == (country or "").strip().lower()
    newest = supply_answers.c.id.desc()
    wanted_model = (model or "").strip().lower()
    if wanted_model:
        row = _first_row(
            select(supply_answers)
            .where(in_country, func.lower(supply_answers.c.model) == wanted_model)
            .order_by(newest),
            "Could not load the supply answer",
        )
        if row is not None:
            return _answer_from_row(row)
    wanted_type = (device_type or "").strip().lower()
    if not wanted_type:
        return None
    row = _first_row(
        select(supply_answers)
        .where(
            in_country,
            supply_answers.c.model == ANY_MODEL,
            func.lower(supply_answers.c.device_type) == wanted_type,
        )
        .order_by(newest),
        "Could not load the supply answer",
    )
    return None if row is None else _answer_from_row(row)


# --- Registry lookups ----------------------------------------------------


def _registry_key(model: str) -> str:
    return (model or "").strip().lower()


def cached_registry_lookup(
    model: str, max_age_days: int = REGISTRY_CACHE_DAYS, *, now: datetime | None = None
) -> RegistryLookup | None:
    """The stored lookup for this model, or None when absent or older than the limit."""
    row = _first_row(
        select(registry_lookups).where(registry_lookups.c.model == _registry_key(model)),
        "Could not read the registry cache",
    )
    if row is None:
        return None
    if row["fetched_at"] < (now or datetime.now()) - timedelta(days=max_age_days):
        return None
    return RegistryLookup(
        model=row["model"], status=row["status"] or "", record_url=row["record_url"] or "",
        raw_json=row["raw_json"] or EMPTY_JSON_OBJECT, fetched_at=row["fetched_at"],
    )


def store_registry_lookup(
    model: str, status: str, record_url: str, raw_json: str | dict | list,
    *, now: datetime | None = None,
) -> None:
    """Write (or replace) the cached lookup for this model."""
    key = _registry_key(model)
    values = {
        "fetched_at": now or datetime.now(),
        "status": status or "",
        "record_url": record_url or "",
        "raw_json": raw_json if isinstance(raw_json, str) else json.dumps(raw_json),
    }
    match = registry_lookups.c.model == key
    try:
        with get_engine().begin() as conn:
            stored = conn.execute(select(registry_lookups.c.model).where(match)).first()
            conn.execute(
                update(registry_lookups).where(match).values(**values)
                if stored else insert(registry_lookups).values(model=key, **values)
            )
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not cache the registry lookup: {exc}") from exc


# --- Handover packets ----------------------------------------------------


def _packet_key(offer_id: int, facility_slug: str) -> Any:
    return and_(packets.c.offer_id == offer_id, packets.c.facility_slug == facility_slug)


def load_packet(offer_id: int, facility_slug: str) -> StoredPacket | None:
    row = _first_row(
        select(packets).where(_packet_key(offer_id, facility_slug)),
        "Could not load the packet",
    )
    if row is None:
        return None
    translated = row["translated_json"]
    return StoredPacket(
        offer_id=row["offer_id"], facility_slug=row["facility_slug"],
        language=row["language"] or "", card=json.loads(row["card_json"] or EMPTY_JSON_OBJECT),
        translated=None if translated is None else json.loads(translated),
        built_at=row["built_at"],
    )


def save_packet(
    offer_id: int, facility_slug: str, language: str, card_json: dict,
    translated_json: dict | None = None, *, now: datetime | None = None,
) -> None:
    """Store (or replace) the packet built for this offer at this clinic."""
    key = _packet_key(offer_id, facility_slug)
    values = {
        "language": language or "",
        "card_json": json.dumps(card_json),
        "translated_json": None if translated_json is None else json.dumps(translated_json),
        "built_at": now or datetime.now(),
    }
    try:
        with get_engine().begin() as conn:
            stored = conn.execute(select(packets.c.offer_id).where(key)).first()
            conn.execute(
                update(packets).where(key).values(**values) if stored
                else insert(packets).values(offer_id=offer_id, facility_slug=facility_slug, **values)
            )
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not save the packet: {exc}") from exc


def clear_packet(offer_id: int, facility_slug: str) -> None:
    _write(delete(packets).where(_packet_key(offer_id, facility_slug)), "Could not clear the packet")


# --- Extractions held for the confirm form -------------------------------


def save_extraction(
    facts: DeviceFacts, document: dict | None = None, *, now: datetime | None = None
) -> str:
    """Hold an extraction under a new random id and return the id."""
    extraction_id = uuid.uuid4().hex[:EXTRACTION_ID_LENGTH]
    _write(
        insert(extractions).values(
            id=extraction_id,
            facts_json=json.dumps(device_to_dict(facts)),
            document_json=json.dumps(document or {}),
            created_at=now or datetime.now(),
        ),
        "Could not hold the extraction",
    )
    return extraction_id


def load_extraction(extraction_id: str, *, now: datetime | None = None) -> StoredExtraction | None:
    """The held extraction, or None when unknown or older than an hour."""
    row = _first_row(
        select(extractions).where(extractions.c.id == (extraction_id or "")),
        "Could not load the extraction",
    )
    if row is None or row["created_at"] < (now or datetime.now()) - EXTRACTION_TTL:
        return None
    return StoredExtraction(
        id=row["id"],
        facts=device_from_dict(json.loads(row["facts_json"])),
        document=json.loads(row["document_json"] or EMPTY_JSON_OBJECT),
        created_at=row["created_at"],
    )
