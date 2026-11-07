"""Routes for the two-sided platform: offers, facilities, requests, matches."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.ai.categorise import categorise_support
from app.ai.client import GenAiError
from app.ai.extract import extract, extract_device, image_to_data_url
from app.data import marketplace_repo as repo
from app.data import seeded
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
from app.matching.verification import (
    confirm,
    request_confirmation,
    run_timeout_sweep,
    withdraw,
)
from app.reference.country_power import country_centroid, lookup
from app.reference.facility_registry import corroborates, find_candidates
from app.web import demo_guard, forms, inspector, presenters
from app.web.forms import FormError
from app.web.presenters import advisory_for, TONE_BY_OUTCOME, TONE_BY_STATUS
from app.web.shared import TEMPLATES, _error, _library_device, _library_entry, _refused

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    """The landing page: two doors and the operator between them.

    The seeded-clinic strip is gone, so the page needs no data; the role
    cookie is read by base.html from the request.
    """
    return TEMPLATES.TemplateResponse(request=request, name="landing.html", context={})


# The worked example is the seeded Kayunga request for one oxygen concentrator.
# Its id differs from one database to the next, so the landing links here.
def example_request_id() -> int | None:
    """The seeded Kayunga request's id, found as the seed finds it, or None."""
    try:
        return seeded.kayunga_request_id()
    except repo.RepositoryError:
        return None


@router.get("/example", response_class=HTMLResponse)
def example(request: Request):
    """Open the seeded request's matches page, or say the example is not loaded."""
    request_id = example_request_id()
    if request_id is None:
        return TEMPLATES.TemplateResponse(
            request=request, name="example_missing.html", context={}, status_code=404,
        )
    return RedirectResponse(url=f"/request/{request_id}/matches", status_code=303)


# ---- role toggle --------------------------------------------------------

ROLES = ("donor", "clinic", "nonprofit")
ROLE_COOKIE_DAYS = 30
HELD_MESSAGE = "That device's units are already held for another request."
# Where each button in the bar lands: the party's own intake or desk. A
# clinic starts by registering (GET /clinic); a registered one then goes on
# to the list upload, and /request stays one link away for known clinics.
ROLE_HOME = {"donor": "/offer", "clinic": "/clinic", "nonprofit": "/review"}


