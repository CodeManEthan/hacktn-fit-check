"""Load the demo: clinics, the hero trio, support offers, one request, a history.

Run with: ./.venv/bin/python -m scripts.seed_demo

Safe to run again on a database that is already seeded (the VM was seeded once
by the earlier script). Offers are keyed on (donor name, manufacturer, model)
and replaced in place; recorded answers are keyed on (device type, model,
country); the Kayunga request is created once; the seeded history is
deleted and written again. Running it twice leaves one of each.

The hero offers are built from the device library (data/device_library.json),
so every value they carry cites a manufacturer document; the donor adds only
what is about the unit (build year, quantity, location, "nothing dated").
The older demo offers below are typed as stated by the donor.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from sqlalchemy import delete, select

from app.checks.runner import check_fit
from app.data import db
from app.data import marketplace_repo as repo
from app.data import repository
from app.data.marketplace_schema import offers as offers_table
from app.data.seed import FACILITIES
from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.language import normalise_languages
from app.domain.marketplace import GeoPoint, Offer, OfferStatus, Request, RequestLine
from app.domain.provenance import UNKNOWN, Fact, Provenance, fact
from app.domain.supply_answer import SupplyAnswer
from app.reference import device_library

STATED = Provenance.DONOR_STATED
OFFER_WINDOW_DAYS = 180
NOTHING_DATED = fact(False, STATED)

KAYUNGA = "uganda-district"
HERO_TYPE = "oxygen concentrator"
HERO_A_SLUG = "drive-devilbiss-525ds"
HERO_B_SLUG = "drive-devilbiss-525ks"
OFFER_C_TEXT = "older oxygen concentrator, works fine, 2 units"

# Devices saved for the seeded history carry this as their raw input, so a
# rerun can find and replace exactly the rows it wrote before.
HISTORY_MARKER = "seed_demo: impact history"
HISTORY_SIZE = 20


def _device(**kwargs) -> DeviceFacts:
    if "manual_languages" in kwargs:
        kwargs["manual_languages"] = normalise_languages(kwargs["manual_languages"])
    return DeviceFacts(**{k: fact(v, STATED) for k, v in kwargs.items()})


def _durable(device: DeviceFacts) -> DeviceFacts:
    """A durable offer: the donor's answer is that nothing in it is dated."""
    return replace(device, dated_items=NOTHING_DATED)


def _from_library(slug: str, year: int) -> DeviceFacts:
    found = device_library.entry(slug)
    if found is None:
        raise SystemExit(f"Library entry {slug!r} is missing; run scripts.build_library first.")
    return replace(device_library.to_device_facts(found),
                   year_of_manufacture=fact(year, STATED), dated_items=NOTHING_DATED)


def hero_offers() -> tuple[Offer, ...]:
    """The three oxygen concentrators screened against Kayunga on stage ([F3])."""
    return (
        Offer(
            donor_name="Nashville General Hospital biomedical department",
            donor_email="biomed@example.org",
            device_type=HERO_TYPE,
            origin=GeoPoint(country="United States", city="Nashville",
                            latitude=36.1627, longitude=-86.7816),
            quantity_offered=1,
            device=_from_library(HERO_A_SLUG, 2016),
        ),
        Offer(
            donor_name="Leeds Teaching Hospitals NHS Trust, surplus",
            donor_email="surplus@example.org",
            device_type=HERO_TYPE,
            origin=GeoPoint(country="United Kingdom", city="Leeds",
                            latitude=53.8008, longitude=-1.5491),
            quantity_offered=1,
            device=_from_library(HERO_B_SLUG, 2019),
        ),
        Offer(
            donor_name="Harris County clinic network",
            donor_email="clinics@example.org",
            device_type=HERO_TYPE,
            origin=GeoPoint(country="United States", city="Houston",
                            latitude=29.7604, longitude=-95.3698),
            quantity_offered=2,
            # Nothing known: no plate, no model, no answer on dated items.
            device=DeviceFacts(raw_input=OFFER_C_TEXT),
        ),
    )


