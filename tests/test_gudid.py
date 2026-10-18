"""The GUDID client: affirms support, never denies it, caches for a week.

The two fixtures are openFDA's real answers for the DeVilbiss 525DS (four
records, one of them the 525DS itself in distribution) and the 525KS (one
record, not in distribution). No test reaches the network: each passes a
fake `fetch`, or relies on the live call being off under pytest.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.reference import gudid

FIXTURES = Path(__file__).parent / "fixtures"
UDI_525DS = json.loads((FIXTURES / "openfda_udi_525DS.json").read_text())
UDI_525KS = json.loads((FIXTURES / "openfda_udi_525KS.json").read_text())


@pytest.fixture
def store(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'gudid.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    repository.setup()
    yield
    if db._engine is not None:
        db._engine.dispose()


def answering(payload, code=200):
    calls = []

    def fetch(url, params, timeout):
        calls.append((url, params, timeout))
        return code, payload

    fetch.calls = calls
    return fetch


def test_an_in_distribution_record_from_the_maker_affirms_support(store):
    fetch = answering(UDI_525DS)

    result = gudid.lookup_support("DeVilbiss", "525DS", fetch=fetch)

    assert result.supported is True
    assert result.status == "In Commercial Distribution"
    assert result.record_url == "https://accessgudid.nlm.nih.gov/devices/00885304000846"


def test_the_request_is_a_phrase_search_on_the_model_number(store):
    fetch = answering(UDI_525KS)

    gudid.lookup_support("DeVilbiss", "525KS", fetch=fetch)

    url, params, timeout = fetch.calls[0]
    assert url == "https://api.fda.gov/device/udi.json"
    assert params == {"search": 'version_or_model_number:"525KS"', "limit": 5}
    assert timeout == 5


def test_a_record_out_of_distribution_leaves_support_unknown_not_false(store):
    result = gudid.lookup_support("DeVilbiss", "525KS", fetch=answering(UDI_525KS))

    assert result.supported is None
    assert result.status == "Not in Commercial Distribution"
    assert result.record_url == "https://accessgudid.nlm.nih.gov/devices/00885304000976"


def test_another_companys_record_does_not_count(store):
    # McKesson relabels the 525DS as "146-525DS"; Acme matches no company at all.
    result = gudid.lookup_support("Acme", "525DS", fetch=answering(UDI_525DS))

    assert result.supported is None
    assert result.status == gudid.NO_RECORD


def test_a_variant_model_number_does_not_speak_for_the_base_model():
    only_variant = {"results": [r for r in UDI_525DS["results"]
                                if r["version_or_model_number"] == "525DS-Q"]}

    result = gudid.read_payload("DeVilbiss", "525DS", only_variant)

    assert result.supported is None and result.status == gudid.NO_RECORD


def test_model_and_manufacturer_compare_loosely():
    result = gudid.read_payload("devilbiss healthcare", "525 ds", UDI_525DS)

    assert result.supported is True


def test_no_match_from_openfda_is_an_answer_and_is_cached(store):
    missing = {"error": {"code": "NOT_FOUND", "message": "No matches found!"}}
    first = gudid.lookup_support("Acme", "V100", fetch=answering(missing, code=404))
    again = answering(UDI_525DS)
    second = gudid.lookup_support("Acme", "V100", fetch=again)

    assert first.status == gudid.NO_RECORD and first.supported is None
    assert second.status == gudid.NO_RECORD
    assert again.calls == []


def test_a_cached_answer_is_reused_for_a_week_and_rejudged_per_manufacturer(store):
    gudid.lookup_support("DeVilbiss", "525DS", fetch=answering(UDI_525DS))
    unused = answering({"results": []})

    other_maker = gudid.lookup_support("Acme", "525ds", fetch=unused)
    same_maker = gudid.lookup_support("DeVilbiss", "525DS", fetch=unused)

    assert unused.calls == []
    assert other_maker.supported is None
    assert same_maker.supported is True


def test_a_cache_older_than_a_week_is_fetched_again(store):
    repo.store_registry_lookup("525DS", "In Commercial Distribution", "", UDI_525DS,
                               now=datetime.now() - timedelta(days=8))
    fresh = answering(UDI_525KS)

    gudid.lookup_support("DeVilbiss", "525DS", fetch=fresh)

    assert len(fresh.calls) == 1


@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("too slow"),
    httpx.ConnectError("no route"),
    ValueError("not JSON"),
])
def test_a_failed_call_is_unknown_and_remembered_briefly(store, failure):
    calls = []

    def fetch(url, params, timeout):
        calls.append(1)
        raise failure

    first = gudid.lookup_support("DeVilbiss", "525DS", fetch=fetch)
    second = gudid.lookup_support("DeVilbiss", "525DS", fetch=fetch)

    assert first.supported is None and first.status == gudid.FAILED
    assert second.status == gudid.FAILED
    # The failure is cached, so the second call does not wait on the FDA again.
    assert len(calls) == 1
    assert repo.cached_registry_lookup("525DS").status == gudid.FAILED


def test_a_server_error_is_unknown_and_remembered_briefly(store):
    fetch = answering({"x": 1}, code=500)
    gudid.lookup_support("DeVilbiss", "525DS", fetch=fetch)
    gudid.lookup_support("DeVilbiss", "525DS", fetch=fetch)

    assert len(fetch.calls) == 1
    assert repo.cached_registry_lookup("525DS").status == gudid.FAILED


def test_a_remembered_failure_expires_after_ten_minutes(store):
    stale = datetime.now() - timedelta(minutes=gudid.FAILED_CACHE_MINUTES + 1)
    repo.store_registry_lookup("525DS", gudid.FAILED, "", "{}", now=stale)
    fetch = answering(json.loads((FIXTURES / "openfda_udi_525DS.json").read_text()))

    result = gudid.lookup_support("DeVilbiss", "525DS", fetch=fetch)

    assert len(fetch.calls) == 1 and result.supported is True


def test_no_model_means_no_call(store):
    fetch = answering(UDI_525DS)

    assert gudid.lookup_support("DeVilbiss", "  ", fetch=fetch).status == gudid.NO_MODEL
    assert fetch.calls == []


def test_an_unreadable_cache_does_not_stop_the_lookup(monkeypatch):
    def broken(*a, **kw):
        raise repo.RepositoryError("database is down")

    monkeypatch.setattr(repo, "cached_registry_lookup", broken)
    monkeypatch.setattr(repo, "store_registry_lookup", broken)

    result = gudid.lookup_support("DeVilbiss", "525DS", fetch=answering(UDI_525DS))

    assert result.supported is True


def test_the_live_call_is_off_under_pytest_unless_asked_for(store, monkeypatch):
    def network(*a, **kw):
        raise AssertionError("reached the network")

    monkeypatch.setattr(gudid, "_http_get", network)
    monkeypatch.delenv("GUDID_LOOKUP", raising=False)

    assert gudid.live_enabled() is False
    assert gudid.lookup_support("DeVilbiss", "525DS").status == gudid.NOT_LOOKED_UP

    monkeypatch.setenv("GUDID_LOOKUP", "on")
    assert gudid.live_enabled() is True
    monkeypatch.setenv("GUDID_LOOKUP", "off")
    assert gudid.live_enabled() is False
