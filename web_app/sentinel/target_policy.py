from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


class TargetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    hostname: str


def _is_blocked_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolved_ips(hostname: str, port: int | None) -> set[str]:
    try:
        infos = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise TargetValidationError("Could not resolve target host") from e
    return {info[4][0] for info in infos}


def validate_public_web_url(raw_url: str) -> ValidatedTarget:
    url = (raw_url or "").strip()
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise TargetValidationError("URL must start with http:// or https://")
    if not parsed.hostname:
        raise TargetValidationError("URL must include a valid host")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise TargetValidationError("Local targets are not allowed")

    try:
        if _is_blocked_ip(hostname):
            raise TargetValidationError("Private or local targets are not allowed")
    except ValueError:
        for ip in _resolved_ips(hostname, parsed.port):
            if _is_blocked_ip(ip):
                raise TargetValidationError("Target resolves to a private or local address")

    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    userinfo = f"{parsed.netloc.rsplit('@', 1)[0]}@" if "@" in parsed.netloc else ""
    normalized_netloc = f"{userinfo}{host_for_netloc}"
    if parsed.port:
        normalized_netloc = f"{normalized_netloc}:{parsed.port}"
    normalized = urlunparse(
        (
            parsed.scheme,
            normalized_netloc,
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )
    return ValidatedTarget(url=normalized, hostname=hostname)
