from __future__ import annotations

import json
import re
from dataclasses import dataclass


class ActionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class AgentAction:
    action: str
    reason: str = ""
    element_id: str | None = None
    value: str | None = None
    url: str | None = None


_ALLOWED_ACTIONS = {"click", "fill", "goto", "scroll", "wait", "finish"}


def _json_from_text(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ActionValidationError("Agent did not return JSON") from e
    if not isinstance(data, dict):
        raise ActionValidationError("Agent action must be a JSON object")
    return data


def parse_agent_action(text: str, known_element_ids: set[str]) -> AgentAction:
    data = _json_from_text(text)
    action = str(data.get("action", "")).strip().lower()
    if action not in _ALLOWED_ACTIONS:
        raise ActionValidationError("Unknown agent action")

    element_id = data.get("element_id")
    if element_id is not None:
        element_id = str(element_id).strip()
    if action in {"click", "fill"}:
        if not element_id or element_id not in known_element_ids:
            raise ActionValidationError("Agent referenced an unknown element")
    elif element_id:
        raise ActionValidationError("Element id is only valid for click/fill")

    value = data.get("value")
    url = data.get("url")
    return AgentAction(
        action=action,
        reason=str(data.get("reason", "")).strip(),
        element_id=element_id,
        value=str(value) if value is not None else None,
        url=str(url) if url is not None else None,
    )
