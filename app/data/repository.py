"""All database access lives here, behind named operations."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy import func, insert, select

from app.data.db import devices, facilities, get_engine, init_db, verdicts
from app.data.seed import FACILITIES
from app.data.serialize import (
    device_from_dict,
    device_to_dict,
    intel_from_dict,
    intel_to_dict,
    verdict_from_dict,
    verdict_to_dict,
)
from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.result import CheckStatus, Verdict

VERDICT_ID_LENGTH = 8


class RepositoryError(RuntimeError):
    """Any storage failure, with a message safe to show a user."""


def _new_verdict_id() -> str:
    return uuid.uuid4().hex[:VERDICT_ID_LENGTH]


def setup() -> None:
    """Create tables and load the staged clinic profiles once."""
    engine = init_db()
    try:
        with engine.begin() as conn:
            existing = conn.execute(select(func.count()).select_from(facilities)).scalar()
            if existing:
                return
            conn.execute(
                insert(facilities),
                [
                    {
                        "slug": f.slug,
                        "name": f.name,
                        "country": f.country,
                        "profile_json": json.dumps(asdict(f)),
                    }
                    for f in FACILITIES
                ],
            )
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not prepare the database: {exc}") from exc


def save_device(device: DeviceFacts) -> int:
    try:
        with get_engine().begin() as conn:
            result = conn.execute(
                insert(devices).values(
                    label=device.label,
                    raw_input=device.raw_input,
                    facts_json=json.dumps(device_to_dict(device)),
                )
            )
            return int(result.inserted_primary_key[0])
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not save the device: {exc}") from exc


def save_verdict(
    *,
    device_id: int,
    device: DeviceFacts,
    facility: FacilityProfile,
    verdict: Verdict,
    intel: SupplyIntel,
) -> str:
    verdict_id = _new_verdict_id()
    try:
        with get_engine().begin() as conn:
            conn.execute(
                insert(verdicts).values(
                    id=verdict_id,
                    device_id=device_id,
                    device_label=device.label,
                    facility_slug=facility.slug,
                    facility_name=facility.name,
                    outcome=verdict.outcome.value,
                    verdict_json=json.dumps(verdict_to_dict(verdict)),
                    intel_json=json.dumps(intel_to_dict(intel)),
                )
            )
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not save the verdict: {exc}") from exc
    return verdict_id


def load_verdict(verdict_id: str) -> dict[str, Any] | None:
    """Return everything the verdict page needs, or None if there is no such id."""
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(verdicts).where(verdicts.c.id == verdict_id)
            ).mappings().first()
            if row is None:
                return None
            device_row = conn.execute(
                select(devices).where(devices.c.id == row["device_id"])
            ).mappings().first()
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not load the verdict: {exc}") from exc

    device = (
        device_from_dict(json.loads(device_row["facts_json"]))
        if device_row
        else DeviceFacts()
    )
    return {
        "id": row["id"],
        "device": device,
        "device_label": row["device_label"],
        "facility_slug": row["facility_slug"],
        "facility_name": row["facility_name"],
        "verdict": verdict_from_dict(json.loads(row["verdict_json"])),
        "intel": intel_from_dict(json.loads(row["intel_json"] or "{}")),
        "created_at": row["created_at"],
    }


def count_by_outcome() -> dict[str, int]:
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(verdicts.c.outcome, func.count()).group_by(verdicts.c.outcome)
            ).all()
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not read the tally: {exc}") from exc
    return {outcome: int(total) for outcome, total in rows}


def failures_by_reason(limit: int = 10) -> list[tuple[str, int]]:
    """Which checks caught the most problems. Reads the stored verdict JSON."""
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(select(verdicts.c.verdict_json)).scalars().all()
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not read the tally: {exc}") from exc

    tally: dict[str, int] = {}
    blocking = {CheckStatus.FAIL.value, CheckStatus.MITIGABLE.value}
    for payload in rows:
        for result in json.loads(payload).get("results", []):
            if result.get("status") in blocking:
                tally[result["label"]] = tally.get(result["label"], 0) + 1
    return sorted(tally.items(), key=lambda item: item[1], reverse=True)[:limit]


def recent_verdicts(limit: int = 20) -> list[dict[str, Any]]:
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(
                    verdicts.c.id,
                    verdicts.c.device_label,
                    verdicts.c.facility_name,
                    verdicts.c.outcome,
                    verdicts.c.created_at,
                ).order_by(verdicts.c.created_at.desc()).limit(limit)
            ).mappings().all()
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not list verdicts: {exc}") from exc
    return [dict(row) for row in rows]


def load_device(device_id: int) -> DeviceFacts | None:
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(devices).where(devices.c.id == device_id)
            ).mappings().first()
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not load the device: {exc}") from exc
    if row is None:
        return None
    return device_from_dict(json.loads(row["facts_json"]))
