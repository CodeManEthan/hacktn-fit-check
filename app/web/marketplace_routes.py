"""Routes for the two-sided platform: offers, facilities, requests, matches."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.ai.categorise import categorise_support
from app.ai.client import GenAiError
from app.ai.extract import extract_device, image_to_data_url
from app.data import marketplace_repo as repo
from app.data.repository import RepositoryError
from app.domain.device import DeviceFacts
from app.domain.facility import FacilityProfile
from app.domain.marketplace import (
    GeoPoint,
    MatchState,
    Offer,
    Request as DonationRequest,
    RequestLine,
)
from app.domain.provenance import Provenance, fact
from app.matching.bundle import build_bundle
from app.matching.coverage import LineCoverage
from app.matching.pipeline import propose_match, rank_candidates
from app.matching.verification import confirm, request_confirmation, withdraw
from app.reference.country_power import country_centroid, lookup
from app.web import forms
from app.web.forms import FormError
from app.web.presenters import TONE_BY_OUTCOME, TONE_BY_STATUS

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()


def _error(request: Request, message: str, back: str = "/") -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name="error.html",
        context={"message": message, "back": back}, status_code=400,
    )


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request=request, name="landing.html", context={})


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
        typed = "\n".join(p for p in (model_name.strip(), spec_text.strip()) if p)
        image_data_url = ""
        if nameplate is not None and nameplate.filename:
            image_data_url = image_to_data_url(await nameplate.read(), nameplate.content_type or "")
        if not typed and not image_data_url:
            raise FormError("Give a model name, paste spec text, or add a nameplate photo.")

        dated = forms.iso_date(expiry_date, "Expiry date")
        offer = Offer(
            donor_name=forms.text(donor_name, "Your name", required=True),
            donor_email=forms.text(donor_email, "Email", required=True),
            device=_with_expiry(
                extract_device(typed_text=typed, image_data_url=image_data_url), dated
            ),
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
            staff_languages=forms.comma_list(str(form.get("staff_languages", ""))) or ("English",),
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
    for line in row.lines:
        offers = repo.open_offers_for_type(line.device_type)
        candidates = rank_candidates(facility, offers)
        coverages.append(
            LineCoverage(line=line, candidates=candidates, screened=len(offers))
        )
        for c in candidates:
            bundles[c.offer.id] = build_bundle(c.verdict, repo.offers_for_support)

    return TEMPLATES.TemplateResponse(
        request=request, name="matches.html",
        context={
            "request_row": row, "facility": facility,
            "coverages": coverages, "bundles": bundles,
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

    candidates = [c for c in rank_candidates(facility, [offer]) if c.offer.id == offer_id]
    if not candidates:
        return _error(request, "That device no longer passes the screening for this facility.")

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
        context={"match": match, "offer": offer, "facility": facility},
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


def _with_expiry(device: DeviceFacts, expiry: date | None) -> DeviceFacts:
    """Attach the donor's expiry date, leaving it unknown when none was given."""
    if expiry is None:
        return device
    return replace(device, expiry_date=fact(expiry, Provenance.DONOR_STATED))


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
