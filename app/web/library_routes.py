"""The library pick's prefill: POST /offer/prefill.

The offer page posts a library slug here and gets back the inspector rows
for that entry, each linked to the page of the document it cites.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.ai.extract import library_citations
from app.reference import device_library
from app.web import inspector

router = APIRouter()


def _prefill_rows(picked: device_library.LibraryEntry) -> list[dict]:
    """The inspector's own rows for a library entry, each linked to its page.

    The same row set and columns as /offer/extract (one builder,
    `inspector.inspector_rows`); the page shows no "wrong" mark on a library
    pick, because a cited value is corrected by a document, not by hand.
    """
    device = device_library.to_device_facts(picked)
    return inspector.inspector_rows(device, library_citations(picked))


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
