"""LLM-backed sanity check that a user-supplied crossword theme
is a real, recognisable word or concept.

Kept separate from the word-bank so the format validator can be
tested without mocking HTTP.
"""
from __future__ import annotations

import logging

from web_app.config import ConfigManager
from web_app.crosswords.word_bank import InvalidThemeError
from web_app.helpers import MeridianError, CodexCLIError, meridian_text, codex_cli_text

_SYSTEM = (
    "You are a strict validator. You are given a single English word and must decide "
    "whether it is a real, recognisable word or common concept that a person in their "
    "20s would know. Reply with exactly one token: 'YES' or 'NO'. No explanation."
)


def is_real_word(theme: str, timeout_s: float | None = None) -> bool:
    """Ask Meridian whether ``theme`` is a real word. Returns True on
    network/parse failure so a flaky Meridian never blocks the user.
    """
    config = ConfigManager()
    try:
        text = meridian_text(
            user_message=theme,
            system=_SYSTEM,
            model=config.crosswords_model,
            max_tokens=config.crosswords_theme_check_max_tokens,
            timeout_s=timeout_s or config.crosswords_theme_check_timeout_s,
            agent="crosswords-theme",
        )
    except MeridianError as e:
        logging.warning("theme_check: %s - allowing theme", e)
        return True

    ok = text.strip().upper().startswith("YES")
    logging.info("Crosswords Meridian theme check: theme=%s accepted=%s", theme, ok)
    return ok


def is_real_word_codex(theme: str, timeout_s: float | None = None) -> bool | None:
    """Ask Codex whether ``theme`` is a real word.

    Returns None when Codex is unavailable, allowing callers to preserve
    Meridian's existing fail-open behavior.
    """
    config = ConfigManager()
    try:
        text = codex_cli_text(
            user_message=theme,
            instructions=_SYSTEM,
            model=config.crosswords_codex_model,
            timeout_s=timeout_s or config.crosswords_theme_check_timeout_s,
        )
    except CodexCLIError as e:
        logging.warning("theme_check_codex: %s", e)
        return None

    ok = text.strip().upper().startswith("YES")
    logging.info("Crosswords Codex theme check: theme=%s accepted=%s", theme, ok)
    return ok


def require_real_word(theme: str) -> None:
    """Raise InvalidThemeError when the configured provider rejects the theme."""
    config = ConfigManager()
    provider = config.llm_api_source.lower()
    if config.debug_mode or provider == "hardcoded":
        logging.info("Crosswords theme check skipped: theme=%s source=%s debug=%s", theme, provider, config.debug_mode)
        return

    if provider == "codex":
        codex_ok = is_real_word_codex(theme)
        if codex_ok is not False:
            return
        raise InvalidThemeError(
            f"'{theme}' doesn't look like a real word. Try a single common English word."
        )

    meridian_ok = is_real_word(theme)
    if meridian_ok:
        return

    raise InvalidThemeError(
        f"'{theme}' doesn't look like a real word. Try a single common English word."
    )
