"""What the page routers share: the templates, the error page, the demo clinic
and the library pick that both the offer form and the prefill read.

marketplace_routes, packet_routes, library_routes, pages_routes and
csv_routes import from here, so none of them imports another.
"""

from __future__ import annotations

from dataclasses import replace as _replace
from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.data import seeded
from app.domain.device import DeviceFacts
from app.domain.provenance import Provenance, fact
from app.domain.text import plural
from app.reference import device_library
from app.web import presenters
from app.web.forms import FormError
from app.web.presenters import rule_count

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["rule_count"] = rule_count()
TEMPLATES.env.globals["offer_name"] = presenters.offer_name
TEMPLATES.env.globals["supply_url"] = presenters.supply_url
TEMPLATES.env.filters["plural"] = plural
TEMPLATES.env.filters["stamp"] = presenters.outcome_stamp
TEMPLATES.env.globals["donor_tag"] = presenters.donor_tag
TEMPLATES.env.globals["clinic_tag"] = presenters.clinic_tag
TEMPLATES.env.globals["seeded_clinic"] = seeded.is_seeded_clinic
# The offer page lists the library through this, read at render time, so a
# rebuilt library file shows without a restart.
TEMPLATES.env.globals["library_entries"] = device_library.load_library


def _error(request: Request, message: str, back: str = "/") -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name="error.html",
        context={"message": message, "back": back}, status_code=400,
    )


DEMO_CLINIC = "uganda-district"

EARLIEST_BUILD_YEAR = 1950


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