@router.post("/role")
def set_role(role: str = Form(default=""), back: str = Form(default="/")) -> RedirectResponse:
    """Remember who is looking, as a cookie, and open that party's home page.

    The cookie recolours the bar and the primary buttons and hides nothing
    (no logins). A button in a bar reads as a destination, so pressing Clinic
    on the donor's form opens the clinic registration form rather than tinting
    the donor's form teal. An unknown role clears the cookie and goes back.
    """
    if role in ROLES:
        response = RedirectResponse(url=ROLE_HOME[role], status_code=303)
        response.set_cookie("role", role, max_age=ROLE_COOKIE_DAYS * 86400, samesite="lax")
    else:
        response = RedirectResponse(url=_local_path(back) or "/", status_code=303)
        response.delete_cookie("role")
    return response


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
    extraction_id: str = Form(default=""),
    cleared_fields: str = Form(default=""),
) -> HTMLResponse:
    try:
        device: DeviceFacts | None = None
        posted = await request.form()
        library_slug = str(posted.get("library_slug") or "").strip()
        if library_slug:
            # A library pick: the facts are rebuilt from the library, never
            # read from the form, so a cited value cannot be edited by hand.
            # The donor adds only what is about this unit: its build year here,
            # quantity and location below.
            picked = _library_entry(library_slug)
            device = _library_device(picked, str(posted.get("year_of_manufacture") or ""))
            device_type = device_type.strip() or picked.device_type
            model_name = model_name.strip() or picked.label

        # Facts the inspector already read and showed, reloaded from the
        # server's cache; the donor's "wrong" marks clear those fields. The
        # client posts only the id and field names, never a value.
        if device is None and extraction_id.strip():
            held = repo.load_extraction(extraction_id.strip())
            if held is None:
                raise FormError(
                    "The facts read for this offer have expired (they are kept for an "
                    "hour). Press \"Read these facts now\" again, then post."
                )
            device = inspector.clear_fields(held.facts, inspector.parse_cleared(cleared_fields))

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
    """The clinic's profile form; country is a list so the grid lookup never misses."""
    # Imported here, not at the top: this handler is the only reader.
    from app.reference.country_power import known_countries

    return TEMPLATES.TemplateResponse(
        request=request, name="facility_form.html",
        context={"countries": known_countries()},
    )


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
            ambient_temp_max_c=forms.decimal(
                str(form.get("ambient_temp_max_c", "")), "Temperature",
                default=30.0, minimum=-50, maximum=70, unit=" C"),
            humidity_max_pct=forms.decimal(
                str(form.get("humidity_max_pct", "")), "Humidity",
                default=70.0, minimum=0, maximum=100, unit="%"),
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
        # A registration is always a new clinic: if the name's slug is taken it
        # gets a free one rather than replacing that clinic's row.
        slug = repo.save_facility(profile, new=True)
    except (FormError, RepositoryError) as exc:
        return _error(request, str(exc), back="/clinic")

    # A registered clinic goes straight to the list upload.
    return RedirectResponse(url=f"/request/upload?facility={slug}", status_code=303)


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
        if repo.load_facility(row.facility_slug) is None:
            # Same guard as the list upload: a request for a clinic that is not
            # registered would only fail later, on its matches page.
            return _error(request, "That facility is not registered. Register it first.",
                          back="/clinic")
        request_id = repo.save_request(row)
    except (FormError, ValueError, RepositoryError) as exc:
        return _error(request, str(exc), back="/request")
    return RedirectResponse(url=f"/request/{request_id}/matches", status_code=303)


@router.get("/request/{request_id}/matches", response_class=HTMLResponse)
def matches(request: Request, request_id: int) -> HTMLResponse:
    run_timeout_sweep()
    row = repo.load_request(request_id)
    if row is None:
        return _error(request, f"No request with id {request_id}.")
    facility = repo.load_facility(row.facility_slug)
    if facility is None:
        return _error(request, f"No facility profile for {row.facility_slug}.")

    coverages = []
    locked: set[int] = set()
    whole_request = demo_guard.request_locked(request_id)
    bundles: dict[int, object] = {}
    advisory: dict[int, object] = {}
    questions: dict[int, list[dict]] = {}
    here = request.url.path
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
            if whole_request or demo_guard.offer_locked(c.offer):
                locked.add(c.offer.id)
            bundles[c.offer.id] = build_bundle(c.verdict, facility, repo.offers_for_support)
            if c.verdict.outcome is Outcome.NEEDS_INFO:
                questions[c.offer.id] = presenters.open_questions(
                    c.verdict, c.offer.device, c.intel, c.offer.device_type,
                    facility.country, back=here)
        advisory.update({c.offer.id: advisory_for(c) for c in screening.rejected})

    return TEMPLATES.TemplateResponse(
        request=request, name="matches.html",
        context={
            "request_row": row, "facility": facility,
            "coverages": coverages, "bundles": bundles, "advisory": advisory,
            "questions": questions, "locked": locked,
            "symbol": TONE_BY_STATUS, "tone": TONE_BY_OUTCOME,
        },
    )


