"""Routes for the two-sided platform: offers, facilities, requests, matches."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.ai.categorise import categorise_support
from app.ai.client import GenAiError
from app.ai.extract import extract_device, image_to_data_url
from app.data import marketplace_repo as repo
from app.data.repository import RepositoryError
from app.domain.device import DeviceFacts
from app.domain.facility import FacilityProfile
from app.domain.language import normalise_languages
from app.domain.facility_trust import Verification, VerificationStatus
from app.domain.marketplace import (
    GeoPoint,
    MatchState,
    Offer,
    Request as DonationRequest,
    RequestLine,
)
from app.domain.result import Outcome
from app.matching.bundle import build_bundle
from app.matching.coverage import LineCoverage
from app.matching.pipeline import propose_match, screen_offers
from app.matching.supply_order import supply_lookup
from app.matching.verification import confirm, request_confirmation, withdraw
from app.reference.country_power import country_centroid, lookup
from app.reference.facility_registry import corroborates, find_candidates
from app.web import forms
from app.web.forms import FormError
from app.web.presenters import advisory_for, rule_count, TONE_BY_OUTCOME, TONE_BY_STATUS

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["rule_count"] = rule_count()
router = APIRouter()


def _error(request: Request, message: str, back: str = "/") -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name="error.html",
        context={"message": message, "back": back}, status_code=400,
    )


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    """The landing workstation, with the seeded clinics as testbeds.

    The telemetry on each card is read from the stored profile rather than
    written into the template, so a figure on the front page cannot drift away
    from the figure the screening actually uses.
    """
    testbeds = [
        {
            "slug": f.slug,
            "name": f.name,
            "country": f.country,
            "voltage": f.mains_voltage_v,
            "frequency": f.mains_frequency_hz,
            "outages": f.outages_per_week,
            "temp": f.ambient_temp_max_c,
        }
        for f in repo.list_facilities()
    ]
    return TEMPLATES.TemplateResponse(
        request=request, name="landing.html", context={"testbeds": testbeds}
    )


# ---- donor side --------------------------------------------------------

@router.get("/offer", response_class=HTMLResponse)
def offer_form(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request=request, name="offer.html", context={})


@router.post("/offer", response_class=HTMLResponse)
async def offer_submit(
    request: Request,
    model_name: str = Form(default=""),
    spec_text: str = Form(default=""),
    device_type: str = Form(default=""),
    quantity: str = Form(default="1"),
    available_until: str = Form(default=""),
    expiry_date: str = Form(default=""),
    country: str = Form(default=""),
    city: str = Form(default=""),
    donor_name: str = Form(default=""),
    donor_email: str = Form(default=""),
    nameplate: UploadFile | None = File(default=None),
) -> HTMLResponse:
    try:
        device: DeviceFacts | None = None
        posted = await request.form()
        library_slug = str(posted.get("library_slug") or "").strip()
        if library_slug:
            # [B3] A library pick: the facts are rebuilt from the library, never
            # read from the form, so a cited value cannot be edited by hand.
            # The donor adds only what is about this unit: its build year here,
            # quantity and location below.
            picked = _library_entry(library_slug)
            device = _library_device(picked, str(posted.get("year_of_manufacture") or ""))
            device_type = device_type.strip() or picked.device_type
            model_name = model_name.strip() or picked.label

        if device is None:
            typed = "\n".join(p for p in (model_name.strip(), spec_text.strip()) if p)
            image_data_url = ""
            if nameplate is not None and nameplate.filename:
                image_data_url = image_to_data_url(await nameplate.read(), nameplate.content_type or "")
            if not typed and not image_data_url:
                raise FormError("Give a model name, paste spec text, or add a nameplate photo.")

            device = extract_device(typed_text=typed, image_data_url=image_data_url)
        # The donor's expiry answer is the last word on the date, whatever
        # built the facts above it.
        device = forms.apply_dated_items(device, expiry_date)
        offer = Offer(
            donor_name=forms.text(donor_name, "Your name", required=True),
            donor_email=forms.text(donor_email, "Email", required=True),
            device=device,
            device_type=forms.text(device_type, "Kind of device", required=True, limit=120),
            support_kind=_support_kind(device_type, model_name),
            origin=_point(forms.text(country, "Country", required=True), city),
            quantity_offered=forms.whole_number(quantity, "How many", default=1, minimum=1),
            available_until=forms.iso_date(available_until, "Available until"),
        )
        offer_id = repo.save_offer(offer)
    except (FormError, ValueError, GenAiError, RepositoryError) as exc:
        return _error(request, str(exc), back="/offer")

    return RedirectResponse(url=f"/offer/{offer_id}", status_code=303)


@router.get("/offer/{offer_id}", response_class=HTMLResponse)
def offer_posted(request: Request, offer_id: int) -> HTMLResponse:
    offer = repo.load_offer(offer_id)
    if offer is None:
        return _error(request, f"No offer with id {offer_id}.")
    return TEMPLATES.TemplateResponse(
        request=request, name="offer_posted.html",
        context={"offer": offer, "pairs": _device_pairs(offer)},
    )


# ---- clinic side -------------------------------------------------------

@router.get("/clinic", response_class=HTMLResponse)
def facility_form(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request=request, name="facility_form.html", context={})


@router.post("/clinic", response_class=HTMLResponse)
async def facility_submit(request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        name = forms.text(str(form.get("name", "")), "Facility name", required=True)
        country = forms.text(str(form.get("country", "")), "Country", required=True)
        grid = lookup(country)
        latitude, longitude = (country_centroid(country) or (None, None))

        profile = FacilityProfile(
            slug=forms.slugify(name),
            name=name,
            country=country,
            mains_voltage_v=forms.whole_number(
                str(form.get("mains_voltage_v", "")), "Mains voltage",
                default=grid.mains_voltage_v if grid else 230, minimum=1),
            mains_frequency_hz=forms.whole_number(
                str(form.get("mains_frequency_hz", "")), "Frequency",
                default=grid.mains_frequency_hz if grid else 50, minimum=1),
            plug_types=tuple(grid.plug_types) if grid else (),
            ambient_temp_max_c=forms.decimal(str(form.get("ambient_temp_max_c", "")), "Temperature", default=30.0),
            humidity_max_pct=forms.decimal(str(form.get("humidity_max_pct", "")), "Humidity", default=70.0),
            has_air_conditioning=forms.flag(form.get("has_air_conditioning")),
            outages_per_week=forms.whole_number(str(form.get("outages_per_week", "")), "Outages"),
            has_voltage_stabilizer=forms.flag(form.get("has_voltage_stabilizer")),
            has_generator=forms.flag(form.get("has_generator")),
            has_oxygen_supply=forms.flag(form.get("has_oxygen_supply")),
            has_distilled_water=forms.flag(form.get("has_distilled_water")),
            has_compressed_air=forms.flag(form.get("has_compressed_air")),
            has_biomed_technician=forms.flag(form.get("has_biomed_technician")),
            trained_operators=forms.flag(form.get("trained_operators")),
            staff_languages=tuple(normalise_languages(
                forms.comma_list(str(form.get("staff_languages", ""))))) or ("English",),
            notes=forms.text(str(form.get("notes", "")), "Notes", limit=600),
            latitude=latitude,
            longitude=longitude,
        )
        repo.save_facility(profile)
    except (FormError, RepositoryError) as exc:
        return _error(request, str(exc), back="/clinic")

    return RedirectResponse(url="/request", status_code=303)


# ---- requests and matching --------------------------------------------

@router.get("/request", response_class=HTMLResponse)
def request_form(request: Request, facility: str = "") -> HTMLResponse:
    """The request form, optionally opened on a named facility.

    `facility` is what the landing page's demo chips carry, so a judge lands
    on the form with the clinic already chosen.
    """
    return TEMPLATES.TemplateResponse(
        request=request, name="request_form.html",
        context={"facilities": repo.list_facilities(), "chosen": facility},
    )


@router.post("/request", response_class=HTMLResponse)
async def request_submit(request: Request) -> HTMLResponse:
    """Accept a request of any length.

    The form posts one device_type and one quantity per row, so the fields
    arrive as parallel lists and are read from the raw form rather than
    declared as named parameters.
    """
    form = await request.form()
    try:
        row = DonationRequest(
            facility_slug=forms.text(
                str(form.get("facility_slug") or ""), "Facility", required=True
            ),
            lines=_lines_from(
                form.getlist("device_type"), form.getlist("quantity")
            ),
            note=forms.text(str(form.get("note") or ""), "Note", limit=600),
        )
        request_id = repo.save_request(row)
    except (FormError, ValueError, RepositoryError) as exc:
        return _error(request, str(exc), back="/request")
    return RedirectResponse(url=f"/request/{request_id}/matches", status_code=303)


@router.get("/request/{request_id}/matches", response_class=HTMLResponse)
def matches(request: Request, request_id: int) -> HTMLResponse:
    row = repo.load_request(request_id)
    if row is None:
        return _error(request, f"No request with id {request_id}.")
    facility = repo.load_facility(row.facility_slug)
    if facility is None:
        return _error(request, f"No facility profile for {row.facility_slug}.")

    coverages = []
    bundles: dict[int, object] = {}
    advisory: dict[int, object] = {}
    for line in row.lines:
        offers = repo.open_offers_for_type(line.device_type)
        screening = screen_offers(facility, offers, intel_for=supply_lookup)
        coverages.append(
            LineCoverage(
                line=line, candidates=screening.ranked, screened=len(offers),
                rejected=screening.rejected,
            )
        )
        for c in screening.ranked:
            bundles[c.offer.id] = build_bundle(c.verdict, facility, repo.offers_for_support)
        advisory.update({c.offer.id: advisory_for(c) for c in screening.rejected})

    return TEMPLATES.TemplateResponse(
        request=request, name="matches.html",
        context={
            "request_row": row, "facility": facility,
            "coverages": coverages, "bundles": bundles, "advisory": advisory,
            "symbol": TONE_BY_STATUS, "tone": TONE_BY_OUTCOME,
        },
    )


@router.post("/match", response_class=HTMLResponse)
def create_match(
    request: Request,
    request_id: int = Form(...),
    offer_id: int = Form(...),
) -> HTMLResponse:
    row = repo.load_request(request_id)
    offer = repo.load_offer(offer_id)
    if row is None or offer is None:
        return _error(request, "That request or offer no longer exists.")
    facility = repo.load_facility(row.facility_slug)
    if facility is None:
        return _error(request, f"No facility profile for {row.facility_slug}.")

    screening = screen_offers(facility, [offer], intel_for=supply_lookup)
    candidates = [c for c in screening.ranked if c.offer.id == offer_id]
    back = f"/request/{request_id}/matches"
    if not candidates or candidates[0].verdict.outcome is Outcome.WONT_WORK:
        return _error(
            request, "That device no longer passes the screening for this facility.", back=back
        )
    if candidates[0].verdict.outcome is Outcome.NEEDS_INFO:
        return _error(
            request,
            "That device has open questions. Answer them before asking the donor.",
            back=back,
        )

    try:
        wanted = next(
            (l.quantity for l in row.lines if l.device_type == offer.device_type),
            offer.quantity_available,
        )
        quantity = min(wanted, offer.quantity_available)
        match = request_confirmation(propose_match(row, candidates[0], quantity))
        match_id = repo.save_match(match)
        repo.update_offer(offer.reserve(quantity))
    except (ValueError, RepositoryError) as exc:
        return _error(request, str(exc), back=f"/request/{request_id}/matches")

    return RedirectResponse(url=f"/match/{match_id}", status_code=303)


@router.get("/match/{match_id}", response_class=HTMLResponse)
def match_sent(request: Request, match_id: str) -> HTMLResponse:
    match = repo.load_match(match_id)
    if match is None:
        return _error(request, f"No match with id {match_id}.")
    offer = repo.load_offer(match.offer_id)
    return TEMPLATES.TemplateResponse(
        request=request, name="match_sent.html",
        context={"match": match, "offer": offer},
    )


# ---- donor confirmation ------------------------------------------------

@router.get("/confirm/{token}", response_class=HTMLResponse)
def confirm_page(request: Request, token: str) -> HTMLResponse:
    match = repo.load_match_by_token(token)
    if match is None:
        return _error(request, "That confirmation link is not valid.")
    offer = repo.load_offer(match.offer_id)
    row = repo.load_request(match.request_id)
    facility = repo.load_facility(row.facility_slug) if row else None
    return TEMPLATES.TemplateResponse(
        request=request, name="confirm.html",
        context={
            "match": match, "offer": offer, "facility": facility,
            "trust": repo.load_verification(row.facility_slug) if row else VerificationStatus(),
        },
    )


@router.post("/confirm/{token}", response_class=HTMLResponse)
def confirm_submit(request: Request, token: str, answer: str = Form(...)) -> HTMLResponse:
    match = repo.load_match_by_token(token)
    if match is None:
        return _error(request, "That confirmation link is not valid.")
    offer = repo.load_offer(match.offer_id)
    now = datetime.now()

    try:
        if answer == "yes":
            updated = confirm(match, now)
        else:
            updated = withdraw(match, now)
            if offer is not None:
                repo.update_offer(offer.release(match.quantity))
        repo.update_match_state(match.id, updated.state, now)
    except (ValueError, RepositoryError) as exc:
        return _error(request, str(exc))

    return RedirectResponse(url=f"/confirm/{token}", status_code=303)


# ---- live extraction ----------------------------------------------------

# The five readings a rating plate carries, in the order it prints them.
INSPECTOR_ROWS = (
    ("Model", "model"),
    ("Voltage", "voltage"),
    ("Frequency", "frequency"),
    ("Power", "power"),
    ("Operating temp", "temperature"),
)


def _inspector_rows(device: DeviceFacts) -> list[dict[str, str]]:
    """The extracted facts as rows, each carrying where it came from.

    An unknown field is still a row. Showing it blank with an `unverified` tag
    is the whole point: the gap is the finding.
    """
    supply = _range_text(device.voltage_min_v, device.voltage_max_v, "V")
    frequency = (
        "/".join(str(v) for v in device.frequencies_hz.value) + " Hz"
        if device.frequencies_hz.known else ""
    )
    power = f"{device.power_watts.value} W" if device.power_watts.known else ""
    temperature = _range_text(device.temp_min_c, device.temp_max_c, "\u00b0C")

    values = {
        "model": (device.label if device.model.known or device.manufacturer.known else "",
                  device.model if device.model.known else device.manufacturer),
        "voltage": (supply, device.voltage_min_v),
        "frequency": (frequency, device.frequencies_hz),
        "power": (power, device.power_watts),
        "temperature": (temperature, device.temp_min_c),
    }

    rows = []
    for label, key in INSPECTOR_ROWS:
        shown, fact_used = values[key]
        rows.append({
            "label": label,
            "value": shown,
            "source": fact_used.source.value if shown else "unverified",
            "known": bool(shown),
        })
    return rows


def _range_text(low, high, unit: str) -> str:
    if low.known and high.known:
        return f"{low.value}\u2013{high.value} {unit}"
    if low.known:
        return f"from {low.value} {unit}"
    if high.known:
        return f"to {high.value} {unit}"
    return ""


@router.post("/offer/extract")
async def offer_extract(request: Request) -> JSONResponse:
    """Extract facts from what the donor has typed or photographed, without a reload.

    Called by the inspector panel on the offer page. Never raises at the
    client: a model failure comes back as an error string the panel shows in
    place of the rows, because a blank panel would look like a device with no
    facts rather than a service that did not answer.
    """
    form = await request.form()
    typed = str(form.get("spec_text") or "").strip()
    model_name = str(form.get("model_name") or "").strip()
    combined = "\n".join(p for p in (model_name, typed) if p)

    image_data_url = ""
    upload = form.get("nameplate")
    if upload is not None and getattr(upload, "filename", ""):
        image_data_url = image_to_data_url(
            await upload.read(), getattr(upload, "content_type", "") or ""
        )

    if not combined and not image_data_url:
        return JSONResponse({"rows": [], "message": "Nothing to read yet."})

    try:
        device = extract_device(typed_text=combined, image_data_url=image_data_url)
    except (GenAiError, ValueError) as exc:
        return JSONResponse({"rows": [], "message": str(exc)}, status_code=200)

    return JSONResponse({"rows": _inspector_rows(device), "message": ""})


# ---- recipient verification --------------------------------------------

# Nominatim wants an ISO 3166-1 alpha-2 code to scope a search to one country.
COUNTRY_CODES = {
    "uganda": "ug", "kenya": "ke", "haiti": "ht", "nepal": "np",
    "nigeria": "ng", "ghana": "gh", "tanzania": "tz", "malawi": "mw",
    "india": "in", "bangladesh": "bd", "ethiopia": "et", "zambia": "zm",
}


@router.get("/review", response_class=HTMLResponse)
def review_list(request: Request) -> HTMLResponse:
    """Every registered clinic and how far its claim has been checked."""
    facilities = repo.list_facilities()
    statuses = repo.verifications_for([f.slug for f in facilities])
    return TEMPLATES.TemplateResponse(
        request=request, name="review_list.html",
        context={
            "facilities": facilities,
            "statuses": statuses,
            "default_status": VerificationStatus(),
        },
    )


@router.get("/review/{slug}", response_class=HTMLResponse)
def review_facility(request: Request, slug: str, check: int = 0) -> HTMLResponse:
    """One clinic, with registry evidence if the reviewer asked for it.

    The lookup only runs on `?check=1` so that opening the page never waits on
    a third-party service, and so a reviewer chooses to make the request.
    """
    facility = repo.load_facility(slug)
    if facility is None:
        return _error(request, f"No facility profile for {slug}.")

    candidates: tuple = ()
    if check:
        candidates = find_candidates(
            facility.name,
            COUNTRY_CODES.get(facility.country.strip().lower(), ""),
            facility.latitude,
            facility.longitude,
        )

    return TEMPLATES.TemplateResponse(
        request=request, name="review_facility.html",
        context={
            "facility": facility,
            "status": repo.load_verification(slug),
            "candidates": candidates,
            "checked": bool(check),
            "corroborates": corroborates,
            "supported": any(corroborates(c) for c in candidates),
        },
    )


@router.post("/review/{slug}", response_class=HTMLResponse)
def review_decide(
    request: Request,
    slug: str,
    decision: str = Form(...),
    note: str = Form(default=""),
) -> HTMLResponse:
    """Record a reviewer's decision. Only a person moves a clinic's tier."""
    try:
        tier = Verification(decision)
    except ValueError:
        return _error(request, f"{decision!r} is not a verification tier.")
    if repo.load_facility(slug) is None:
        return _error(request, f"No facility profile for {slug}.")

    repo.set_verification(slug, tier, forms.text(note, "Note", limit=400))
    return RedirectResponse(url=f"/review/{slug}", status_code=303)


# ---- helpers -----------------------------------------------------------

def _point(country: str, city: str) -> GeoPoint:
    latitude, longitude = (country_centroid(country) or (None, None))
    return GeoPoint(country=country, city=(city or "").strip(),
                    latitude=latitude, longitude=longitude)


def _lines_from(types: list[str], quantities: list[str]) -> tuple[RequestLine, ...]:
    """Build the request lines from the form's parallel device/quantity lists.

    Blank rows are dropped so an empty extra row the clinic never filled in is
    not an error. A request with nothing left after that is refused.
    """
    lines = []
    for position, raw_type in enumerate(types):
        if not raw_type.strip():
            continue
        raw_quantity = quantities[position] if position < len(quantities) else "1"
        lines.append(
            RequestLine(
                device_type=raw_type,
                quantity=forms.whole_number(
                    raw_quantity, "How many", default=1, minimum=1
                ),
            )
        )
    if not lines:
        raise FormError("Name at least one kind of equipment you need.")
    return tuple(lines)


def _device_pairs(offer: Offer) -> list[tuple[str, str]]:
    from app.web.presenters import identity_pairs
    return identity_pairs(offer.device)


def _support_kind(device_type: str, label: str) -> str:
    """Ask the model which support need this item meets, if any.

    Never blocks the donor: a model failure falls back to keywords inside
    categorise_support, and an uncategorised offer is still findable by the
    requirement's own search terms.
    """
    kind = categorise_support(device_type, label)
    return kind.value if kind else ""


# ---- [B3] library pick ------------------------------------------------------

from dataclasses import replace as _replace  # noqa: E402

from app.domain.provenance import Provenance, fact  # noqa: E402
from app.reference import device_library  # noqa: E402

# The offer page lists the library through this, read at render time, so a
# rebuilt library file shows without a restart.
TEMPLATES.env.globals["library_entries"] = device_library.load_library

EARLIEST_BUILD_YEAR = 1950

# The rows the prefill shows: (label, DeviceFacts field whose citation the row
# links to, how to show the value). Every row says where its value was read.
PREFILL_ROWS = (
    ("Model", "model"),
    ("Voltage", "voltage_min_v"),
    ("Frequency", "frequencies_hz"),
    ("Power", "power_watts"),
    ("Operating temp", "temp_min_c"),
    ("Max humidity", "humidity_max_pct"),
    ("Needs oxygen", "needs_oxygen"),
    ("Needs distilled water", "needs_distilled_water"),
    ("Needs compressed air", "needs_compressed_air"),
    ("Consumables", "consumables"),
    ("Manual languages", "manual_languages"),
    ("Operating complexity", "operating_complexity"),
)


def _library_entry(slug: str) -> device_library.LibraryEntry:
    picked = device_library.entry(slug)
    if picked is None:
        raise FormError(f"No library entry {slug!r}. Clear the pick and paste the spec instead.")
    return picked


def _build_year(raw: str) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise FormError(f"Year made must be a year such as 2016. You gave {value!r}.")
    year = int(value)
    if not EARLIEST_BUILD_YEAR <= year <= datetime.now().year:
        raise FormError(f"Year made must be between {EARLIEST_BUILD_YEAR} and {datetime.now().year}.")
    return year


def _library_device(picked: device_library.LibraryEntry, year_text: str) -> DeviceFacts:
    """The entry's cited facts plus the donor's word on when this unit was built."""
    device = device_library.to_device_facts(picked)
    year = _build_year(year_text)
    if year is not None:
        device = _replace(device, year_of_manufacture=fact(year, Provenance.DONOR_STATED))
    return device


def _yes_no(value) -> str:
    return "yes" if value else "no"


def _prefill_value(device: DeviceFacts, name: str) -> str:
    if name == "model":
        return device.label if device.model.known else ""
    if name == "voltage_min_v":
        return _range_text(device.voltage_min_v, device.voltage_max_v, "V")
    if name == "temp_min_c":
        return _range_text(device.temp_min_c, device.temp_max_c, "\u00b0C")
    held = getattr(device, name)
    if not held.known:
        return ""
    if name == "frequencies_hz":
        return "/".join(str(v) for v in held.value) + " Hz"
    if name == "power_watts":
        return f"{held.value} W"
    if name == "humidity_max_pct":
        return f"{held.value} %"
    if name.startswith("needs_"):
        return _yes_no(held.value)
    if isinstance(held.value, list):
        return ", ".join(str(v) for v in held.value)
    return str(held.value)


def _prefill_rows(picked: device_library.LibraryEntry) -> list[dict]:
    """Inspector rows for a library entry: label, value, source, link and page."""
    device = device_library.to_device_facts(picked)
    rows = []
    for label, name in PREFILL_ROWS:
        shown = _prefill_value(device, name)
        cited = device_library.citation(picked, name) if shown else {}
        rows.append({
            "label": label,
            "value": shown,
            "source": getattr(device, name).source.value if shown else "unverified",
            "source_url": cited.get("url", ""),
            "page": cited.get("page"),
            "document": cited.get("title", ""),
            "known": bool(shown),
        })
    return rows


@router.post("/offer/prefill")
async def offer_prefill(request: Request) -> JSONResponse:
    """The inspector rows for one library entry, each with its document link.

    Never raises at the client: an unknown slug comes back as a message with
    no rows, the way /offer/extract reports a reader that did not answer.
    """
    form = await request.form()
    slug = str(form.get("slug") or form.get("library_slug") or "").strip()
    picked = device_library.entry(slug) if slug else None
    if picked is None:
        return JSONResponse({"rows": [], "device_type": "", "label": "",
                             "message": "Pick a device from the library." if not slug
                             else f"No library entry {slug!r}."})
    return JSONResponse({
        "rows": _prefill_rows(picked),
        "device_type": picked.device_type,
        "label": picked.label,
        "message": "Facts from the maker's documents. Clear the pick to paste a spec instead.",
    })
