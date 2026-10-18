"""The inventory CSV upload (design §8): screen a whole list, save nothing until asked.

GET  /offer/csv                 the upload form: a file and the clinic to screen against
POST /offer/csv                 screens every row and renders the result table
GET  /offer/csv/sample          the demo inventory, data/sample_inventory.csv
GET  /offer/csv/<token>.csv     the results as a CSV download
GET  /offer/csv/<token>         the result table again
POST /offer/csv/<token>/post    saves the screened rows as Offers, the only write

A screening is held in this process's memory for an hour under a random
token, so the download and the post button read exactly what was shown. An
unknown or expired token is a plain error page, never a stack trace.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.data import marketplace_repo as repo
from app.data.repository import RepositoryError
from app.domain.result import Outcome
from app.matching import csv_screen
from app.matching.csv_screen import CsvError, CsvScreening
from app.matching.supply_order import supply_lookup
from app.web import forms
from app.web.forms import FormError
from app.web.marketplace_routes import DEMO_CLINIC, TEMPLATES

router = APIRouter()

SAMPLE_PATH = Path(__file__).resolve().parents[2] / "data" / "sample_inventory.csv"
CACHE_SECONDS = 3600
MAX_HELD = 100
TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}")

TONE = {
    Outcome.WORKS.value: "good",
    Outcome.WORKS_IF.value: "hold",
    Outcome.WONT_WORK.value: "bad",
    Outcome.NEEDS_INFO.value: "ask",
}


@dataclass
class Held:
    screening: CsvScreening
    created: float
    filename: str = ""
    donor_name: str = ""
    donor_email: str = ""
    # Saved offer ids, in the order of `screening.screened`: posted[i] is row i.
    # A post that stopped partway leaves the rows saved so far here, and the
    # next press saves only the rest (check 1 L5).
    posted: list[int] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(len(self.screening.screened) - len(self.posted), 0)

    @property
    def complete(self) -> bool:
        return bool(self.posted) and self.remaining == 0


_HELD: dict[str, Held] = {}
_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


def _purge(now: float) -> None:
    for token in [t for t, h in _HELD.items() if now - h.created > CACHE_SECONDS]:
        del _HELD[token]
    while len(_HELD) > MAX_HELD:
        del _HELD[min(_HELD, key=lambda t: _HELD[t].created)]


def hold(held: Held) -> str:
    token = secrets.token_hex(16)
    with _LOCK:
        _HELD[token] = held
        _purge(_now())
    return token


def held(token: str) -> Held | None:
    if not TOKEN_PATTERN.fullmatch(token or ""):
        return None
    with _LOCK:
        _purge(_now())
        return _HELD.get(token)


def _error(request: Request, message: str, back: str = "/offer/csv",
           status_code: int = 400) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name="error.html",
        context={"message": message, "back": back}, status_code=status_code,
    )


def _gone(request: Request) -> HTMLResponse:
    return _error(
        request,
        "That screening is not held any more. Results are kept for an hour after the "
        "upload, and only on the server that screened them. Upload the file again.",
        status_code=404,
    )


def _upload_page(request: Request, *, selected: str = "", donor_name: str = "",
                 donor_email: str = "") -> HTMLResponse:
    facilities = repo.list_facilities()
    slugs = [f.slug for f in facilities]
    if selected not in slugs:
        selected = DEMO_CLINIC if DEMO_CLINIC in slugs else (slugs[0] if slugs else "")
    return TEMPLATES.TemplateResponse(
        request=request, name="csv_upload.html",
        context={"facilities": facilities, "selected": selected,
                 "columns": csv_screen.COLUMNS, "has_sample": SAMPLE_PATH.exists(),
                 "donor_name": donor_name, "donor_email": donor_email},
    )


def _result_page(request: Request, token: str, item: Held) -> HTMLResponse:
    screening = item.screening
    counts = [(outcome.value, screening.count(outcome), TONE[outcome.value])
              for outcome in (Outcome.WORKS, Outcome.WORKS_IF, Outcome.NEEDS_INFO,
                              Outcome.WONT_WORK)]
    return TEMPLATES.TemplateResponse(
        request=request, name="csv_result.html",
        context={"token": token, "s": screening, "held": item, "counts": counts,
                 "tone": TONE, "postable": len(screening.screened),
                 "remaining": item.remaining,
                 "hours": CACHE_SECONDS // 3600},
    )


@router.get("/offer/csv", response_class=HTMLResponse)
def csv_form(request: Request, facility: str = "") -> HTMLResponse:
    return _upload_page(request, selected=facility)


@router.post("/offer/csv", response_class=HTMLResponse)
async def csv_screen_upload(
    request: Request,
    inventory: UploadFile | None = File(default=None),
    facility: str = Form(default=""),
    donor_name: str = Form(default=""),
    donor_email: str = Form(default=""),
) -> HTMLResponse:
    """Screen every row against the chosen clinic. Writes nothing to the database."""
    if inventory is None or not inventory.filename:
        return _error(request, "Choose a CSV file to screen.")
    clinic = repo.load_facility(facility.strip()) if facility.strip() else None
    if clinic is None:
        return _error(request, "Choose the clinic to screen against." if not facility.strip()
                      else f"No clinic is registered as {facility.strip()}.")
    data = await inventory.read(csv_screen.MAX_BYTES + 1)
    try:
        screening = csv_screen.screen_csv(
            data, clinic, intel_for=supply_lookup,
            donor_name=forms.text(donor_name, "Donor", limit=200),
            donor_email=forms.text(donor_email, "Email", limit=200),
        )
    except CsvError as exc:
        return _error(request, f"{inventory.filename}: {exc}")
    token = hold(Held(screening=screening, created=_now(), filename=inventory.filename,
                      donor_name=donor_name.strip(), donor_email=donor_email.strip()))
    return _result_page(request, token, held(token))


@router.get("/offer/csv/sample")
def csv_sample() -> Response:
    if not SAMPLE_PATH.exists():
        return Response("No sample inventory is installed.", status_code=404,
                        media_type="text/plain")
    return FileResponse(SAMPLE_PATH, media_type="text/csv", filename="sample_inventory.csv")


@router.get("/offer/csv/{token}.csv")
def csv_download(request: Request, token: str) -> Response:
    item = held(token)
    if item is None:
        return _gone(request)
    stem = Path(item.filename or "inventory").stem or "inventory"
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{stem}-screened-{item.screening.facility_slug}.csv")
    return Response(
        csv_screen.to_csv(item.screening), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/offer/csv/{token}", response_class=HTMLResponse)
def csv_result(request: Request, token: str) -> HTMLResponse:
    item = held(token)
    if item is None:
        return _gone(request)
    return _result_page(request, token, item)


@router.post("/offer/csv/{token}/post", response_class=HTMLResponse)
def csv_post(
    request: Request,
    token: str,
    donor_name: str = Form(default=""),
    donor_email: str = Form(default=""),
) -> HTMLResponse:
    """Save every screened row as an open Offer. The one write in this module.

    Error and blank rows are not posted. A screening is posted once; a second
    press shows what the first one saved instead of saving it again. If saving
    stops partway, the rows already saved stay recorded and the next press
    saves only the rows after them, so none is saved twice.
    """
    item = held(token)
    if item is None:
        return _gone(request)
    back = f"/offer/csv/{token}"
    if item.complete:
        return _result_page(request, token, item)
    try:
        name = forms.text(donor_name, "Your name", required=True, limit=200)
        email = forms.text(donor_email, "Email", required=True, limit=200)
    except FormError as exc:
        return _error(request, str(exc), back=back)
    rows = item.screening.screened
    if not rows:
        return _error(request, "No row of this file was screened, so there is nothing to post.",
                      back=back)
    with _LOCK:
        if item.complete:
            return _result_page(request, token, item)
        try:
            for row in rows[len(item.posted):]:
                offer = replace(row.offer, donor_name=name, donor_email=email)
                item.posted.append(repo.save_offer(offer))
        except RepositoryError as exc:
            return _error(
                request,
                f"Saving stopped after {len(item.posted)} of {len(rows)} offers: {exc}. "
                f"The {len(item.posted)} saved are kept; go back and press post again to "
                f"save the other {item.remaining}, which saves none twice.",
                back=back)
        finally:
            if item.posted:
                item.donor_name, item.donor_email = name, email
    return _result_page(request, token, item)
