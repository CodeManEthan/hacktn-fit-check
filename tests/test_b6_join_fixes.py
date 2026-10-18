"""[B6] The wave-one join fixes: page links on library citations, one inspector
row builder for both offer paths, and the document `kind` the packet reads.
No model is called.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.ai.extract import library_citations
from app.config import SETTINGS
from app.data import db, repository
from app.main import app
from app.reference import device_library
from app.web import inspector, marketplace_routes
from tests.test_packet_route import a_clinic, an_offer, fakes  # noqa: F401 - fixture

DS = "drive-devilbiss-525ds"
KS = "drive-devilbiss-525ks"


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'joins.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


# ---- (B4 check L1) the source link keeps its page -----------------------------

def test_the_525ds_voltage_citation_links_to_its_page():
    entry = device_library.entry(DS)
    page = entry.facts["voltage_min_v"]["page"]
    cited = library_citations(entry)["voltage_min_v"]
    assert cited.page == page
    assert cited.link.endswith(f"#page={page}")


def test_a_fact_without_its_own_url_falls_back_to_the_document():
    entry = device_library.entry(DS)
    bare = {**entry.facts["voltage_min_v"]}
    bare.pop("url")
    stripped = replace(entry, facts={**entry.facts, "voltage_min_v": bare})
    doc = next(d for d in entry.documents if d["file"] == bare["document"])
    assert library_citations(stripped)["voltage_min_v"].url == doc["url"]


# ---- (B4 check L2) one inspector row set ----------------------------------------

def test_prefill_returns_the_inspector_rows_with_page_links(client):
    data = client.post("/offer/prefill", data={"slug": DS}).json()
    rows = data["rows"]
    assert [r["label"] for r in rows] == list(inspector.ROW_LABELS)
    assert all(set(r) >= {"label", "value", "source", "source_url", "page", "field", "known"}
               for r in rows)
    voltage = next(r for r in rows if r["label"] == "Voltage")
    page = device_library.entry(DS).facts["voltage_min_v"]["page"]
    assert voltage["known"] and voltage["source_url"].endswith(f"#page={page}")
    assert data["device_type"] == "oxygen concentrator"


def test_the_old_prefill_row_builder_is_gone():
    for name in ("_inspector_rows", "INSPECTOR_ROWS", "PREFILL_ROWS", "_prefill_value"):
        assert not hasattr(marketplace_routes, name)


def test_the_offer_page_renders_both_paths_through_one_renderer(client):
    page = client.get("/offer").text
    assert 'name="library_slug"' in page and 'id="extract-now"' in page
    head = page[page.index('class="audit inspector-table"'):]
    head = head[:head.index("</thead>")]
    assert head.count("<th scope") == 4
    assert "window.fitInspector = {" in page
    assert "window.fitInspector.render(rows, false)" in page



def test_the_library_pick_script_runs_after_the_inspector_exists(client):
    """(B6 check L1) An inline script that runs before <tbody data-inspector-body>
    finds no body, returns early and never listens for the pick. Every script
    that looks the body up must come after it, and the pick after the renderer."""
    page = client.get("/offer").text
    body_at = page.index("<tbody data-inspector-body>")
    pick_at = page.index("select.addEventListener('change'")
    renderer_at = page.index("window.fitInspector = {")
    script_starts = [i for i in range(len(page)) if page.startswith("<script>", i)]
    pick_script = max(i for i in script_starts if i < pick_at)
    assert body_at < pick_script, "the pick script runs before the inspector body exists"
    assert renderer_at < pick_at, "the pick script runs before window.fitInspector is set"
    for start in script_starts:
        end = page.index("</script>", start)
        if "[data-inspector-body]" in page[start:end]:
            assert start > body_at, "a script looks up the inspector body before it exists"


def _note_page(note):
    found = re.search(r"p\.(\d+)\s*$", note)
    return int(found.group(1)) if found else None


def test_the_525ds_model_row_links_the_page_its_note_cites():
    """(B6 check L3) The Model row showed the model fact's note (p.11) but linked
    the manufacturer fact's page (p.44, the address). Both now come from one fact."""
    entry = device_library.entry(DS)
    rows = inspector.inspector_rows(device_library.to_device_facts(entry), library_citations(entry))
    model = next(r for r in rows if r["label"] == "Model")
    page = _note_page(model["note"])
    assert page == 11
    assert model["page"] == page and model["source_url"].endswith(f"#page={page}")


def test_every_library_row_links_the_page_its_note_cites():
    for entry in device_library.load_library():
        facts = device_library.to_device_facts(entry)
        for row in inspector.inspector_rows(facts, library_citations(entry)):
            page = _note_page(row["note"])
            if row["known"] and page is not None and row["source_url"]:
                assert row["source_url"].endswith(f"#page={page}"), (entry.slug, row["label"])

# ---- (B5 check L2) documents carry their kind ---------------------------------------

def test_the_525ks_documents_fact_carries_each_kind():
    facts = device_library.to_device_facts(device_library.entry(KS))
    kinds = [d["kind"] for d in facts.documents.value]
    assert {"manual", "registry", "product page"} <= set(kinds)


def test_the_packet_picks_the_manual_by_kind():
    docs = device_library.to_device_facts(device_library.entry(KS)).documents.value
    reordered = [d for d in docs if d["kind"] != "manual"] + [d for d in docs if d["kind"] == "manual"]
    picked = marketplace_routes._packet_manual(reordered)
    assert picked["kind"] == "manual"


def test_the_editions_list_leaves_out_registry_records_and_product_pages(fakes):  # noqa: F811
    docs = device_library.to_device_facts(device_library.entry(KS)).documents.value
    offer_id, slug = an_offer(documents=docs), a_clinic()

    body = fakes.client.get(f"/packet/{offer_id}?facility={slug}").text
    editions = body[body.index("The manufacturer's own editions"):]
    editions = editions[:editions.index("</section>")]

    assert "Instruction Guide SE-525KS-1 Rev D" in editions
    assert "FDA GUDID record" not in editions
    assert "product page" not in editions
    assert "Declaration of Conformity" not in editions
