"""Pull a JSON object out of whatever the model actually sent back."""

from __future__ import annotations

import json
import re
from typing import Any

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class JsonReplyError(ValueError):
    """The reply held no JSON object we could use."""


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object in a model reply.

    Models wrap JSON in prose or code fences often enough that trusting
    json.loads on the raw string is a guaranteed intermittent failure.
    """
    if not text or not text.strip():
        raise JsonReplyError("the model returned an empty reply")

    for candidate in _candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise JsonReplyError(f"no JSON object found in: {text[:200]}")


def _candidates(text: str) -> list[str]:
    found = [text.strip()]
    found.extend(match.strip() for match in FENCE.findall(text))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        found.append(text[start : end + 1])
    return found
