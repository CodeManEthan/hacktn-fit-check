"""Parsing and validation of form input, kept out of the route handlers.

Every value crossing this boundary is untrusted, so each parser has an explicit
failure rather than a silent default.
"""

from __future__ import annotations

from datetime import date

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
    except ValueError as exc:
        raise FormError(f"{field} must be a number. You gave {value!r}.") from exc
    if number < minimum:
        raise FormError(f"{field} cannot be below {minimum}.")
    if number > MAX_QUANTITY:
        raise FormError(f"{field} cannot be above {MAX_QUANTITY}.")
    return number


def decimal(raw: str, field: str, *, default: float | None = None) -> float | None:
    value = (raw or "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise FormError(f"{field} must be a number. You gave {value!r}.") from exc


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
