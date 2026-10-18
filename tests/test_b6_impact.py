"""[B6] §7.3 the impact paragraph: figures from the log, a gated model paragraph,
cached per process. Fake clients only; no model is called.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ai import impact
from app.ai.client import GenAiError
from app.config import SETTINGS
from app.data import db
from app.main import app
from app.web import routes
from scripts import seed_demo

FIGURES = {
    "screened": 20, "blocked": 11,
    "blockers": {"Operating temperature": 8, "Training and repair": 3, "Supply frequency": 2},
    "add_ons": 0, "needs_info": 9, "clinics": 3, "countries": ["Haiti", "Nepal", "Uganda"],
}


class FakeChat:
    def __init__(self, text="", fail=False):
        self.text, self.fail, self.calls = text, fail, 0

    def complete(self, prompt, **kwargs):
        self.calls += 1
        self.prompt, self.kwargs = prompt, kwargs
        if self.fail:
            raise GenAiError("down")
        return SimpleNamespace(text=self.text)


@pytest.fixture(autouse=True)
def fresh_cache():
    impact.clear_cache()
    yield
    impact.clear_cache()


# ---- the gate ---------------------------------------------------------------

def test_a_paragraph_using_only_the_figures_is_kept():
    text = ("Fit-Check screened 20 devices for 3 clinics and stopped 11 before shipping; "
            "operating temperature stopped 8 of them.")
    assert impact.gate(text, FIGURES) == text


def test_a_paragraph_with_an_invented_number_is_replaced():
    text = "Fit-Check screened 20 devices and saved about 40,000 dollars."
    kept = impact.gate(text, FIGURES)
    assert kept == impact.code_sentence(FIGURES)
    assert "40,000" not in kept


def test_a_percentage_the_figures_do_not_hold_is_replaced():
    assert impact.gate("55% of screenings were stopped.", FIGURES) == impact.code_sentence(FIGURES)


def test_an_invented_number_in_words_is_replaced():
    assert impact.gate("Forty-two devices were stopped.", FIGURES) == impact.code_sentence(FIGURES)
    assert impact.gate("Twenty devices were screened.", FIGURES) == "Twenty devices were screened."


def test_a_stated_sum_of_two_figures_is_allowed():
    text = "Of 20 screenings, 11 were stopped and 9 need answers, 20 in all."
    assert impact.gate(text, FIGURES) == text
    assert 11 + 9 in impact.allowed_numbers(FIGURES)
    assert 8 + 3 in impact.allowed_numbers(FIGURES)


def test_the_code_sentence_uses_only_the_figures():
    sentence = impact.code_sentence(FIGURES)
    assert impact.gate(sentence, FIGURES) == sentence
    assert "20" in sentence and "Haiti, Nepal, Uganda" in sentence


# ---- the model call and the cache ---------------------------------------------

def test_write_summary_sends_the_figures_with_the_only_these_rule():
    chat = FakeChat("ok")
    impact.write_summary(FIGURES, chat)
    assert '"screened": 20' in chat.prompt
    assert "only" in chat.kwargs["system"] and "model" not in chat.kwargs


def test_the_summary_is_cached_for_the_same_figures():
    chat = FakeChat("Fit-Check screened 20 devices and stopped 11.")
    first = impact.summary(FIGURES, chat)
    second = impact.summary(FIGURES, chat)
    assert first == second and first.by_model and chat.calls == 1
    assert first.label == impact.WRITTEN_BY_MODEL
    impact.summary({**FIGURES, "screened": 21}, chat)
    assert chat.calls == 2


def test_a_refused_paragraph_is_labelled_as_code_written():
    result = impact.summary(FIGURES, FakeChat("It saved 400 lives."))
    assert not result.by_model and result.label == impact.WRITTEN_BY_CODE
    assert result.text == impact.code_sentence(FIGURES)


def test_a_model_failure_falls_back_and_retries_later():
    chat = FakeChat(fail=True)
    result = impact.summary(FIGURES, chat, now=1000.0)
    assert result.text == impact.code_sentence(FIGURES) and not result.by_model
    impact.summary(FIGURES, chat, now=1000.0 + 10)
    assert chat.calls == 1
    impact.summary(FIGURES, chat, now=1000.0 + impact.FAILURE_RETRY_SECONDS + 1)
    assert chat.calls == 2


def test_nothing_screened_calls_no_model():
    chat = FakeChat("x")
    result = impact.summary({**FIGURES, "screened": 0}, chat)
    assert chat.calls == 0 and result.text == "Nothing has been screened yet."


# ---- the figures and the page, on the seeded history ---------------------------

@pytest.fixture
def seeded(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=f"sqlite:///{tmp_path / 'i.db'}"))
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setenv("GUDID_LOOKUP", "off")
    seed_demo.main()
    capsys.readouterr()
    yield
    if db._engine is not None:
        db._engine.dispose()


def test_figures_come_from_the_seeded_history(seeded):
    found = impact.figures()
    assert found["screened"] == seed_demo.HISTORY_SIZE
    assert found["clinics"] == 3 and found["countries"] == ["Haiti", "Nepal", "Uganda"]
    assert found["blocked"] + found["needs_info"] + found["add_ons"] <= found["screened"]
    assert sum(found["blockers"].values()) >= found["blocked"]


def test_the_impact_page_shows_the_gated_paragraph_and_its_label(seeded, monkeypatch):
    chat = FakeChat("Fit-Check screened 20 devices and found 999 problems.")
    monkeypatch.setattr(routes, "_impact_client", lambda: chat)
    body = TestClient(app).get("/impact").text
    assert "999" not in body
    assert impact.WRITTEN_BY_CODE in body
    assert "need answers first" in body

    impact.clear_cache()
    chat.text = "Fit-Check screened 20 devices for 3 clinics."
    body = TestClient(app).get("/impact").text
    assert chat.text in body and impact.WRITTEN_BY_MODEL in body
