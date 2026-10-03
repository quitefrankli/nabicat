"""Small NabiCat integrations used by Sentinel's synchronous runner."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from redis.exceptions import WatchError

from web_app.config import ConfigManager
from web_app.logging_utils import log_event
from web_app.redis_client import get_redis

from .secrets import redact_error, redact_text

_deadline: ContextVar[float | None] = ContextVar("sentinel_deadline", default=None)


@contextmanager
def deadline_scope(deadline: float) -> Iterator[None]:
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_request_seconds() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else max(0.0, deadline - time.monotonic())


class RequestDeadlineExceeded(TimeoutError):
    pass


def capped_timeout_seconds(requested: float, *, reserve: float = 1.0) -> float:
    remaining = remaining_request_seconds()
    if remaining is None:
        return requested
    available = remaining - reserve
    if available <= 0:
        raise RequestDeadlineExceeded("Sentinel's request deadline is exhausted")
    return min(requested, available)


def capped_timeout_ms(requested: int, *, reserve_ms: int = 1000) -> int:
    seconds = capped_timeout_seconds(requested / 1000, reserve=reserve_ms / 1000)
    return max(1, int(seconds * 1000))


def emit_event(
    name: str,
    *,
    actor=None,
    error: BaseException | None = None,
    level: int | None = None,
    **fields,
) -> None:
    values = {
        key: redact_text(value) if isinstance(value, str) else value
        for key, value in fields.items()
    }
    if error is not None:
        values["error_type"] = type(error).__name__
    log_event(
        "sentinel",
        name,
        level=level or (logging.ERROR if error is not None else logging.INFO),
        user=actor,
        exc_info=redact_error(error) if error is not None else None,
        **values,
    )


def redis_state_key(name: str) -> str:
    return f"nabicat:app:sentinel:state:{name}"


def state_get(name: str) -> bytes | None:
    return get_redis().get(redis_state_key(name))


def state_delete(name: str) -> None:
    get_redis().delete(redis_state_key(name))


def state_put(name: str, value: bytes, *, ttl_s: int, if_absent: bool = False) -> bool:
    return bool(
        get_redis().set(
            redis_state_key(name), value, ex=ttl_s, nx=if_absent or None
        )
    )


def state_replace(name: str, value: bytes) -> bool:
    client = get_redis()
    key = redis_state_key(name)
    while True:
        with client.pipeline() as pipeline:
            try:
                pipeline.watch(key)
                ttl_ms = pipeline.pttl(key)
                if ttl_ms == -2:
                    pipeline.unwatch()
                    return False
                pipeline.multi()
                if ttl_ms >= 0:
                    pipeline.set(key, value, px=max(1, ttl_ms))
                else:
                    pipeline.set(key, value)
                return bool(pipeline.execute()[0])
            except WatchError:
                continue


@contextmanager
def execution_lease():
    """Hold Sentinel's cross-worker execution lock for one synchronous run."""
    cfg = ConfigManager().sentinel
    lease = ExecutionLease(
        "nabicat:app:sentinel:lease:execution", cfg.lease_ttl_s
    )
    if not lease.acquire():
        yield None
        return
    try:
        yield lease
    finally:
        try:
            if not lease.release():
                log_event(
                    "sentinel",
                    "sentinel.execution_lease_release_rejected",
                    level=logging.WARNING,
                    reason="lease_lost",
                )
        except Exception as error:
            log_event(
                "sentinel",
                "sentinel.execution_lease_release_failed",
                level=logging.ERROR,
                exc_info=error,
                error_type=type(error).__name__,
            )


def renew_execution_lease(lease) -> bool:
    return bool(lease and lease.renew())


@dataclass(slots=True)
class ExecutionLease:
    key: str
    ttl_s: int
    token: bytes = b""

    def acquire(self) -> bool:
        import secrets

        self.token = secrets.token_bytes(24)
        return bool(get_redis().set(self.key, self.token, nx=True, ex=self.ttl_s))

    def renew(self) -> bool:
        client = get_redis()
        while True:
            with client.pipeline() as pipeline:
                try:
                    pipeline.watch(self.key)
                    if pipeline.get(self.key) != self.token:
                        pipeline.unwatch()
                        return False
                    pipeline.multi()
                    pipeline.set(self.key, self.token, ex=self.ttl_s)
                    return bool(pipeline.execute()[0])
                except WatchError:
                    continue

    def release(self) -> bool:
        client = get_redis()
        while True:
            with client.pipeline() as pipeline:
                try:
                    pipeline.watch(self.key)
                    if pipeline.get(self.key) != self.token:
                        pipeline.unwatch()
                        return False
                    pipeline.multi()
                    pipeline.delete(self.key)
                    return bool(pipeline.execute()[0])
                except WatchError:
                    continue
