"""Decide which support need an offered item can satisfy.

Runs on OCI Generative AI at intake, once per offer, so every later request is
a database lookup rather than a model call. The model is given a closed list
and may only pick from it or abstain, which keeps the judgement bounded: it is
normalising a donor's free text, never deciding clinical equivalence.
"""

from __future__ import annotations

import logging

from app.ai.client import CLIENT, GenAiClient, GenAiError
from app.domain.requirement import SEARCH_TERMS, RequirementKind

LOGGER = logging.getLogger(__name__)

CATEGORISE_SYSTEM = """\
You classify a piece of donated equipment into exactly one support category,
or none at all.

Rules:
- Reply with ONLY a JSON object, no prose and no code fence.
- "category" must be one of the listed values, or null.
- Use null for any clinical or diagnostic device. A ventilator, monitor,
  x-ray or anaesthesia machine is NOT a support item, it is the thing that
  needs support. Only accessories and infrastructure get a category.
- If you are not confident, use null. A wrong category sends a clinic the
  wrong hardware."""

CATEGORISE_USER = """\
Categories:
{categories}

Item type: {device_type}
Item name: {label}

Answer as: {{"category": "<one of the above>" or null, "why": "<one short sentence>"}}"""


def _options() -> str:
    return "\n".join(f"- {kind.value}" for kind in RequirementKind)


def categorise_support(
    device_type: str, label: str = "", *, client: GenAiClient = CLIENT
) -> RequirementKind | None:
    """Return the support need this item satisfies, or None.

    Falls back to the keyword table when the model is unreachable, so posting
    an offer never fails because Generative AI is down.
    """
    text = f"{device_type} {label}".strip()
    if not text:
        return None

    try:
        payload = client.complete_json(
            CATEGORISE_USER.format(
                categories=_options(), device_type=device_type or "(not given)",
                label=label or "(not given)"),
            system=CATEGORISE_SYSTEM,
            max_tokens=200,
        )
    except GenAiError as exc:
        LOGGER.warning("categorisation fell back to keywords: %s", exc)
        return keyword_category(text)

    raw = payload.get("category")
    if not raw:
        return None
    for kind in RequirementKind:
        if kind.value == str(raw).strip().lower():
            return kind
    LOGGER.warning("model returned an unknown category %r", raw)
    return None


def keyword_category(text: str) -> RequirementKind | None:
    """The offline fallback: first kind whose search terms appear in the text."""
    lowered = text.lower()
    for kind, terms in SEARCH_TERMS.items():
        if any(term in lowered for term in terms):
            return kind
    return None
