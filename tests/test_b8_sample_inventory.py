"""[B8] data/sample_inventory.csv: six library devices, screened against the
seeded Kayunga clinic (scripts/seed_demo.py into a fresh SQLite file).

Every plate cell in the sample must be the library's own value, so the file
carries no device fact typed from memory. GUDID is off; no model is called.
"""

from __future__ import annotations

import csv
import html
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.domain.result import Outcome
from app.main import app
from app.matching import csv_screen
from app.reference import device_library
from app.web import csv_routes
from scripts import seed_demo

SAMPLE = csv_routes.SAMPLE_PATH


def sample_rows() -> list[dict[str, str]]:
    with SAMPLE.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_the_sample_has_the_contract_header_and_six_distinct_library_devices():
    with SAMPLE.open(encoding="utf-8") as fh:
        assert fh.readline().strip() == ",".join(csv_screen.COLUMNS)
    rows = sample_rows()
    assert len(rows) == 6
    slugs = []
    for row in rows:
        hit = device_library.best_match(row["manufacturer"], row["model"])
        assert hit is not None, row["model"]
        assert row["device_type"] == hit.device_type
        slugs.append(hit.slug)
    assert len(set(slugs)) == 6


def test_every_plate_cell_in_the_sample_is_the_library_value():
    for row in sample_rows():
        hit = device_library.best_match(row["manufacturer"], row["model"])
        library = device_library.to_device_facts(hit)
        typed = csv_screen.donor_device(row)
        for column in csv_screen.SPEC_COLUMNS:
            if not row[column]:
                # A blank cell is one the library leaves unknown.
                assert not getattr(library, column).known, (row["model"], column)
                continue
            assert csv_screen._same_value(getattr(typed, column).value,
                                          getattr(library, column).value), (row["model"], column)


@pytest.fixture(scope="module")
def kayunga(tmp_path_factory):
    path = tmp_path_factory.mktemp("b8") / "demo.db"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db, "SETTINGS", replace(SETTINGS, database_url=f"sqlite:///{path}"))
        mp.setattr(db, "_engine", None)
        mp.setenv("GUDID_LOOKUP", "off")
        mp.setattr(csv_routes, "_HELD", {})
        seed_demo.main()
        yield TestClient(app)
        if db._engine is not None:
            db._engine.dispose()


def test_the_sample_screens_cleanly_against_kayunga(kayunga):
    clinic = repo.load_facility(seed_demo.KAYUNGA)
    result = csv_screen.screen_csv(SAMPLE.read_bytes(), clinic)
    assert [r.status for r in result.rows] == ["screened"] * 6
    assert all(r.notes == () for r in result.rows)
    by_model = {r.offer.device.model.value: r for r in result.rows}
    # The 525KS: the seeded recorded answer settles its supply, so it ships with extras.
    assert by_model["525KS"].outcome == "works if you also send"
    assert any("MC44D-605" in s for s in by_model["525KS"].send_with)
    # The Pulmo-Aide is a 60 Hz unit on a 50 Hz grid.
    assert by_model["5650D"].outcome == "won't work"
    assert by_model["5650D"].blockers[0].startswith("Supply frequency:")
    assert {r.outcome for r in result.rows} == {"works if you also send", "needs info", "won't work"}
    # Every row is its library entry (check 1 L1: maker and device type agree),
    # and the screening is 0 works, 1 works-if, 3 needs info, 2 won't work.
    assert all(r.library_slug for r in result.rows)
    assert [result.count(o) for o in (Outcome.WORKS, Outcome.WORKS_IF, Outcome.NEEDS_INFO,
                                      Outcome.WONT_WORK)] == [0, 1, 3, 2]


def test_the_sample_through_the_routes_posts_six_offers_only_on_the_button(kayunga):
    form = kayunga.get("/offer/csv")
    assert f'<option value="{seed_demo.KAYUNGA}" selected>' in form.text
    assert 'href="/offer/csv/sample"' in form.text
    sample = kayunga.get("/offer/csv/sample")
    assert sample.status_code == 200 and sample.text == SAMPLE.read_text(encoding="utf-8")

    before = {t: len(repo.open_offers_for_type(t)) for t in {r["device_type"] for r in sample_rows()}}
    response = kayunga.post(
        "/offer/csv",
        data={"facility": seed_demo.KAYUNGA, "donor_name": "Leeds and Nashville surplus",
              "donor_email": "surplus@example.org"},
        files={"inventory": ("sample_inventory.csv", SAMPLE.read_bytes(), "text/csv")})
    assert response.status_code == 200
    body = html.unescape(response.text)
    assert "6 rows screened against Kayunga District Hospital" in body
    assert "rows not read" not in body
    after_screen = {t: len(repo.open_offers_for_type(t)) for t in before}
    assert after_screen == before

    token = re.search(r"/offer/csv/([0-9a-f]{32})\.csv", body).group(1)
    posted = kayunga.post(f"/offer/csv/{token}/post",
                          data={"donor_name": "Leeds and Nashville surplus",
                                "donor_email": "surplus@example.org"})
    assert "6 offers saved" in posted.text
    after_post = {t: len(repo.open_offers_for_type(t)) for t in before}
    assert sum(after_post.values()) == sum(before.values()) + 6
