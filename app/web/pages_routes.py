"""Standing pages and the clinic's list upload (design §13, [B14]).

About, contact and help carry no state of their own. The upload turns a
spreadsheet or a typed list into one request and sends the clinic to that
request's matches page, where every line is screened. Copied from upstream
a862cc6 (marketplace_routes.py), re-pointed at this tree's data.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.checks.runner import ALL_CHECKS, ROUTE_CHECKS
from app.data import marketplace_repo as repo
from app.data.repository import RepositoryError
from app.domain.marketplace import Request as DonationRequest
from app.intake.freetext import parse_list
from app.intake.tables import IntakeError, parse_table
from app.reference.country_power import country_count
from app.reference.device_library import load_library
from app.reference.import_rules import load_import_rules
from app.web.forms import FormError
from app.web.marketplace_routes import TEMPLATES, _error

router = APIRouter()

# A note longer than this is cut, as the request form cuts it.
NOTE_LIMIT = 600


def check_count() -> int:
    """How many checks a verdict runs, read from the registry, never typed."""
    return len(ALL_CHECKS) + len(ROUTE_CHECKS)


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name="about.html",
        context={
            "check_count": check_count(),
            "library_count": len(load_library()),
            "country_count": country_count(),
            "rule_countries": sorted(load_import_rules()),
        },
    )


@router.get("/contact", response_class=HTMLResponse)
def contact_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request=request, name="contact.html", context={})


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name="help.html", context={"check_count": check_count()},
    )


@router.get("/request/upload", response_class=HTMLResponse)
def request_upload_form(request: Request, facility: str = "") -> HTMLResponse:
    """Arrive with a spreadsheet or a sentence instead of typing a form.

    `facility` is the slug the register handler sends a new clinic on with,
    so the clinic it just registered is the one chosen.
    """
    return TEMPLATES.TemplateResponse(
        request=request, name="request_upload.html",
        context={"facilities": repo.list_facilities(), "chosen": facility},
    )


@router.post("/request/upload", response_class=HTMLResponse)
async def request_upload_submit(request: Request) -> HTMLResponse:
    """Turn an uploaded table or a typed list into one request.

    Both paths end at repo.save_request, so however a clinic arrives there is
    still exactly one way a request is stored. The typed path asks the model
    only to list kinds and counts; nothing it returns becomes a device fact.
    """
    form = await request.form()
    facility_slug = str(form.get("facility_slug") or "").strip()
    typed = str(form.get("wanted_text") or "").strip()
    upload = form.get("list_file")

    try:
        if not facility_slug:
            raise FormError("Choose which facility this request is for.")
        if repo.load_facility(facility_slug) is None:
            raise FormError("That facility is not registered. Register it first.")

        if upload is not None and getattr(upload, "filename", ""):
            lines = parse_table(await upload.read(), upload.filename)
        elif typed:
            lines = parse_list(typed, vocabulary=repo.known_device_types())
        else:
            raise FormError("Upload a list, or type what you need.")

        request_id = repo.save_request(
            DonationRequest(facility_slug=facility_slug, lines=lines, note=typed[:NOTE_LIMIT])
        )
    except (FormError, IntakeError, ValueError, RepositoryError) as exc:
        return _error(request, str(exc), back="/request/upload")

    return RedirectResponse(url=f"/request/{request_id}/matches", status_code=303)
