"""Meridian-backed sanity check that a user-supplied crossword theme
is a real, recognisable word or concept.

Kept separate from the word-bank so the format validator can be
tested without mocking HTTP.
"""
from __future__ import annotations

import logging

from web_app.config import ConfigManager
from web_app.crosswords.word_bank import InvalidThemeError
from web_app.helpers import MeridianError, meridian_text

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

    return text.strip().upper().startswith("YES")


def require_real_word(theme: str) -> None:
    """Raise InvalidThemeError if Meridian rejects the theme. Skipped in debug mode."""
    if ConfigManager().debug_mode:
        return
    if not is_real_word(theme):
        raise InvalidThemeError(
            f"'{theme}' doesn't look like a real word. Try a single common English word."
        )
