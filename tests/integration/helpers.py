"""Small helpers for browser-like integration requests."""

import re

import requests


def get_csrf_token(session: requests.Session, server_url: str, path: str = "/") -> str:
    response = session.get(f"{server_url}{path}")
    response.raise_for_status()
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', response.text)
    if not match:
        raise AssertionError(f"CSRF token not found on {path}")
    return match.group(1)


def with_csrf(session: requests.Session, server_url: str, data: dict | None = None, path: str = "/") -> dict:
    payload = dict(data or {})
    payload["csrf_token"] = get_csrf_token(session, server_url, path)
    return payload
