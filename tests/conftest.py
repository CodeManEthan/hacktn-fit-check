"""Suite-wide fixtures.

The import check (design §2.5) reads data/import_rules.json, where only real
countries live, so the factories' "Testland" would read as not on file and
every factory device would need info. Each test sees the committed table
plus the fixture's Testland row. A test that sets RULES_PATH itself wins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.reference import import_rules

FIXTURE_RULES = Path(__file__).parent / "fixtures" / "import_rules.json"


@pytest.fixture(autouse=True)
def testland_import_rules(tmp_path_factory, monkeypatch):
    committed = import_rules.RULES_PATH
    table = json.loads(committed.read_text(encoding="utf-8")) if committed.exists() else {}
    table.update(json.loads(FIXTURE_RULES.read_text(encoding="utf-8")))
    merged = tmp_path_factory.getbasetemp() / "import_rules_with_testland.json"
    if not merged.exists():
        merged.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(import_rules, "RULES_PATH", merged)