@router.get("/request/{request_id}/offer/{offer_id}", response_class=HTMLResponse)
def candidate_page(request: Request, request_id: int, offer_id: int) -> HTMLResponse:
    """One offer's verdict for one request's clinic.

    The offer is screened again here, with the same supply order the matches
    page uses, so a rejected or a needs-info offer renders as well as a
    survivor: stamp, every check with the citation of the facts it read, the
    send-with list and each add-on's own plate check, the open questions
    (the nonprofit's link to /supply), and the advisory when rejected.
    """
    row = repo.load_request(request_id)
    if row is None:
        return _error(request, f"No request with id {request_id}.")
    back = f"/request/{request_id}/matches"
    offer = repo.load_offer(offer_id)
    if offer is None:
        return _error(request, f"No offer with id {offer_id}.", back=back)
    facility = repo.load_facility(row.facility_slug)
    if facility is None:
        return _error(request, f"No facility profile for {row.facility_slug}.")

    screening = screen_offers(facility, [offer], intel_for=supply_lookup)
    found = [*screening.ranked, *screening.rejected]
    if not found:
        return _error(
            request,
            f"Offer {offer_id} is no longer open (reserved, withdrawn or past its date), "
            "so there is nothing to screen.",
            back=back,
        )
    candidate = found[0]
    verdict = candidate.verdict
    rejected = verdict.outcome is Outcome.WONT_WORK
    here = request.url.path
    return TEMPLATES.TemplateResponse(
        request=request, name="candidate.html",
        context={
            "request_row": row, "facility": facility, "c": candidate,
            "offer": offer, "verdict": verdict, "rejected": rejected,
            "rows": presenters.check_rows(verdict, offer.device, candidate.intel,
                                          facility.country),
            "questions": presenters.open_questions(
                verdict, offer.device, candidate.intel, offer.device_type,
                facility.country, back=here),
            "addons": presenters.addon_states(
                build_bundle(verdict, facility, repo.offers_for_support)),
            "advisory": {offer.id: advisory_for(candidate)} if rejected else {},
            "pairs": presenters.identity_pairs(offer.device),
            "proposable": verdict.outcome in (Outcome.WORKS, Outcome.WORKS_IF),
            "locked": demo_guard.request_locked(request_id) or demo_guard.offer_locked(offer),
            "packet_url": f"/packet/{offer.id}?facility={facility.slug}",
            "back": back,
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
    back = f"/request/{request_id}/matches"
    if demo_guard.request_locked(request_id) or demo_guard.offer_locked(offer):
        return _refused(request, back=back)
    facility = repo.load_facility(row.facility_slug)
    if facility is None:
        return _error(request, f"No facility profile for {row.facility_slug}.")
    line = repo.line_for_offer(row.lines, offer.device_type)
    if line is None:
        return _error(
            request, "That offer is not for any of the device types on this request.", back=back
        )
    if offer.quantity_available <= 0:
        return _error(request, HELD_MESSAGE, back=back)

    screening = screen_offers(facility, [offer], intel_for=supply_lookup)
    candidates = [c for c in screening.ranked if c.offer.id == offer_id]
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
        quantity = min(line.quantity, offer.quantity_available)
        match = request_confirmation(propose_match(row, candidates[0], quantity))
        match_id = repo.save_match_reserving(match)
    except repo.StockHeld:
        return _error(request, HELD_MESSAGE, back=back)
    except (ValueError, RepositoryError) as exc:
        return _error(request, str(exc), back=f"/request/{request_id}/matches")

    return RedirectResponse(url=f"/match/{match_id}", status_code=303)


@router.get("/match/{match_id}", response_class=HTMLResponse)
def match_sent(request: Request, match_id: str) -> HTMLResponse:
    run_timeout_sweep()
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
            "locked": demo_guard.match_locked(match),
        },
    )


@router.post("/confirm/{token}", response_class=HTMLResponse)
def confirm_submit(request: Request, token: str, answer: str = Form(...)) -> HTMLResponse:
    match = repo.load_match_by_token(token)
    if match is None:
        return _error(request, "That confirmation link is not valid.")
    if demo_guard.match_locked(match):
        return _refused(request, back=f"/confirm/{token}")
    now = datetime.now()

    try:
        updated = confirm(match, now) if answer == "yes" else withdraw(match, now)
        moved = repo.respond_to_match(
            match, updated.state, now, release=updated.state is MatchState.WITHDRAWN
        )
    except (ValueError, RepositoryError) as exc:
        return _error(request, str(exc))
    if not moved:
        return _error(request, "This match was already answered.")

    return RedirectResponse(url=f"/confirm/{token}", status_code=303)


