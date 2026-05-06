import random
from unittest.mock import patch

import pytest

from web_app.app import app
from web_app.config import ConfigManager
from web_app.crosswords.generator import build_crossword
from web_app.crosswords.word_bank import (
    DEBUG_SETS,
    InvalidThemeError,
    clamp_difficulty,
    theme_criteria,
    validate_theme,
)
from web_app.crosswords.word_source import (
    ChainedSource,
    DebugSource,
    FallbackSource,
    MeridianSource,
    WordSource,
)


@pytest.fixture(scope='module', autouse=True)
def setup_app():
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.secret_key = 'test-secret-key'
    from web_app.helpers import limiter, register_all_blueprints
    limiter.enabled = False
    if 'crosswords' not in app.blueprints:
        register_all_blueprints(app)


@pytest.fixture
def client():
    with app.test_client() as c:
        yield c


@pytest.fixture
def debug_mode():
    prev = ConfigManager().debug_mode
    ConfigManager().debug_mode = True
    yield
    ConfigManager().debug_mode = prev


def test_build_crossword_places_words_consistently():
    pairs = [
        ("PYTHON", "Snake lang"),
        ("HONEY", "Bee product"),
        ("NEBULA", "Space cloud"),
        ("ORCHID", "Fancy flower"),
    ]
    puzzle = build_crossword(pairs, rng=random.Random(0))

    assert puzzle["rows"] > 0 and puzzle["cols"] > 0
    assert len(puzzle["cells"]) == puzzle["rows"]
    assert all(len(row) == puzzle["cols"] for row in puzzle["cells"])

    across, down = puzzle["clues"]["across"], puzzle["clues"]["down"]
    assert len(across) + len(down) >= 2

    for clue, is_across in [(c, True) for c in across] + [(c, False) for c in down]:
        for i, letter in enumerate(clue["answer"]):
            r = clue["row"] + (0 if is_across else i)
            c = clue["col"] + (i if is_across else 0)
            cell = puzzle["cells"][r][c]
            assert cell is not None, f"missing cell for {clue['answer']} at ({r},{c})"
            assert cell["letter"] == letter

    start_cells = {(c["row"], c["col"]) for c in across + down}
    numbers_at_start = {(c["row"], c["col"]): c["number"] for c in across + down}
    assert len(start_cells) == len(set(numbers_at_start.values()))


def test_new_endpoint_uses_theme_and_difficulty(client, debug_mode):
    res = client.post('/crosswords/api/new', json={'theme': 'cats', 'difficulty': 2})
    assert res.status_code == 200
    data = res.get_json()
    assert set(data.keys()) >= {"rows", "cols", "cells", "clues", "theme", "difficulty"}
    assert data['theme'] == 'cats'
    assert data['difficulty'] == 2

    answers = {c['answer'] for c in data['clues']['across'] + data['clues']['down']}
    expected = {w for w, _ in DEBUG_SETS[('cats', 2)]}
    assert answers.issubset(expected)
    assert len(answers) >= 2


def test_debug_fixtures_cover_all_themes_and_difficulties():
    themes = {'cats', 'careers', 'music', 'sports', 'ai'}
    for theme in themes:
        for difficulty in range(1, 6):
            key = (theme, difficulty)
            assert key in DEBUG_SETS, f"missing debug fixture for {key}"
            pairs = DEBUG_SETS[key]
            assert len(pairs) == 5
            for word, clue in pairs:
                assert word.isalpha() and word.isupper()
                assert clue


def test_validate_theme_accepts_and_rejects():
    assert validate_theme('Cats') == 'cats'
    assert validate_theme('  sports  ') == 'sports'

    bad_inputs = [
        None, '', '   ',
        'a',                  # too short
        'a' * 14,             # too long
        'two words',          # space
        'ice-cream',          # hyphen
        'theme1',             # digit
        'yay!',               # punctuation
    ]
    for bad in bad_inputs:
        with pytest.raises(InvalidThemeError):
            validate_theme(bad)


def test_clamp_difficulty_bounds():
    assert clamp_difficulty(0) == 1
    assert clamp_difficulty(99) == 5
    assert clamp_difficulty('not a number') == 2


def test_invalid_theme_returns_400_with_criteria(client, debug_mode):
    res = client.post('/crosswords/api/new', json={'theme': 'a', 'difficulty': 2})
    assert res.status_code == 400
    body = res.get_json()
    assert 'error' in body
    assert body['criteria'] == theme_criteria()


def test_debug_source_returns_fixture_exactly():
    pairs = DebugSource().get_pairs('music', 3, count=10)
    assert pairs == DEBUG_SETS[('music', 3)]
    assert DebugSource().get_pairs('nonexistent', 1, count=5) == []


def test_meridian_source_parses_json_response():
    fake_text = (
        '[{"word": "Kitten", "clue": "Baby cat"}, '
        '{"word": "MEOW!", "clue": "Bad word (has punct)"}, '
        '{"word": "tail", "clue": "Swishes when annoyed"}]'
    )
    with patch('web_app.crosswords.word_source.meridian_text', return_value=fake_text):
        pairs = MeridianSource().get_pairs('cats', 2, count=3)

    # "MEOW!" filtered (non-alpha); others uppercased.
    assert pairs == [("KITTEN", "Baby cat"), ("TAIL", "Swishes when annoyed")]


def test_chained_source_falls_back_when_primary_empty():
    class Empty(WordSource):
        def get_pairs(self, theme, difficulty, count):
            return []

    fallback_pairs = [("A", "a"), ("B", "b"), ("C", "c")]

    class Canned(WordSource):
        def get_pairs(self, theme, difficulty, count):
            return fallback_pairs

    chained = ChainedSource([Empty(), Canned()], min_pairs=3)
    assert chained.get_pairs('x', 1, 5) == fallback_pairs
