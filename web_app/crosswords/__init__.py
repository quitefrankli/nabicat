import logging

from flask import Blueprint, jsonify, render_template, request

from web_app.config import ConfigManager
from web_app.crosswords.generator import build_crossword
from web_app.crosswords.theme_check import require_real_word
from web_app.crosswords.word_bank import (
    InvalidThemeError,
    clamp_difficulty,
    theme_criteria,
    validate_theme,
)
from web_app.crosswords.word_source import default_source

crosswords_api = Blueprint(
    'crosswords',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/crosswords'
)


@crosswords_api.context_processor
def inject_app_name():
    return dict(app_name='Crosswords')


@crosswords_api.route('/')
def index():
    cfg = ConfigManager()
    return render_template(
        'crosswords_index.html',
        default_theme=cfg.crosswords_default_theme,
        default_difficulty=cfg.crosswords_default_difficulty,
        difficulty_min=cfg.crosswords_difficulty_min,
        difficulty_max=cfg.crosswords_difficulty_max,
        theme_min_len=cfg.crosswords_theme_min_len,
        theme_max_len=cfg.crosswords_theme_max_len,
        theme_criteria=theme_criteria(),
    )


@crosswords_api.route('/api/new', methods=['POST'])
def new_crossword():
    cfg = ConfigManager()
    payload = request.get_json(silent=True) or {}
    try:
        theme = validate_theme(payload.get('theme'))
        require_real_word(theme)
    except InvalidThemeError as e:
        logging.info("Crosswords rejected theme: raw=%r reason=%s", payload.get('theme'), e)
        return jsonify({'error': str(e), 'criteria': theme_criteria()}), 400

    difficulty = clamp_difficulty(payload.get('difficulty'))
    count = cfg.crosswords_word_count
    logging.info(
        "Crosswords generation requested: theme=%s difficulty=%s source=%s count=%s",
        theme,
        difficulty,
        cfg.llm_api_source,
        count,
    )
    pairs = default_source().get_pairs(theme=theme, difficulty=difficulty, count=count)
    if not pairs:
        logging.warning("Crosswords generation failed: no word pairs theme=%s difficulty=%s source=%s", theme, difficulty, cfg.llm_api_source)
        return jsonify({'error': 'Could not generate words for that theme. Try another.'}), 503

    puzzle = build_crossword(pairs)
    puzzle['theme'] = theme
    puzzle['difficulty'] = difficulty
    placed = len(puzzle['clues']['across']) + len(puzzle['clues']['down'])
    logging.info(
        "Crosswords generated: theme=%s difficulty=%s source=%s pairs=%s placed=%s grid=%sx%s",
        theme,
        difficulty,
        cfg.llm_api_source,
        len(pairs),
        placed,
        puzzle['rows'],
        puzzle['cols'],
    )
    return jsonify(puzzle)
