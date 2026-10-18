"""Each country's import and registration rules for donated medical equipment.

The committed table is data/import_rules.json, keyed by the country name as
the facility stores it. Each row carries its source, the label languages a
device must include, the minimum remaining service life, the paperwork, and
the quotes those come from. With no file, every country reads as not on
file, which the import check turns into a question, never a pass.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import PROJECT_ROOT
from app.domain.language import normalise_languages

RULES_PATH = PROJECT_ROOT / "data" / "import_rules.json"


def load_import_rules() -> dict[str, dict[str, Any]]:
    """The whole table, label languages normalised, or {} when absent."""
    if not RULES_PATH.exists():
        return {}
    payload = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    rules: dict[str, dict[str, Any]] = {}
    for country, row in payload.items():
        if not isinstance(row, dict):
            continue
        row = dict(row)
        if "label_languages" in row:
            row["label_languages"] = normalise_languages(row.get("label_languages") or ())
        rules[country] = row
    return rules


def rule_for(country: str) -> dict[str, Any] | None:
    """The row for one country, matched without regard to case or spacing."""
    wanted = " ".join((country or "").split()).casefold()
    if not wanted:
        return None
    for name, row in load_import_rules().items():
        if " ".join(name.split()).casefold() == wanted:
            return row
    return None


def documents_required(country: str) -> list[str]:
    """The paperwork the country asks for, or [] when it is not on file."""
    row = rule_for(country) or {}
    return [str(item) for item in row.get("documents_required") or ()]