DEMO_OFFERS = (
    Offer(
        donor_name="Baptist Health biomedical department",
        donor_email="biomed@example.org",
        device_type="ventilator",
        origin=GeoPoint(country="United States", city="Nashville", latitude=36.1627, longitude=-86.7816),
        quantity_offered=3,
        device=_device(
            manufacturer="Puritan Bennett", model="840", year_of_manufacture=2009,
            voltage_min_v=100, voltage_max_v=120, frequencies_hz=[50, 60], power_watts=400,
            temp_min_c=10.0, temp_max_c=40.0, humidity_max_pct=95.0,
            needs_oxygen=True, needs_compressed_air=True, needs_distilled_water=False,
            consumables=["patient circuits", "bacterial filters", "oxygen sensors"],
            manual_languages=["English"], operating_complexity="high",
        ),
    ),
    Offer(
        donor_name="St Mary's Hospital surplus",
        donor_email="surplus@example.org",
        device_type="anaesthesia machine",
        origin=GeoPoint(country="United States", city="Chicago", latitude=41.8781, longitude=-87.6298),
        quantity_offered=2,
        device=_device(
            manufacturer="Drager", model="Fabius Tiro", year_of_manufacture=2019,
            voltage_min_v=220, voltage_max_v=240, frequencies_hz=[50, 60], power_watts=150,
            temp_min_c=10.0, temp_max_c=40.0, humidity_max_pct=80.0,
            needs_oxygen=True, needs_compressed_air=True, needs_distilled_water=False,
            consumables=["soda lime canisters", "breathing circuits"],
            manual_languages=["English", "French"], operating_complexity="high",
        ),
    ),
    Offer(
        donor_name="Nairobi Equipment Trust",
        donor_email="trust@example.org",
        device_type="patient monitor",
        origin=GeoPoint(country="Kenya", city="Nairobi", latitude=-1.2921, longitude=36.8219),
        quantity_offered=6,
        device=_device(
            manufacturer="Philips", model="SureSigns VM4", year_of_manufacture=2021,
            voltage_min_v=100, voltage_max_v=240, frequencies_hz=[50, 60], power_watts=60,
            temp_min_c=0.0, temp_max_c=45.0, humidity_max_pct=90.0,
            needs_oxygen=False, needs_compressed_air=False, needs_distilled_water=False,
            consumables=["SpO2 sensors", "blood pressure cuffs"],
            manual_languages=["English", "French", "Swahili"], operating_complexity="low",
        ),
    ),
    Offer(
        donor_name="Gulf Coast Medical Center",
        donor_email="surplus@example.org",
        device_type="ventilator",
        origin=GeoPoint(country="Kenya", city="Mombasa", latitude=-4.0435, longitude=39.6682),
        quantity_offered=2,
        device=_device(
            manufacturer="Hamilton Medical", model="C1", year_of_manufacture=2020,
            voltage_min_v=100, voltage_max_v=240, frequencies_hz=[50, 60], power_watts=120,
            temp_min_c=0.0, temp_max_c=40.0, humidity_max_pct=90.0,
            needs_oxygen=True, needs_compressed_air=False, needs_distilled_water=False,
            consumables=["patient circuits", "bacterial filters"],
            manual_languages=["English"], operating_complexity="high",
        ),
    ),
)

SOLLATEK_NOTE = ("stand-in figures: the UNICEF S0002632 1 kVA medical stabiliser's stated "
                 "160-260 V input (add-on-fulfillment-research.md [A9]) until a Sollatek AVS30 "
                 "document is fetched")


def _sollatek() -> DeviceFacts:
    """The Sollatek row, with the stand-in input range the self-check needs."""
    return replace(
        _device(manufacturer="Sollatek", model="AVS30", year_of_manufacture=2022),
        voltage_min_v=fact(160, STATED, SOLLATEK_NOTE),
        voltage_max_v=fact(260, STATED, SOLLATEK_NOTE),
        frequencies_hz=fact([50], STATED, SOLLATEK_NOTE),
    )