# ---- live extraction ----------------------------------------------------

@router.post("/offer/extract")
async def offer_extract(request: Request) -> JSONResponse:
    """Extract facts from what the donor has typed or photographed, without a reload.

    Called by the inspector panel on the offer page. The extraction is held
    server-side for an hour under `extraction_id`; posting the offer with
    that id reuses exactly these facts. Each row carries its source and the
    link to the page it was read from. Never raises at the client: a model
    failure comes back as an error string the panel shows in place of the
    rows, because a blank panel would look like a device with no facts rather
    than a service that did not answer.
    """
    form = await request.form()
    typed = str(form.get("spec_text") or "").strip()
    model_name = str(form.get("model_name") or "").strip()
    combined = "\n".join(p for p in (model_name, typed) if p)

    try:
        image_data_url = ""
        upload = form.get("nameplate")
        if upload is not None and getattr(upload, "filename", ""):
            image_data_url = image_to_data_url(
                await upload.read(), getattr(upload, "content_type", "") or ""
            )
        if not combined and not image_data_url:
            return JSONResponse({"rows": [], "extraction_id": "", "message": "Nothing to read yet."})
        extraction = extract(typed_text=combined, image_data_url=image_data_url)
    except (GenAiError, ValueError) as exc:
        return JSONResponse({"rows": [], "extraction_id": "", "message": str(exc)}, status_code=200)

    rows = inspector.inspector_rows(extraction.facts, extraction.citations)
    message = _extract_message(extraction, rows)
    try:
        extraction_id = repo.save_extraction(extraction.facts, extraction.stored_document())
    except RepositoryError:
        # The rows are still worth showing; posting re-reads the source.
        extraction_id = ""
        message = f"{message} These facts could not be held, so posting reads the source again."
    return JSONResponse({
        "rows": rows,
        "extraction_id": extraction_id,
        "library_slug": extraction.library_slug,
        "device_type": extraction.device_type,
        "message": message,
    })


def _extract_message(extraction, rows: list[dict]) -> str:
    known = sum(1 for r in rows if r["known"])
    if extraction.library_hit:
        return (
            f"{extraction.facts.label}: facts from the device library's manufacturer "
            f"documents. Each row links to the page it was read from."
        )
    if not known:
        return (
            "No library entry for this model, and nothing checkable was printed in "
            "what you gave. Every fact stays unknown and comes back as a question."
        )
    return (
        "No library entry for this model. These facts were read from what you gave; "
        "a value not printed there was left unknown."
    )


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
            "locked": demo_guard.clinic_locked(slug),
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
    if demo_guard.clinic_locked(slug):
        return _refused(request, back=f"/review/{slug}")
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


# ---- recorded supply answers -------------------------------------------

from urllib.parse import quote, urlencode  # noqa: E402

from app.domain.supply_answer import ANY_MODEL, SupplyAnswer  # noqa: E402

SUPPLY_QUESTIONS = (
    ("consumables_available", "Are its consumables (filters, circuits, cartridges) sold here?"),
    ("spare_parts_available", "Can spare parts for it be bought here?"),
    ("sold_locally", "Is this kind of device sold here, new?"),
    ("manufacturer_supported", "Does the manufacturer or a local distributor still support it?"),
)
SUPPLY_CHOICES = {"yes": True, "no": False, "unknown": None, "": None}


def _local_path(back: str) -> str:
    """Only a path on this site: never another host, so the form is no open redirect."""
    back = (back or "").strip()
    if back.startswith("/") and not back.startswith(("//", "/\\")):
        return back
    return ""


