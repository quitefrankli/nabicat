from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import quote, quote_plus


@dataclass(frozen=True, slots=True)
class RunSecrets:
    """Request-scoped browser values and their non-secret model placeholders."""

    placeholders: dict[str, str] = field(default_factory=dict)
    account_fields: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    card_fields: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    scrub_values: tuple[str, ...] = field(default_factory=tuple)

    def resolve(self, value: str) -> str:
        return self.placeholders.get(value, value)


_CURRENT: ContextVar[RunSecrets | None] = ContextVar("sentinel_secrets", default=None)


def current_secrets() -> RunSecrets:
    return _CURRENT.get() or RunSecrets()


@contextmanager
def secret_scope(secrets: RunSecrets) -> Iterator[None]:
    token = _CURRENT.set(secrets)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def build_secrets(
    account_credentials: dict[str, object] | None,
    card_details: dict[str, str] | None,
) -> RunSecrets:
    raw_values: list[str] = []
    placeholders: dict[str, str] = {}
    account_fields: list[tuple[str, str]] = []
    card_fields: list[tuple[str, str]] = []
    if account_credentials:
        for key in ("username", "password"):
            value = str(account_credentials.get(key, ""))
            if value:
                raw_values.append(value)
                placeholder = _placeholder(len(placeholders))
                placeholders[placeholder] = value
                account_fields.append((key, placeholder))
        extras = account_credentials.get("extras")
        if isinstance(extras, dict):
            for label, raw_value in extras.items():
                value = str(raw_value)
                if not value:
                    continue
                raw_values.append(value)
                placeholder = _placeholder(len(placeholders))
                placeholders[placeholder] = value
                account_fields.append((str(label), placeholder))
    if card_details:
        for key, raw_value in card_details.items():
            value = str(raw_value)
            if not value:
                continue
            raw_values.append(value)
            if key == "card_number":
                raw_values.extend(_grouped_card_numbers(value))
            elif key == "expiry" and re.fullmatch(r"\d{2}/\d{2}", value):
                month, year = value.split("/", 1)
                raw_values.extend((f"{month} / {year}", f"{month}-{year}", month + year))
            placeholder = _placeholder(len(placeholders))
            placeholders[placeholder] = value
            card_fields.append((key, placeholder))
    scrub_values = {
        variant for value in raw_values for variant in _common_renderings(value) if variant
    }
    return RunSecrets(
        placeholders=placeholders,
        account_fields=tuple(account_fields),
        card_fields=tuple(card_fields),
        scrub_values=tuple(sorted(scrub_values, key=len, reverse=True)),
    )


def redact_text(value: str) -> str:
    rendered = str(value)
    secrets = current_secrets()
    if not secrets.scrub_values:
        return rendered
    placeholders = tuple(secrets.placeholders)
    if not placeholders:
        return _redact_segment(rendered, secrets.scrub_values)
    protected = re.compile("(" + "|".join(re.escape(item) for item in placeholders) + ")")
    return "".join(
        part if part in secrets.placeholders else _redact_segment(part, secrets.scrub_values)
        for part in protected.split(rendered)
    )


def contains_secret(value: str) -> bool:
    """Return whether a value contains any rendering of a scoped secret."""
    secrets = current_secrets()
    if not secrets.scrub_values:
        return False
    rendered = _normalize_percent_escapes(str(value))
    return any(secret in rendered for secret in secrets.scrub_values)


def redact_error(error: BaseException) -> BaseException:
    """Protect scoped secrets while retaining normal tracebacks when no secrets exist."""
    if not current_secrets().scrub_values:
        return error
    return RuntimeError(f"{type(error).__name__}: {redact_text(str(error))}")


def _common_renderings(value: str) -> set[str]:
    normalized = {
        value,
        unicodedata.normalize("NFC", value),
        unicodedata.normalize("NFD", value),
    }
    rendered = set(normalized)
    for item in normalized:
        rendered.update(
            {
                quote(item, safe=""),
                quote_plus(item, safe=""),
                html.escape(item, quote=True),
                html.escape(item, quote=False),
                json.dumps(item, ensure_ascii=False)[1:-1],
                json.dumps(item, ensure_ascii=True)[1:-1],
            }
        )
    return {_normalize_percent_escapes(item) for item in rendered}


def _placeholder(index: int) -> str:
    return f"{{{{sentinel.secret.{index}}}}}"


def _grouped_card_numbers(value: str) -> tuple[str, ...]:
    if not value.isdigit() or not 13 <= len(value) <= 19:
        return ()
    groupings = [[value[index : index + 4] for index in range(0, len(value), 4)]]
    if len(value) == 15:
        groupings.append([value[:4], value[4:10], value[10:]])
    return tuple(
        rendered
        for grouping in groupings
        for separator in (" ", "-")
        if (rendered := separator.join(grouping))
    )


def _redact_segment(value: str, scrub_values: tuple[str, ...]) -> str:
    value = _normalize_percent_escapes(value)
    for secret in scrub_values:
        value = value.replace(secret, "[redacted]")
    return value


def _normalize_percent_escapes(value: str) -> str:
    return re.sub(
        r"%[0-9A-Fa-f]{2}",
        lambda match: match.group(0).upper(),
        value,
    )