# Support equipment. Without these in the inventory a requirement stays advice.
SUPPORT_OFFERS = (
    Offer(
        donor_name="Kampala Power Trust",
        donor_email="trust@example.org",
        device_type="voltage stabiliser",
        support_kind="voltage stabiliser",
        origin=GeoPoint(country="Uganda", city="Kampala", latitude=0.3476, longitude=32.5825),
        quantity_offered=4,
        device=_sollatek(),
    ),
    Offer(
        donor_name="Kampala Power Trust",
        donor_email="trust@example.org",
        device_type="UPS 3kVA",
        support_kind="backup power",
        origin=GeoPoint(country="Uganda", city="Kampala", latitude=0.3476, longitude=32.5825),
        quantity_offered=2,
        device=_device(manufacturer="APC", model="Smart-UPS 3000", year_of_manufacture=2021),
    ),
    Offer(
        donor_name="Mombasa Medical Gases",
        donor_email="gases@example.org",
        device_type="medical air compressor",
        support_kind="medical air compressor",
        origin=GeoPoint(country="Kenya", city="Mombasa", latitude=-4.0435, longitude=39.6682),
        quantity_offered=1,
        device=_device(manufacturer="Bambi", model="VT200", year_of_manufacture=2020),
    ),
    # The on-stage reject: a US regulator that fails frequency at Kayunga.
    Offer(
        donor_name="Houston surplus AVR",
        donor_email="avr@example.org",
        device_type="voltage stabiliser",
        support_kind="voltage stabiliser",
        origin=GeoPoint(country="United States", city="Houston",
                        latitude=29.7604, longitude=-95.3698),
        quantity_offered=1,
        # The donor's description; no maker is stated, so none is typed.
        device=_device(model="automatic voltage regulator",
                       voltage_min_v=110, voltage_max_v=120, frequencies_hz=[60]),
    ),
)

# The nonprofit's recorded answer for the 525KS in Uganda ([R20]): filters are
# not stocked, so the consumables check raises "two years of filters".
KAYUNGA_ANSWERS = (
    SupplyAnswer(
        device_type=HERO_TYPE, model="525KS", country="Uganda",
        consumables_available=False, spare_parts_available=True,
        sold_locally=None, manufacturer_supported=None,
        recorded_by="Fit-Check field team",
        note="Filters not stocked in Kampala; Drive's East Africa distributor services the 525 series",
    ),
)


def all_offers() -> tuple[Offer, ...]:
    """Every seeded offer; all but Offer C are durable (nothing dated)."""
    durable = tuple(replace(o, device=_durable(o.device)) for o in DEMO_OFFERS + SUPPORT_OFFERS)
    return hero_offers() + durable


# ---- recorded answers as supply intel ----------------------------------------

def recorded_intel(device: DeviceFacts, device_type: str, country: str) -> SupplyIntel:
    """Supply intel from the nonprofit's recorded answers only: no network, no model.

    The history uses this so its verdicts need nothing outside the database;
    the hero acceptance test uses it as the recorded-answer half of the
    supply order. A recorded "don't know" stays unknown.
    """
    answer = repo.load_supply_answer(device_type, str(device.model.value or ""), country)
    if answer is None:
        return SupplyIntel(country=country)
    note = f"recorded by {answer.recorded_by}" if answer.recorded_by else ""

    def held(value: bool | None) -> Fact:
        return UNKNOWN if value is None else fact(value, Provenance.RECORDED, note)

    return SupplyIntel(
        country=country,
        consumables_available=held(answer.consumables_available),
        spare_parts_available=held(answer.spare_parts_available),
        manufacturer_supported=held(answer.manufacturer_supported),
        sold_locally=held(answer.sold_locally),
        reasoning=answer.note,
    )


# ---- upserts --------------------------------------------------------------------

def _offer_key(donor_name: str, device: DeviceFacts) -> tuple[str, str, str]:
    return (
        donor_name.strip().casefold(),
        str(device.manufacturer.value or "").strip().casefold(),
        str(device.model.value or "").strip().casefold(),
    )


def _stored_offers_by_key() -> dict[tuple[str, str, str], list[Offer]]:
    with db.get_engine().connect() as conn:
        ids = [row[0] for row in conn.execute(select(offers_table.c.id).order_by(offers_table.c.id))]
    grouped: dict[tuple[str, str, str], list[Offer]] = {}
    for offer_id in ids:
        stored = repo.load_offer(offer_id)
        if stored is not None:
            grouped.setdefault(_offer_key(stored.donor_name, stored.device), []).append(stored)
    return grouped


