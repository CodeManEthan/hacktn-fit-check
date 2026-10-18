"""[B6] The client clamps max_tokens to what each model accepts. No model is called."""

from __future__ import annotations

from types import SimpleNamespace

from app.ai import client as client_module
from app.ai.client import DEFAULT_MAX_TOKENS, MODEL_MAX_TOKENS, GenAiClient, max_tokens_for
from app.config import DEFAULT_REASONING_MODEL, DEFAULT_VISION_MODEL, SETTINGS


class _FakeOci:
    """Stands in for the OCI inference client and keeps the request it was sent."""

    def __init__(self):
        self.sent = None

    def chat(self, details):
        self.sent = details
        message = SimpleNamespace(content=[SimpleNamespace(text="ok")])
        return SimpleNamespace(data=SimpleNamespace(
            chat_response=SimpleNamespace(choices=[SimpleNamespace(message=message)])))


def _client() -> tuple[GenAiClient, _FakeOci]:
    client = GenAiClient(SETTINGS)
    fake = _FakeOci()
    client._client = fake
    client._compartment_id = "compartment"
    return client, fake


def test_the_vision_model_is_capped_at_4096():
    assert MODEL_MAX_TOKENS[DEFAULT_VISION_MODEL] == 4096
    assert max_tokens_for(DEFAULT_VISION_MODEL, DEFAULT_MAX_TOKENS) == 4096
    assert max_tokens_for(DEFAULT_VISION_MODEL, 200) == 200


def test_other_models_keep_what_was_asked():
    assert max_tokens_for(DEFAULT_REASONING_MODEL, DEFAULT_MAX_TOKENS) == 6000
    assert max_tokens_for("some.other-model", 9000) == 9000


def test_complete_sends_the_clamped_cap_for_a_vision_call():
    client, fake = _client()
    client.complete("read the plate", model=DEFAULT_VISION_MODEL)
    assert fake.sent.chat_request.max_tokens == 4096


def test_complete_sends_the_default_cap_for_the_reasoning_model():
    client, fake = _client()
    client.complete("hello", model=DEFAULT_REASONING_MODEL)
    assert fake.sent.chat_request.max_tokens == client_module.DEFAULT_MAX_TOKENS
