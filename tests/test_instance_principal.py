"""Design §3: the instance-principal path of build_signer_and_config. oci is faked."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.ai.client import GenAiClient, GenAiError, build_signer_and_config
from app.config import ConfigError, SETTINGS

VM = replace(SETTINGS, oci_auth="instance_principal", region="us-chicago-1",
             compartment_id="ocid1.compartment.oc1..fake")


@pytest.fixture
def fake_signer(monkeypatch):
    import oci

    built = []

    class FakeSigner:
        def __init__(self):
            built.append(self)

    monkeypatch.setattr(oci.auth.signers, "InstancePrincipalsSecurityTokenSigner", FakeSigner)
    monkeypatch.setattr(oci.config, "from_file",
                        lambda *a, **k: pytest.fail("instance principals read no config file"))
    return built


def test_the_signer_is_the_instances_and_the_config_is_only_the_region(fake_signer):
    config, signer = build_signer_and_config(VM)

    assert config == {"region": "us-chicago-1"}
    assert fake_signer == [signer]


def test_a_missing_compartment_is_a_config_error_naming_the_variable(fake_signer):
    for missing in (None, "", "  "):
        with pytest.raises(ConfigError, match="OCI_COMPARTMENT_ID"):
            build_signer_and_config(replace(VM, compartment_id=missing))
    assert fake_signer == []


def test_the_chat_client_passes_the_signer_and_the_named_compartment(fake_signer, monkeypatch):
    import oci.generative_ai_inference as inference

    built = {}
    monkeypatch.setattr(inference, "GenerativeAiInferenceClient",
                        lambda cfg, **kw: built.update(config=cfg, **kw) or "client")
    chat = GenAiClient(VM)

    assert chat._ensure_client() == "client"
    assert built["config"] == {"region": "us-chicago-1"}
    assert built["signer"] is fake_signer[0]
    assert chat._compartment_id == "ocid1.compartment.oc1..fake"


def test_the_chat_client_reports_the_missing_compartment_at_first_call(fake_signer):
    chat = GenAiClient(replace(VM, compartment_id=None))

    with pytest.raises(GenAiError, match="OCI_COMPARTMENT_ID") as caught:
        chat._ensure_client()
    assert isinstance(caught.value.__cause__, ConfigError)
