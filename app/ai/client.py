"""Thin wrapper over OCI Generative AI chat.

Every call out of the process goes through here so failures have one shape.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.ai.json_io import JsonReplyError, parse_json_object
from app.config import SETTINGS, Settings

DEFAULT_MAX_TOKENS = 1600
DEFAULT_TEMPERATURE = 0.0


class GenAiError(RuntimeError):
    """Any failure talking to OCI Generative AI, with a message fit for a user."""


@dataclass(frozen=True)
class ChatReply:
    text: str
    model_id: str
    seconds: float


class GenAiClient:
    """Lazily built so importing the app never needs OCI credentials."""

    def __init__(self, settings: Settings = SETTINGS) -> None:
        self._settings = settings
        self._client: Any = None
        self._compartment_id: str | None = settings.compartment_id

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import oci
            from oci.generative_ai_inference import GenerativeAiInferenceClient

            config = oci.config.from_file(
                self._settings.oci_config_path, self._settings.oci_profile
            )
            oci.config.validate_config(config)
            self._compartment_id = self._compartment_id or config["tenancy"]
            self._client = GenerativeAiInferenceClient(
                config,
                service_endpoint=self._settings.genai_endpoint,
                timeout=self._settings.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as one message
            raise GenAiError(
                "Could not reach OCI Generative AI. Check ~/.oci/config and that "
                f"the key is uploaded. Underlying error: {exc}"
            ) from exc
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "",
        image_data_url: str = "",
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> ChatReply:
        """One chat turn. Returns the text, or raises GenAiError."""
        from oci.generative_ai_inference import models as genai

        client = self._ensure_client()
        model_id = model or self._settings.reasoning_model

        content: list[Any] = []
        if image_data_url:
            image = genai.ImageContent()
            image.image_url = genai.ImageUrl(url=image_data_url)
            content.append(image)
        text_content = genai.TextContent()
        text_content.text = prompt
        content.append(text_content)

        user_message = genai.Message()
        user_message.role = "USER"
        user_message.content = content

        messages = []
        if system:
            system_content = genai.TextContent()
            system_content.text = system
            system_message = genai.Message()
            system_message.role = "SYSTEM"
            system_message.content = [system_content]
            messages.append(system_message)
        messages.append(user_message)

        request = genai.GenericChatRequest()
        request.api_format = genai.BaseChatRequest.API_FORMAT_GENERIC
        request.messages = messages
        request.max_tokens = max_tokens
        request.temperature = DEFAULT_TEMPERATURE

        details = genai.ChatDetails()
        details.compartment_id = self._compartment_id
        details.serving_mode = genai.OnDemandServingMode(model_id=model_id)
        details.chat_request = request

        started = time.time()
        try:
            response = client.chat(details)
            text = response.data.chat_response.choices[0].message.content[0].text
        except GenAiError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GenAiError(f"Generative AI call failed: {exc}") from exc

        return ChatReply(text=text, model_id=model_id, seconds=time.time() - started)

    def complete_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """Same call, but insist on a JSON object coming back."""
        reply = self.complete(prompt, **kwargs)
        try:
            return parse_json_object(reply.text)
        except JsonReplyError as exc:
            raise GenAiError(f"The model did not return usable JSON: {exc}") from exc


CLIENT = GenAiClient()
