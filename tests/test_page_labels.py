"""The words the pages print: one label per outcome, counts that agree with
their nouns, the donor panel's plain statuses, and no internal seed marker.

Pages are rendered with TestClient against the seeded demo
(scripts/seed_demo.py into a fresh SQLite file). GUDID is off; no model is called.
"""

from __future__ import annotations

import contextlib
import html
import importlib.util
import io
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.domain.requirement import Requirement, RequirementKind
from app.domain.result import CheckResult, CheckStatus, Outcome, Verdict
from app.domain.text import plural
from app.main import app
from app.matching.bundle import Bundle, BundleItem
from app.web import csv_routes, presenters
from scripts import seed_demo

ROOT = Path(__file__).resolve().parents[1]

# Each outcome has its own tone class, so the tone on a label says which
# outcome it stands for; the label must then be that outcome's stamp.
STAMP_BY_TONE = {presenters.TONE_BY_OUTCOME[o]: Verdict(o, ()).stamp for o in Outcome}


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    path = tmp_path_factory.mktemp("labels") / "demo.db"
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db, "SETTINGS", replace(SETTINGS, database_url=f"sqlite:///{path}"))
        mp.setattr(db, "_engine", None)
        mp.setenv("GUDID_LOOKUP", "off")
        mp.setattr(csv_routes, "_HELD", {})
        with contextlib.redirect_stdout(out):
            seeded = seed_demo.main()
        yield TestClient(app), seeded, out.getvalue()
        if db._engine is not None:
            db._engine.dispose()


def _hero_ids():
    offers = repo.open_offers_for_type(seed_demo.HERO_TYPE)
    return [o.id for o in offers]


def _assert_labels(pairs, where):
    assert pairs, f"no outcome labels found on {where}"
    for tone, label in pairs:
        assert label.strip() == STAMP_BY_TONE[tone], (where, tone, label)


# --- one label per outcome ------------------------------------------------------

def test_matches_page_labels_are_the_stamp(demo):
    client, seeded, _ = demo
    body = client.get(f"/request/{seeded['request_id']}/matches").text
    pairs = re.findall(r'<span class="unit-badges">.*?<span class="pill (\w+)">([^<]*)</span>',
                       body, re.S)
    _assert_labels(pairs, "matches")


def test_candidate_page_labels_are_the_stamp(demo):
    client, seeded, _ = demo
    for offer_id in _hero_ids():
        body = client.get(f"/request/{seeded['request_id']}/offer/{offer_id}").text
        pairs = re.findall(r'<div class="stamp (\w+)">\s*<b>([^<]*)</b>', body)
        _assert_labels(pairs, f"candidate {offer_id}")


def test_verdict_pages_and_impact_labels_are_the_stamp(demo):
    client, _, _ = demo
    rows = repository.recent_verdicts()
    assert rows
    for row in rows[:5]:
        body = client.get(f"/verdict/{row['id']}").text
        pairs = re.findall(r'<div class="stamp (\w+)">\s*<b>([^<]*)</b>', body)
        _assert_labels(pairs, f"verdict {row['id']}")

    body = client.get("/impact").text
    tiles = re.findall(r'<div class="counter (\w+)"><b>\d+</b><span>([^<]*)</span>', body)
    _assert_labels(tiles, "impact tiles")
    pills = re.findall(r'<td><span class="pill (\w+)">([^<]*)</span></td>', body)
    _assert_labels(pills, "impact recent")


def test_csv_result_labels_are_the_stamp(demo):
    client, _, _ = demo
    response = client.post(
        "/offer/csv",
        data={"facility": seed_demo.KAYUNGA, "donor_name": "Label check",
              "donor_email": "labels@example.org"},
        files={"inventory": ("sample.csv", csv_routes.SAMPLE_PATH.read_bytes(), "text/csv")})
    body = response.text
    tiles = re.findall(r'<div class="counter (\w+)"><b>\d+</b><span>([^<]*)</span>', body)
    _assert_labels(tiles, "csv tiles")
    pills = re.findall(r'<td><span class="pill (\w+)">([^<]*)</span></td>', body)
    _assert_labels(pills, "csv rows")


def test_every_text_check_live_reads_is_on_the_seeded_matches_page(demo, monkeypatch, capsys):
    """Runs deploy/check_live.py against this app, so renaming a literal it
    reads fails here before it fails a deploy."""
    client, _, seed_output = demo
    spec = importlib.util.spec_from_file_location("check_live_here", ROOT / "deploy" / "check_live.py")
    check_live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_live)

    base = "http://local.test"

    def fetch(url):
        path = url.removeprefix(base)
        if path.startswith("/packet/"):
            # The packet page asks a model for its card; the demo walk test
            # covers it with a fake. Here only the matches page's text counts.
            return 200, ""
        response = client.get(path)
        return response.status_code, response.text

    monkeypatch.setattr(check_live, "fetch", fetch)
    monkeypatch.setattr("sys.stdin", io.StringIO(seed_output))
    assert check_live.main(["check_live.py", base]) == 0, capsys.readouterr().out


# --- counts agree with their nouns ----------------------------------------------

def test_plural():
    assert plural(1, "condition") == "1 condition"
    assert plural(0, "item") == "0 items"
    assert plural(3, "open question") == "3 open questions"


def _result(status):
    return CheckResult(key=status.value, label="x", status=status, detail="d")


def test_verdict_headline_counts_agree():
    one_each = Verdict(Outcome.WONT_WORK, (_result(CheckStatus.FAIL), _result(CheckStatus.MITIGABLE)))
    assert one_each.headline == "1 blocker, 1 condition"
    assert Verdict(Outcome.WORKS, (_result(CheckStatus.PASS),)).headline == "1 check passes"
    assert Verdict(Outcome.WORKS, (_result(CheckStatus.PASS),) * 2).headline == "All 2 checks pass"


def test_bundle_summary_counts_agree():
    stabiliser = Requirement(kind=RequirementKind.STABILISER, detail="a stabiliser")

    def item(available):
        return BundleItem(requirement=stabiliser, offers=(object(),) if available else ())

    one = Bundle(items=(item(True),))
    assert one.summary == "1 item can be sourced from open offers."
    two = Bundle(items=(item(True), item(True)))
    assert two.summary == "All 2 items can be sourced from open offers."
    short = Bundle(items=(item(False),))
    assert short.summary == "0 of 1 item can be sourced."


# --- the donor panel and the seed marker ----------------------------------------

def test_offer_page_reads_plainly(demo):
    client, _, _ = demo
    body = html.unescape(client.get("/offer").text)
    assert "What we read" in body and "Waiting for a photo or text" in body
    for old in ("telemetry</h2>", "ARTIFACT", "EXTRACTED", "Email for confirmation"):
        assert old not in body


def test_offer_posted_promises_no_email():
    text = (ROOT / "app/web/templates/offer_posted.html").read_text(encoding="utf-8")
    assert "we will\n     email you" not in text and "sends no email" in text


def test_the_seed_marker_never_renders(demo):
    client, _, _ = demo
    assert presenters.SEED_HISTORY_MARKER == seed_demo.HISTORY_MARKER
    for row in repository.recent_verdicts():
        body = client.get(f"/verdict/{row['id']}").text
        assert seed_demo.HISTORY_MARKER not in body
        assert "Sample screening" in body
