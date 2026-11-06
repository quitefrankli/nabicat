"""Read paginated events from the configured rotating application log."""

import json
import logging
import os
import re
from datetime import datetime, timedelta

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from web_app.config import ConfigManager
from web_app.errors import APIError


def parse_log_time(value):
    """Accept ISO timestamps or a relative duration, interpreting naive times locally."""
    if not isinstance(value, str):
        raise ValueError('Time must be an ISO timestamp or a duration such as 30m or 2h')
    duration = re.fullmatch(r'(\d+)([smhd])', value)
    if duration:
        count, unit = duration.groups()
        return (datetime.now().astimezone() - timedelta(**{
            {'s': 'seconds', 'm': 'minutes', 'h': 'hours', 'd': 'days'}[unit]: int(count),
        })).timestamp()
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().timestamp()


def _log_files():
    config = ConfigManager()
    path = config.log_file_path
    return [path, *(path.with_name(f'{path.name}.{index}')
                    for index in range(1, config.dev.log_rotation_backup_count + 1))]


def _reverse_lines(source, end):
    """Yield complete lines and their byte offsets backwards without loading a file."""
    pending = b''
    position = end
    while position:
        size = min(position, ConfigManager().api_logs_read_chunk_bytes)
        position -= size
        source.seek(position)
        pending = source.read(size) + pending
        while (split := pending.rfind(b'\n', 0, len(pending) - 1)) >= 0:
            yield position + split + 1, pending[split + 1:]
            pending = pending[:split + 1]
    if pending:
        yield 0, pending


def _events(source, end):
    continuation = []
    for offset, raw in _reverse_lines(source, end):
        line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
        header = re.match(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$', line)
        if not header:
            continuation.append(line)
            continue
        timestamp, level, message = header.groups()
        # Worker/thread prefixes precede structured JSON; older logs may be plain text.
        payload = {}
        start = message.find('{')
        if start >= 0:
            try:
                decoded = json.loads(message[start:])
                if isinstance(decoded, dict):
                    payload = decoded
            except ValueError:
                pass
        yield offset, {
            'timestamp': datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S,%f').astimezone().isoformat(),
            'level': level, 'fields': payload, 'message': message,
            'traceback': '\n'.join(reversed(continuation)),
        }
        continuation = []


def read_logs(options):
    config = ConfigManager()
    limit = options.get('limit', config.api_logs_page_size)
    if type(limit) is not int or not 1 <= limit <= config.api_logs_page_size:
        raise APIError(f'limit must be between 1 and {config.api_logs_page_size}')
    signer = URLSafeTimedSerializer(current_app.secret_key, salt=config.api_logs_cursor_salt)
    if 'cursor' in options:
        if not isinstance(options['cursor'], str) or not options['cursor']:
            raise APIError('Invalid log cursor; restart the query')
        try:
            state = signer.loads(options['cursor'], max_age=config.api_logs_cursor_max_age_s)
        except (BadData, TypeError):
            raise APIError('Invalid or expired log cursor; restart the query') from None
    else:
        filters = {}
        for key in ('app', 'event', 'request_id', 'level', 'since', 'until'):
            value = options.get(key)
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise APIError(f'{key} must be a non-empty string')
                filters[key] = value
        if 'level' in filters:
            filters['level'] = filters['level'].upper()
            if filters['level'] not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
                raise APIError('Invalid log level')
        try:
            for key in ('since', 'until'):
                if key in filters:
                    filters[key] = parse_log_time(filters[key])
        except (ValueError, OverflowError):
            raise APIError('Invalid time; use an ISO timestamp or duration such as 30m or 2h') from None
        if filters.get('since', float('-inf')) > filters.get('until', float('inf')):
            raise APIError('since must not be later than until')
        sources = []
        for path in _log_files():
            try:
                stat = path.stat()
                if not any(inode == stat.st_ino for inode, _ in sources):
                    sources.append([stat.st_ino, stat.st_size])
            except FileNotFoundError:
                pass
        state = {'sources': sources, 'filters': filters}

    events = []
    filters = state['filters']
    while state['sources']:
        inode, end = state['sources'][0]
        source = None
        for path in _log_files():
            try:
                candidate = path.open('rb')
            except FileNotFoundError:
                continue
            stat = os.fstat(candidate.fileno())
            if stat.st_ino == inode and stat.st_size >= end:
                source = candidate
                break
            candidate.close()
        if source is None:
            raise APIError('Logs for this cursor have rotated out; restart the query')
        with source:
            for offset, event in _events(source, end):
                state['sources'][0][1] = offset
                fields = event['fields']
                if any(fields.get(key) != filters[key] for key in ('app', 'event', 'request_id') if key in filters):
                    continue
                if logging.getLevelName(event['level']) < logging.getLevelName(filters.get('level', 'DEBUG')):
                    continue
                timestamp = datetime.fromisoformat(event['timestamp']).timestamp()
                if not filters.get('since', float('-inf')) <= timestamp <= filters.get('until', float('inf')):
                    continue
                events.append(event)
                if len(events) == limit:
                    return {'events': events, 'next_cursor': signer.dumps(state)}
        state['sources'].pop(0)
    return {'events': events, 'next_cursor': None}
