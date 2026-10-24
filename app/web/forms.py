"""Parsing and validation of form input, kept out of the route handlers.

Every value crossing this boundary is untrusted, so each parser has an explicit
failure rather than a silent default.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

from app.domain.device import DeviceFacts
from app.domain.provenance import UNKNOWN, Provenance, fact

MAX_QUANTITY = 1000


class FormError(ValueError):
    """A field the user must fix, with a message written for them."""


def text(raw: str, field: str, *, required: bool = False, limit: int = 300) -> str:
    value = (raw or "").strip()
    if required and not value:
        raise FormError(f"{field} is needed.")
    return value[:limit]


def whole_number(raw: str, field: str, *, default: int = 0, minimum: int = 0) -> int:
    value = (raw or "").strip()
    if not value:
        return default
    try:
        number = int(float(value))
    except (ValueError, OverflowError) as exc:
        # "inf", "nan" and "1e400" parse as floats but have no integer value.
        raise FormError(f"{field} must be a number. You gave {value!r}.") from exc
    if number < minimum:
        raise FormError(f"{field} cannot be below {minimum}.")
    if number > MAX_QUANTITY:
        raise FormError(f"{field} cannot be above {MAX_QUANTITY}.")
    return number


def decimal(
    raw: str,
    field: str,
    *,
    default: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    unit: str = "",
) -> float | None:
    """A finite number, within [minimum, maximum] when those are given."""
    value = (raw or "").strip()
    if not value:
        return default
    try:
        number = float(value)
    except ValueError as exc:
        raise FormError(f"{field} must be a number. You gave {value!r}.") from exc
    if not math.isfinite(number):
        raise FormError(f"{field} must be a number. You gave {value!r}.")
    if minimum is not None and number < minimum:
        raise FormError(f"{field} cannot be below {minimum:g}{unit}.")
    if maximum is not None and number > maximum:
        raise FormError(f"{field} cannot be above {maximum:g}{unit}.")
    return number


def iso_date(raw: str, field: str) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FormError(f"{field} must look like 2027-03-31. You gave {value!r}.") from exc


def flag(raw: str | None) -> bool:
    """An unchecked HTML checkbox sends nothing at all."""
    return raw is not None


def comma_list(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in (raw or "").split(",") if part.strip())


def slugify(name: str) -> str:
    kept = [c.lower() if c.isalnum() else "-" for c in name.strip()]
    slug = "".join(kept).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    if not slug:
        raise FormError("That name cannot be turned into an address.")
    return slug[:60]


def apply_dated_items(device: DeviceFacts, expiry_text: str) -> DeviceFacts:
    """Write the donor's answer to "does anything here expire?" onto the device.

    The offer form says "Blank if nothing dated", so a blank field is an
    answer: nothing dated. A date means something is dated, and that date is
    the expiry. A blank also clears any expiry an earlier step wrote, so the
    two facts never disagree. Every route that builds an offer calls this last, so the
    donor's answer is the last word on the date. A malformed date is a
    FormError, never a silent blank.
    """
    expiry = iso_date(expiry_text, "Expiry date")
    if expiry is None:
        return replace(
            device, dated_items=fact(False, Provenance.DONOR_STATED), expiry_date=UNKNOWN
        )
    return replace(
        device,
        dated_items=fact(True, Provenance.DONOR_STATED),
        expiry_date=fact(expiry, Provenance.DONOR_STATED),
    )
