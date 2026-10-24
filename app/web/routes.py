"""The legacy single-device routes still in use: POST /check, /verdict/<id> and /impact.

The old /donor form and its /clinic picker were removed; GET /clinic is the
clinic registration form in marketplace_routes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.ai import impact
from app.ai.client import CLIENT, GenAiError
from app.checks import check_fit
from app.data import repository
from app.data.repository import RepositoryError
from app.data.seed import get_facility
from app.matching.supply_order import supply_lookup
from app.web.presenters import (
    TONE_BY_OUTCOME,
    TONE_BY_OUTCOME_VALUE,
    TONE_BY_STATUS,
    device_kind,
    identity_pairs,
    rule_count,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["rule_count"] = rule_count()
router = APIRouter()

def _error(request: Request, message: str, back: str = "/") -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="error.html",
        context={"message": message, "back": back},
        status_code=400,
    )


@router.post("/check", response_class=HTMLResponse)
def run_check(
    request: Request,
    device_id: int = Form(...),
    facility_slug: str = Form(...),
) -> HTMLResponse:
    stored = repository.load_device(device_id)
    if stored is None:
        return _error(request, "That device is no longer in the database.")

    try:
        facility = get_facility(facility_slug)
    except KeyError as exc:
        return _error(request, str(exc), back="/")

    try:
        # No device type is stored on this path, so only a recorded answer
        # for this exact model can apply.
        intel = supply_lookup(stored, "", facility.country)
        verdict = check_fit(stored, facility, intel)
        verdict_id = repository.save_verdict(
            device_id=device_id,
            device=stored,
            facility=facility,
            verdict=verdict,
            intel=intel,
        )
    except (GenAiError, RepositoryError) as exc:
        return _error(request, str(exc), back="/")

    return RedirectResponse(url=f"/verdict/{verdict_id}", status_code=303)


@router.get("/verdict/{verdict_id}", response_class=HTMLResponse)
def verdict_page(request: Request, verdict_id: str) -> HTMLResponse:
    try:
        record = repository.load_verdict(verdict_id)
    except RepositoryError as exc:
        return _error(request, str(exc))
    if record is None:
        return _error(request, f"No verdict with id {verdict_id}.")

    facility = get_facility(record["facility_slug"])
    return TEMPLATES.TemplateResponse(
        request=request,
        name="verdict.html",
        context={
            "record": record,
            "verdict": record["verdict"],
            "device": record["device"],
            "intel": record["intel"],
            "facility": facility,
            "pairs": identity_pairs(record["device"]),
            "kind": device_kind(record["device"]),
            "symbol": TONE_BY_STATUS,
            "tone": TONE_BY_OUTCOME,
        },
    )


def _impact_client():
    """The chat client the impact paragraph is written with; tests replace it."""
    return CLIENT


@router.get("/impact", response_class=HTMLResponse)
def impact_page(request: Request) -> HTMLResponse:
    try:
        reasons = repository.failures_by_reason()
        figures = impact.figures()
        context = {
            "figures": figures,
            "summary": impact.summary(figures, _impact_client()),
            "counts": repository.count_by_outcome(),
            "reasons": reasons,
            "peak": max((n for _, n in reasons), default=1),
            "recent": repository.recent_verdicts(),
            "tone": TONE_BY_OUTCOME_VALUE,
        }
    except RepositoryError as exc:
        return _error(request, str(exc), back="/impact")
    return TEMPLATES.TemplateResponse(
        request=request, name="impact.html", context=context
    )
