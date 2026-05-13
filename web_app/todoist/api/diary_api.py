import re
import flask
import flask_login

from datetime import datetime
from typing import Iterable
from flask import Blueprint, render_template

from web_app.config import ConfigManager
from web_app.helpers import limiter, cur_user, from_req
from web_app.todoist.data_interface import DataInterface, Entry


diary_api = Blueprint('diary_api', __name__, url_prefix='/diary')


@diary_api.before_request
@flask_login.login_required
def require_login():
    pass


def _index_redirect(entry_id: int | None = None):
    if entry_id is not None:
        return flask.redirect(flask.url_for('.index', _anchor=f'entry-{entry_id}'))
    return flask.redirect(flask.url_for('.index'))


_TAG_INVALID = re.compile(r'[^a-z0-9-]')


def _normalize_tag(raw: str) -> str:
    cleaned = raw.strip().lower()
    cleaned = re.sub(r'\s+', '-', cleaned)
    cleaned = _TAG_INVALID.sub('', cleaned)
    cleaned = re.sub(r'-+', '-', cleaned).strip('-')
    return cleaned


def _parse_tags(raw: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for piece in raw.split(','):
        norm = _normalize_tag(piece)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def _parse_mood_rating(raw: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    value = max(0.0, min(5.0, value))
    return round(value * 2) / 2


def _recent_tags(entries: Iterable[Entry]) -> list[str]:
    config = ConfigManager()
    last_used: dict[str, datetime] = {}
    for entry in entries:
        for tag in entry.tags:
            if tag not in last_used or entry.last_modified > last_used[tag]:
                last_used[tag] = entry.last_modified

    ordered = sorted(last_used.keys(), key=lambda t: last_used[t], reverse=True)
    for default_tag in config.diary_default_tags:
        if default_tag not in last_used:
            ordered.append(default_tag)
    return ordered[:config.diary_tag_dropdown_limit]


@diary_api.route('/', methods=['GET'])
@limiter.limit("2/second")
def index():
    data = DataInterface().load_diary(cur_user())
    entries = list(data.entries.values())
    entries.sort(key=lambda e: e.last_modified.timestamp(), reverse=True)
    recent_tags = _recent_tags(data.entries.values())
    return render_template('diary_page.html', entries=entries, recent_tags=recent_tags)


@diary_api.route('/new', methods=['POST'])
@limiter.limit("1/second")
def new_entry():
    data_interface = DataInterface()
    data = data_interface.load_diary(cur_user())
    entry_id = 0 if not data.entries else max(data.entries.keys()) + 1
    now = datetime.now()
    data.entries[entry_id] = Entry(id=entry_id, creation_date=now, last_modified=now)
    data_interface.save_diary(data, cur_user())
    return _index_redirect(entry_id)


@diary_api.route('/edit/<int:entry_id>', methods=['POST'])
@limiter.limit("2/second")
def edit_entry(entry_id: int):
    data_interface = DataInterface()
    data = data_interface.load_diary(cur_user())
    entry = data.entries.get(entry_id)
    if entry is None:
        flask.flash('Entry not found', category='error')
        return _index_redirect()

    entry.title = from_req('title')
    entry.body = from_req('body')
    entry.mood_rating = _parse_mood_rating(from_req('mood_rating'))
    entry.tags = _parse_tags(from_req('tags'))
    entry.last_modified = datetime.now()
    data_interface.save_diary(data, cur_user())
    return _index_redirect(entry_id)


@diary_api.route('/delete/<int:entry_id>', methods=['GET'])
@limiter.limit("1/second")
def delete_entry(entry_id: int):
    data_interface = DataInterface()
    data = data_interface.load_diary(cur_user())
    data.entries.pop(entry_id, None)
    data_interface.save_diary(data, cur_user())
    return _index_redirect()
