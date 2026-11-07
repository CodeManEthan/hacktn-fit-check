"""The handover packet: GET /packet/<offer id>?facility=<clinic slug>.

A one-page card built from the device's manual by the chat model, gated
against the manual's own text, translated for the clinic's staff when they
read another language, and stored so the next visit reads it back.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.ai.client import CLIENT as _PACKET_CHAT, GenAiError
from app.ai.packet import Card, build_card, card_from_dict, gate, split_pages
from app.ai.translate import ENGINE_CHAT, Target, pick_target, translate_card
from app.checks import check_fit
from app.data import marketplace_repo as repo
from app.data.repository import RepositoryError
from app.domain.device import DeviceFacts
from app.domain.facility import FacilityProfile
from app.domain.language import normalise_languages
from app.domain.marketplace import Offer
from app.domain.result import Verdict
from app.matching.pipeline import screen_offers
from app.matching.supply_order import supply_lookup
from app.reference.device_library import document_text
from app.reference.import_rules import documents_required
from app.web import demo_guard
from app.web.shared import TEMPLATES, _error, _refused

router = APIRouter()

MANUAL_KIND = "manual"
MANUAL_TITLE_WORDS = ("manual", "instruction", "guide", "ifu")
# The kinds that are editions of the manufacturer's manual. A registry
# record, a shop's product page, a declaration or a notice is about the
# device, not an edition of its manual. A document stored before kinds were
# carried (no kind) is still listed.
EDITION_KINDS = frozenset({"manual", "service manual"})


def _packet_chat():
    """The chat client the card is built with; tests replace it."""
    return _PACKET_CHAT


def _packet_language():
    """The OCI Language client; None builds one from the settings. Tests replace it."""
    return None


def _packet_documents(device: DeviceFacts) -> list[dict]:
    documents = device.documents
    if not documents.known or not isinstance(documents.value, (list, tuple)):
        return []
    return [d for d in documents.value if isinstance(d, dict)]


def _packet_manual(documents: list[dict]) -> dict | None:
    """The first document of kind manual, else the first titled like one, else the first."""
    if not documents:
        return None
    by_kind = next((d for d in documents if str(d.get("kind", "")).lower() == MANUAL_KIND), None)
    by_title = next((d for d in documents
                     if any(w in str(d.get("title", "")).lower() for w in MANUAL_TITLE_WORDS)),
                    None)
    return by_kind or by_title or documents[0]


def _packet_verdict(offer: Offer, clinic: FacilityProfile) -> Verdict:
    """The verdict stored on a match of this offer for this clinic, else a fresh one.

    The fresh one is screened the way the candidate page screens it (the
    supply order's intel and the shipment estimate), so the send-with list
    carries the same conditions. An offer no longer open is not screened;
    it is checked with the same intel and no shipment.
    """
    for row in repo.list_open_requests():
        if row.facility_slug != clinic.slug or row.id is None:
            continue
        stored = [m for m in repo.matches_for_request(row.id) if m.offer_id == offer.id]
        if stored:
            return stored[-1].verdict
    screening = screen_offers(clinic, [offer], intel_for=supply_lookup)
    found = [*screening.ranked, *screening.rejected]
    if found:
        return found[0].verdict
    return check_fit(offer.device, clinic, supply_lookup(offer.device, offer.device_type,
                                                         clinic.country))


def _packet_translation(card: Card, target: Target) -> dict | None:
    """The stored form of the translated card, or None when none was made."""
    if not target.language or card.empty:
        return None
    translated, result = translate_card(card, target.language, _packet_language(),
                                        chat=_packet_chat())
    if translated is None:
        return {"language": result.language, "engine": result.engine, "flag": result.flag}
    return {
        "language": result.language, "engine": result.engine, "flag": result.flag,
        "provenance": result.provenance.value if result.provenance else "",
        "card": translated.to_dict(),
    }


def _packet_header(target: Target, translated: dict | None) -> tuple[str, bool]:
    """The packet's header line, and whether it is a warning."""
    if not target.language:
        return target.note, target.note != "English, as requested"
    if not translated or not translated.get("card"):
        flag = (translated or {}).get("flag") or f"No {target.language} card; English supplied"
        return flag, True
    line = f"{target.language} beside English, {translated.get('flag')}"
    if translated.get("engine") == ENGINE_CHAT:
        line += " (translated by the chat model: model knowledge, not OCI Language)"
    if target.note:
        line = f"{target.note}. {line}"
    return line, False


@router.get("/packet/{offer_id}", response_class=HTMLResponse)
def packet_page(
    request: Request, offer_id: int, facility: str = "", rebuild: int = 0
) -> HTMLResponse:
    """The handover packet for one offer at one clinic."""
    offer = repo.load_offer(offer_id)
    if offer is None:
        return _error(request, f"No offer with id {offer_id}.")
    clinic = repo.load_facility(facility) if facility else None
    if clinic is None:
        return _error(request, "Name the clinic: /packet/<offer id>?facility=<clinic slug>.")
    locked = demo_guard.offer_locked(offer)
    if rebuild and locked:
        return _refused(request, back=f"/packet/{offer_id}?facility={clinic.slug}")

    staff = normalise_languages(clinic.staff_languages)
    target = pick_target(staff)
    documents = _packet_documents(offer.device)
    manual = _packet_manual(documents)
    problem = ""
    card: Card | None = None
    translated: dict | None = None

    try:
        if rebuild:
            repo.clear_packet(offer_id, clinic.slug)
        stored = repo.load_packet(offer_id, clinic.slug)
        built = False
        if stored is not None:
            card, translated = card_from_dict(stored.card), stored.translated
        elif manual is not None:
            text = document_text(str(manual.get("slug", "")), str(manual.get("file", "")))
            if not text:
                problem = "The manual's text is not in the library, so no card could be built."
            else:
                pages = split_pages(text)
                card = gate(build_card(text, pages, offer.device.label, _packet_chat()),
                            text, pages)
                built = True
        stale = card is not None and target.language is not None and not (
            translated and translated.get("card")
            and translated.get("language") == target.language)
        if stale:
            translated = _packet_translation(card, target)
        # A failed translation is shown but not cached, so the next visit retries it.
        keep = translated if translated and translated.get("card") else None
        if card is not None and (built or (stale and keep is not None)):
            repo.save_packet(offer_id, clinic.slug, target.language or "English",
                             card.to_dict(), keep)
    except GenAiError as exc:
        problem = f"The card could not be built: {exc}"
    except RepositoryError as exc:
        problem = str(exc)

    header, header_warns = _packet_header(target, translated)
    verdict = _packet_verdict(offer, clinic)
    editions = [d for d in documents
                if str(d.get("kind") or "").lower() in EDITION_KINDS | {""}
                and set(normalise_languages(d.get("languages") or ())) & set(staff)]
    return TEMPLATES.TemplateResponse(
        request=request, name="packet.html",
        context={
            "offer": offer, "facility": clinic, "staff": staff,
            "header": header, "header_warns": header_warns,
            "editions": editions, "documents": documents, "manual": manual,
            "card": card, "problem": problem,
            "translated": card_from_dict(translated["card"])
            if translated and translated.get("card") else None,
            "translation": translated or {},
            "paperwork": documents_required(clinic.country),
            # The conditional results, not verdict.needs: a stored verdict
            # keeps each result's fix but not its structured requirement.
            "send_with": [(r.label, r.fix or r.detail) for r in verdict.conditional],
            "verdict": verdict, "locked": locked,
        },
    )