def upsert_offers(offers: tuple[Offer, ...], available_until: date) -> list[tuple[int, Offer, str]]:
    """Save each offer, or replace the stored one with the same key.

    The stored offer keeps its id, creation time and the stock already
    reserved or shipped against it; its facts, type, origin, quantity and
    window are the seed's. Older duplicates of a key are withdrawn, not
    deleted, so a match that points at one still loads.
    """
    stored = _stored_offers_by_key()
    written = []
    for offer in offers:
        fresh = replace(offer, available_until=offer.available_until or available_until)
        found = stored.get(_offer_key(offer.donor_name, offer.device), [])
        if not found:
            written.append((repo.save_offer(fresh), fresh, "added"))
            continue
        keep, *extras = found
        repo.update_offer(replace(
            fresh, id=keep.id, created_at=keep.created_at, status=OfferStatus.OPEN,
            quantity_reserved=keep.quantity_reserved, quantity_shipped=keep.quantity_shipped,
        ))
        for extra in extras:
            repo.update_offer(replace(extra, status=OfferStatus.WITHDRAWN))
        written.append((keep.id, fresh, "replaced"))
    return written


def ensure_kayunga_request() -> tuple[int, bool]:
    """The open Kayunga request for one oxygen concentrator, made once."""
    for open_request in repo.list_open_requests():
        if open_request.facility_slug == KAYUNGA and any(
            line.device_type.strip().casefold() == HERO_TYPE for line in open_request.lines
        ):
            return open_request.id, False
    return repo.save_request(Request(
        facility_slug=KAYUNGA,
        lines=(RequestLine(device_type=HERO_TYPE, quantity=1),),
        note="For the maternity ward. Our one concentrator shares the generator with theatre.",
    )), True


# ---- history ----------------------------------------------------------------------

def history_pairs() -> list[tuple[device_library.LibraryEntry, object]]:
    """Twenty (library device, clinic) pairs: every device at one clinic, eight at a second."""
    entries = device_library.load_library()
    clinics = FACILITIES
    pairs = [(e, clinics[i % len(clinics)]) for i, e in enumerate(entries)]
    pairs += [(e, clinics[(i + 1) % len(clinics)]) for i, e in enumerate(entries)]
    return pairs[:HISTORY_SIZE]


def _clear_history() -> int:
    devices, verdicts = db.devices, db.verdicts
    with db.get_engine().begin() as conn:
        ids = [row[0] for row in conn.execute(
            select(devices.c.id).where(devices.c.raw_input == HISTORY_MARKER))]
        if ids:
            conn.execute(delete(verdicts).where(verdicts.c.device_id.in_(ids)))
            conn.execute(delete(devices).where(devices.c.id.in_(ids)))
    return len(ids)


def seed_history() -> list[tuple[str, str, str]]:
    """Screen library devices against the clinics and store each real verdict."""
    _clear_history()
    written = []
    for found, clinic in history_pairs():
        device = replace(device_library.to_device_facts(found), raw_input=HISTORY_MARKER)
        intel = recorded_intel(device, found.device_type, clinic.country)
        verdict = check_fit(device, clinic, intel)
        device_id = repository.save_device(device)
        repository.save_verdict(device_id=device_id, device=device, facility=clinic,
                                verdict=verdict, intel=intel)
        written.append((device.label, clinic.name, verdict.outcome.value))
    return written


def main(today: date | None = None) -> dict[str, int]:
    repository.setup()
    for profile in FACILITIES:
        repo.save_facility(profile)
    print(f"facilities: {len(FACILITIES)}")

    expires = (today or date.today()) + timedelta(days=OFFER_WINDOW_DAYS)
    written = upsert_offers(all_offers(), expires)
    for offer_id, offer, action in written:
        print(f"  offer {offer_id} {action}: {offer.device.label} x{offer.quantity_offered} "
              f"from {offer.origin.city}")

    for answer in KAYUNGA_ANSWERS:
        answer_id = repo.save_supply_answer(answer)
        print(f"  answer {answer_id}: {answer.device_type} {answer.model} in {answer.country}")

    request_id, made = ensure_kayunga_request()
    print(f"  request {request_id} {'added' if made else 'kept'}: Kayunga, 1 {HERO_TYPE}")

    history = seed_history()
    print(f"history: {len(history)} verdicts")
    return {"offers": len(written), "answers": len(KAYUNGA_ANSWERS), "history": len(history),
            "request_id": request_id}


if __name__ == "__main__":
    main()
