"""The prototype note on every page, the GitHub route on contact and help, and
/example, which opens the seeded Kayunga request's matches page."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import seeded as seeded_rows
from app.main import app
from app.web import marketplace_routes
from scripts import seed_demo

NOTE = "<b>HackTN 2026 prototype</b> with demo data. Nothing is shipped and no one is emailed."
ISSUES = "https://github.com/CodeManEthan/hacktn-fit-check/issues"


def _use_database(mp: pytest.MonkeyPatch, path) -> None:
    mp.setattr(db, "SETTINGS", replace(SETTINGS, database_url=f"sqlite:///{path}"))
    mp.setattr(db, "_engine", None)
    mp.setenv("GUDID_LOOKUP", "off")


def _dispose() -> None:
    if db._engine is not None:
        db._engine.dispose()


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("notice") / "demo.db"
    with pytest.MonkeyPatch.context() as mp:
        _use_database(mp, path)
        request_id = seed_demo.main()["request_id"]
        yield {"client": TestClient(app), "request_id": request_id}
        _dispose()


@pytest.fixture
def empty(tmp_path, monkeypatch):
    _use_database(monkeypatch, tmp_path / "empty.db")
    yield TestClient(app)
    _dispose()


# ---- the note -----------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/about", "/contact", "/help", "/offer", "/impact",
                                  "/no-such-page"])
def test_every_page_carries_the_prototype_note_once(seeded, path):
    body = seeded["client"].get(path).text
    assert body.count('class="proto-note"') == 1
    assert NOTE in body


def test_the_matches_page_carries_the_note_above_the_bar(seeded):
    body = seeded["client"].get(f"/request/{seeded['request_id']}/matches").text
    assert NOTE in body
    assert body.index('class="proto-note"') < body.index('class="lander-bar"')


# ---- contact and help ------------------------------------------------------------

@pytest.mark.parametrize("path", ["/contact", "/help"])
def test_contact_and_help_point_at_github_issues_not_the_hackathon_table(seeded, path):
    body = seeded["client"].get(path).text
    assert "HackTN table" not in body
    assert "no mailbox" not in body
    assert f'href="{ISSUES}"' in body
    assert "mailto:" not in body


def test_contact_also_names_the_portfolio(seeded):
    body = seeded["client"].get("/contact").text
    assert 'href="https://codemanethan.github.io">his portfolio</a>' in body


# ---- /example -----------------------------------------------------------------

def test_the_landing_button_opens_the_example(seeded):
    body = seeded["client"].get("/").text
    assert 'class="lander-cta" href="/example">See a worked example</a>' in body
    assert "Kayunga District Hospital" in body


def test_example_redirects_to_the_seeded_request_matches_page(seeded):
    reply = seeded["client"].get("/example", follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"] == f"/request/{seeded['request_id']}/matches"
    landed = seeded["client"].get("/example")
    assert landed.status_code == 200 and "Kayunga" in landed.text


def test_example_looks_the_request_up_as_the_seed_does(monkeypatch):
    assert seeded_rows.KAYUNGA == seed_demo.KAYUNGA
    assert seeded_rows.KAYUNGA_REQUEST_TYPE == seed_demo.HERO_TYPE
    monkeypatch.setattr(seeded_rows, "kayunga_request_id", lambda: 41)
    assert marketplace_routes.example_request_id() == 41


def test_example_without_a_seed_says_so_instead_of_redirecting(empty):
    repository.setup()
    reply = empty.get("/example", follow_redirects=False)
    assert reply.status_code == 404
    assert "The worked example is not loaded" in reply.text
    assert NOTE in reply.text


def test_example_on_a_database_with_no_tables_still_answers(empty):
    reply = empty.get("/example", follow_redirects=False)
    assert reply.status_code == 404
    assert "The worked example is not loaded" in reply.text
