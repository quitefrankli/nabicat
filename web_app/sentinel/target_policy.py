from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

from web_app.config import ConfigManager
from web_app.web_targets import resolve_web_target


class TargetValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedTarget:
    url: str
    hostname: str
    allowed_hosts: frozenset[str]
    addresses: dict[str, tuple[str, ...]]


def validate_public_web_url(
    raw_url: str,
    *,
    additional_hosts: tuple[str, ...] = (),
) -> ValidatedTarget:
    raw = str(raw_url or "").strip()
    if not raw:
        raise TargetValidationError("URL is required")
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    if parsed.username is not None or parsed.password is not None:
        raise TargetValidationError("URL must not include a username or password")
    requested_hosts = list(additional_hosts)
    counterpart = _www_counterpart(parsed.hostname or "")
    if counterpart and counterpart not in requested_hosts:
        requested_hosts.append(counterpart)
    try:
        described = resolve_web_target(
            raw,
            additional_hosts=tuple(requested_hosts),
            allow_local=ConfigManager().sentinel.allow_local_targets,
        )
    except ValueError as error:
        raise TargetValidationError(str(error)) from error
    hostname = urlparse(described.url).hostname
    if hostname is None:
        raise TargetValidationError("URL must include a valid host")
    return ValidatedTarget(
        url=described.url,
        hostname=hostname.rstrip(".").lower(),
        allowed_hosts=described.allowed_hosts,
        addresses=dict(described.addresses),
    )


def _www_counterpart(hostname: str) -> str | None:
    hostname = hostname.rstrip(".").lower()
    if not hostname or "." not in hostname:
        return None
    try:
        ipaddress.ip_address(hostname)
        return None
    except ValueError:
        pass
    return hostname.removeprefix("www.") if hostname.startswith("www.") else f"www.{hostname}"
