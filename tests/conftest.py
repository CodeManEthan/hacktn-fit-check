"""Suite-wide fixtures.

The import check reads data/import_rules.json, where only real
countries live, so the factories' "Testland" would read as not on file and
every factory device would need info. Each test sees the committed table
plus the fixture's Testland row. A test that sets RULES_PATH itself wins.

No test reaches a model. Every model client a route asks for, and the
shared chat client the AI helpers default to, is a fake that answers like
an unconfigured OCI account: GenAiError, so each page takes its fallback.
A test that wants an answer patches its own fake over this one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai import client as model_client
from app.ai.client import GenAiError
from app.reference import import_rules
from app.web import packet_routes, routes
from tests.test_impact import FakeChat

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


class NoModel(FakeChat):
    """The impact tests' fake chat, always failing, with the JSON call too."""

    def __init__(self):
        super().__init__(fail=True)

    def complete_json(self, prompt, **kwargs):
        return self.complete(prompt, **kwargs)


class NoLanguage:
    """An OCI Language client that never answers."""

    def batch_language_translation(self, details):
        raise GenAiError("OCI Language is not reachable from a test")


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    down = NoModel()
    monkeypatch.setattr(model_client.CLIENT, "complete", down.complete)
    monkeypatch.setattr(routes, "_impact_client", lambda: down)
    monkeypatch.setattr(packet_routes, "_packet_chat", lambda: down)
    monkeypatch.setattr(packet_routes, "_packet_language", lambda: NoLanguage())
    return down