def _supply_page(request: Request, country: str, values: dict, *,
                 saved: bool = False, error: str = "", status_code: int = 200) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name="supply_form.html", status_code=status_code,
        context={"country": country, "questions": SUPPLY_QUESTIONS,
                 "saved": saved, "error": error, "locked": demo_guard.supply_locked(),
                 **values},
    )


@router.get("/supply", response_class=HTMLResponse)
def supply_index(request: Request) -> HTMLResponse:
    """The nonprofit's supply desk: one form per country a registered clinic is in."""
    countries = sorted({f.country for f in repo.list_facilities() if f.country})
    return TEMPLATES.TemplateResponse(
        request=request, name="supply_index.html", context={"countries": countries},
    )


@router.get("/supply/{country}", response_class=HTMLResponse)
def supply_form(request: Request, country: str, device_type: str = "", model: str = "",
                back: str = "", saved: int = 0) -> HTMLResponse:
    """The nonprofit's four answers for one device type (or model) in one country.

    An answer already stored for exactly this type, model and country fills
    the form in, so a correction starts from what was written.
    """
    answers: dict = {}
    recorded_by = note = ""
    try:
        stored = repo.load_supply_answer(device_type, model, country) if device_type else None
    except repo.RepositoryError:
        stored = None
    if (stored and stored.device_type.lower() == device_type.strip().lower()
            and stored.model.lower() == model.strip().lower()):
        answers = {name: getattr(stored, name) for name, _ in SUPPLY_QUESTIONS}
        recorded_by, note = stored.recorded_by, stored.note
    return _supply_page(request, country, {
        "device_type": device_type, "model": model, "back": _local_path(back),
        "answers": answers, "recorded_by": recorded_by, "note": note,
    }, saved=bool(saved))


@router.post("/supply/{country}", response_class=HTMLResponse)
def supply_save(
    request: Request,
    country: str,
    device_type: str = Form(default=""),
    model: str = Form(default=""),
    consumables_available: str = Form(default=""),
    spare_parts_available: str = Form(default=""),
    sold_locally: str = Form(default=""),
    manufacturer_supported: str = Form(default=""),
    recorded_by: str = Form(default=""),
    note: str = Form(default=""),
    back: str = Form(default=""),
) -> HTMLResponse:
    """Write one recorded answer, then go back to the page that asked."""
    if demo_guard.supply_locked():
        return _refused(request, back=f"/supply/{quote(country)}")
    raw = {"consumables_available": consumables_available,
           "spare_parts_available": spare_parts_available,
           "sold_locally": sold_locally, "manufacturer_supported": manufacturer_supported}
    values = {"device_type": device_type, "model": model, "back": _local_path(back),
              "recorded_by": recorded_by, "note": note,
              "answers": {k: SUPPLY_CHOICES.get(v.strip().lower()) for k, v in raw.items()}}
    try:
        unknown = [k for k, v in raw.items() if v.strip().lower() not in SUPPLY_CHOICES]
        if unknown:
            raise FormError("Each question takes yes, no or don't know.")
        answer = SupplyAnswer(
            device_type=forms.text(device_type, "Kind of equipment", required=True, limit=120),
            model=forms.text(model, "Model", limit=200) or ANY_MODEL,
            country=forms.text(country, "Country", required=True, limit=100),
            recorded_by=forms.text(recorded_by, "Who is answering", required=True, limit=200),
            note=forms.text(note, "How you know", limit=2000),
            **values["answers"],
        )
        repo.save_supply_answer(answer)
    except FormError as exc:
        return _supply_page(request, country, values, error=str(exc), status_code=400)
    except repo.RepositoryError as exc:
        return _supply_page(request, country, values, error=str(exc), status_code=500)

    if values["back"]:
        return RedirectResponse(url=values["back"], status_code=303)
    query = urlencode({"device_type": answer.device_type, "model": answer.model, "saved": 1})
    return RedirectResponse(url=f"/supply/{quote(answer.country)}?{query}", status_code=303)

