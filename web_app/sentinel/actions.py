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


_ALLOWED_ACTIONS = {"click", "fill", "goto", "scroll", "select", "wait", "finish"}


def _json_from_text(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = _extract_last_json_object(cleaned)
        if data is None:
            raise ActionValidationError("Agent did not return JSON")
    if not isinstance(data, dict):
        raise ActionValidationError("Agent action must be a JSON object")
    return data


def _extract_last_json_object(text: str) -> dict | None:
    """Find the LAST top-level JSON object in a text blob.

    Models sometimes "think out loud" with multiple JSON candidates separated
    by prose. We treat the last brace-balanced object as the model's final
    answer. Aware of strings + escapes so { and } inside string values don't
    fool the depth tracker.
    """
    candidates: list[dict] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                snippet = text[start : i + 1]
                start = -1
                try:
                    parsed = json.loads(snippet)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    candidates.append(parsed)
    return candidates[-1] if candidates else None


def parse_agent_action(text: str, known_element_ids: set[str]) -> AgentAction:
    data = _json_from_text(text)
    action = str(data.get("action", "")).strip().lower()
    if action not in _ALLOWED_ACTIONS:
        raise ActionValidationError("Unknown agent action")

    element_id = data.get("element_id")
    if element_id is not None:
        element_id = str(element_id).strip()
    if action in {"click", "fill", "select"}:
        if not element_id or element_id not in known_element_ids:
            raise ActionValidationError("Agent referenced an unknown element")
    elif element_id:
        raise ActionValidationError("Element id is only valid for click/fill/select")

    value = data.get("value")
    url = data.get("url")
    return AgentAction(
        action=action,
        reason=str(data.get("reason", "")).strip(),
        element_id=element_id,
        value=str(value) if value is not None else None,
        url=str(url) if url is not None else None,
    )
