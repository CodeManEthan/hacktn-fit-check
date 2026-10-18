"""Credentials, model defaults and the token cap. No model is called."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app import config as config_module
from app.ai import client as client_module
from app.ai.client import DEFAULT_MAX_TOKENS, GenAiClient, GenAiError, build_signer_and_config
from app.config import ConfigError, SETTINGS, load_settings

FAKE_CONFIG = {"tenancy": "tenancy-id", "region": "us-chicago-1"}


def test_the_token_cap_leaves_room_for_reasoning():
    assert DEFAULT_MAX_TOKENS == 6000


def test_model_and_auth_defaults(monkeypatch):
    for name in ("REASONING_MODEL", "VISION_MODEL", "OCI_AUTH"):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.reasoning_model == "openai.gpt-oss-120b"
    assert settings.vision_model == "meta.llama-4-maverick-17b-128e-instruct-fp8"
    assert settings.oci_auth == "api_key"


def test_oci_auth_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("OCI_AUTH", "Instance_Principal")

    assert load_settings().oci_auth == "instance_principal"


def test_the_api_key_path_reads_and_validates_the_config_file(monkeypatch):
    import oci

    calls = []
    monkeypatch.setattr(oci.config, "from_file",
                        lambda path, profile: calls.append((path, profile)) or dict(FAKE_CONFIG))
    monkeypatch.setattr(oci.config, "validate_config", lambda cfg: calls.append("validated"))

    cfg, signer = build_signer_and_config(replace(SETTINGS, oci_auth="api_key",
                                                  oci_config_path="/k", oci_profile="P"))

    assert cfg == FAKE_CONFIG and signer is None
    assert calls == [("/k", "P"), "validated"]


def test_instance_principals_need_a_compartment():
    with pytest.raises(ConfigError, match="OCI_COMPARTMENT_ID"):
        build_signer_and_config(replace(SETTINGS, oci_auth="instance_principal",
                                        compartment_id=None))


def test_an_unknown_auth_mode_is_a_config_error():
    with pytest.raises(ConfigError, match="OCI_AUTH"):
        build_signer_and_config(replace(SETTINGS, oci_auth="password"))


def test_the_chat_client_builds_from_the_helper(monkeypatch):
    import oci.generative_ai_inference as inference

    built = {}

    def fake_client(cfg, **kwargs):
        built.update(config=cfg, **kwargs)
        return "client"

    monkeypatch.setattr(client_module, "build_signer_and_config",
                        lambda settings: (dict(FAKE_CONFIG), "a-signer"))
    monkeypatch.setattr(inference, "GenerativeAiInferenceClient", fake_client)
    chat = GenAiClient(replace(SETTINGS, compartment_id=None))

    assert chat._ensure_client() == "client"
    assert built["signer"] == "a-signer"
    assert chat._compartment_id == "tenancy-id"


def test_a_credential_failure_reaches_the_user_as_one_error(monkeypatch):
    def refuse(settings):
        raise ConfigError("instance principals not yet wired")

    monkeypatch.setattr(client_module, "build_signer_and_config", refuse)

    with pytest.raises(GenAiError, match="not yet wired"):
        GenAiClient(SETTINGS)._ensure_client()


def test_config_module_still_exposes_settings():
    assert config_module.SETTINGS.oci_auth in config_module.OCI_AUTH_MODES
