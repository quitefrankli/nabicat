"""Server-side, allowlisted client for the local JHunt API."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

import requests
from web_app.config import ConfigManager
from .connection_store import load_connection


class BridgeError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def valid_token(value) -> bool:
    config = ConfigManager()
    return (
        isinstance(value, str)
        and config.jhunt_token_min_chars <= len(value) <= config.jhunt_token_max_chars
        and value.isascii()
        and all(char.isprintable() and not char.isspace() for char in value)
    )


def connection_settings(candidate: dict | None = None) -> dict[str, str | bool]:
    if candidate is not None:
        settings = dict(candidate)
    else:
        settings = ConfigManager().jhunt_connection_settings()
        try:
            saved = load_connection()
        except (OSError, ValueError):
            raise BridgeError("JHunt connection settings could not be read.") from None
        if saved is not None:
            settings.update(saved)
    raw_url = str(settings["base_url"])
    try:
        parsed = urlsplit(raw_url)
    except ValueError as exc:
        raise BridgeError("JHunt connection is unavailable.") from exc
    hostname = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname.lower() == "localhost"
    if (
        not settings["enabled"]
        or not settings["token"]
        or not settings["generation"]
        or parsed.scheme != "http"
        or not loopback
        or not port
        or any(char.isspace() for char in raw_url)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise BridgeError("JHunt connection is unavailable.")
    return {
        **settings,
        "base_url": raw_url.rstrip("/"),
    }


class JHuntClient:
    def __init__(self, settings: dict | None = None):
        self.settings = connection_settings(settings)
        self.config = ConfigManager()
        self.session = requests.Session()
        self.session.trust_env = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        self.session.close()

    @property
    def generation(self) -> str:
        return str(self.settings["generation"])

    def _request(self, method: str, path: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.settings['token']}",
            "Accept": "application/json",
        }
        headers.update(kwargs.pop("headers", {}))
        try:
            timeout = kwargs.pop(
                "timeout",
                (self.config.jhunt_connect_timeout_s, self.config.jhunt_read_timeout_s),
            )
            response = self.session.request(
                method,
                f"{self.settings['base_url']}{path}",
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise BridgeError("JHunt could not be reached.") from exc
        if response.status_code >= 300:
            status = response.status_code if response.status_code in (404, 409, 422) else 502
            response.close()
            raise BridgeError("JHunt rejected the request.", status)
        return response

    def load_inbox(self) -> dict:
        response = self._request(
            "GET", self.config.jhunt_inbox_endpoint, timeout=self.config.jhunt_request_timeout_s
        )
        try:
            snapshot = response.json()
        except (ValueError, requests.RequestException) as exc:
            raise BridgeError("JHunt returned an invalid inbox.", 502) from exc
        finally:
            response.close()
        if (
            not isinstance(snapshot, dict)
            or type(snapshot.get("revision")) is not int
            or snapshot["revision"] < 0
            or not valid_store_id(snapshot.get("store_id"))
            or any(
                not isinstance(snapshot.get(name, []), list)
                for name in ("notifications", "tasks", "applications")
            )
            or ("search" in snapshot and (
                not isinstance(snapshot["search"], dict)
                or not isinstance(snapshot["search"].get("sources", []), list)
                or not isinstance(snapshot["search"].get("active_requests", []), list)
            ))
        ):
            raise BridgeError("JHunt returned an invalid inbox.", 502)
        snapshot["connected"] = True
        snapshot["connection_generation"] = self.generation
        snapshot.setdefault("notifications", [])
        snapshot.setdefault("tasks", [])
        snapshot.setdefault("applications", [])
        snapshot.setdefault("search", {"criteria": None, "sources": [], "active_requests": [], "latest_request": None})
        return snapshot

    def save_criteria(self, body: dict):
        return self._json_post(self.config.jhunt_save_criteria_endpoint, body)

    def start_search(self, body: dict):
        return self._json_post(self.config.jhunt_search_endpoint, body)

    def command_receipt(self, request_key: str):
        return self._json_get(self.config.jhunt_search_command_endpoint.format(request_key=request_key))

    def search_request(self, ident: str):
        return self._json_get(self.config.jhunt_search_request_endpoint.format(ident=ident))

    def _json_get(self, path: str):
        response = self._request("GET", path)
        try:
            return response.json()
        except (ValueError, requests.RequestException) as exc:
            raise BridgeError("JHunt returned an invalid response.", 502) from exc
        finally:
            response.close()

    def answer_task(self, ident: str, body: dict):
        return self._json_post(self.config.jhunt_task_input_endpoint.format(ident=ident), body)

    def decide_application(self, ident: str, body: dict):
        return self._json_post(
            self.config.jhunt_application_decision_endpoint.format(ident=ident), body
        )

    def _json_post(self, path: str, body: dict):
        response = self._request("POST", path, json=body)
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise BridgeError("JHunt returned an invalid response.", 502) from exc
        finally:
            response.close()

    def artifact(self, key: str):
        return self._request(
            "GET", self.config.jhunt_artifact_endpoint.format(key=key),
            headers={"Accept": "application/pdf, image/png, text/plain"}, stream=True,
        )

    def events(self, cursor: str):
        return self._request(
            "GET", self.config.jhunt_event_endpoint,
            params={"cursor": cursor},
            headers={"Accept": "text/event-stream"}, stream=True,
        )


def valid_identifier(value: str) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(ConfigManager().jhunt_identifier_pattern, value) is not None
    )


def valid_artifact_hash(value: str) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(ConfigManager().jhunt_artifact_hash_pattern, value) is not None
    )


def valid_store_id(value) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and valid_identifier(value)


def valid_generation(value) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and valid_identifier(value)
