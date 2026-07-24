"""Exactly-once execution of scheduled jobs across gunicorn workers.

run_once gates a job body on a Redis SET NX EX. Every worker starts its own
scheduler, so without this guard each cron job would fire once per worker. The
test drives the decorated body from multiple simulated workers and asserts it
runs exactly once.

Runs against a real Redis when REDIS_URL points at a reachable server;
otherwise falls back to fakeredis, which faithfully implements the SET NX EX
semantics the guard depends on.
"""
import pytest

import web_app.redis_client as redis_client
from web_app.redis_client import run_once


@pytest.fixture
def redis_backed(monkeypatch):
    """Point get_redis() at a live Redis if available, else fakeredis."""
    client = None
    try:
        import redis as _redis
        candidate = _redis.Redis.from_url(
            redis_client.ConfigManager().redis_url, decode_responses=False
        )
        candidate.ping()
        client = candidate
    except Exception:
        import fakeredis
        client = fakeredis.FakeRedis()

    monkeypatch.setattr(redis_client, "_client", client)
    # Ensure a clean slate for the keys this test uses.
    for key in client.scan_iter("nabicat:sched:test_*"):
        client.delete(key)
    yield client
    for key in client.scan_iter("nabicat:sched:test_*"):
        client.delete(key)


def test_run_once_executes_body_exactly_once(redis_backed):
    calls = []

    @run_once("test_job")
    def job():
        calls.append(1)
        return "ran"

    # Simulate the same cron job firing on multiple workers for one occurrence.
    results = [job() for _ in range(5)]

    assert len(calls) == 1
    assert results.count("ran") == 1
    assert results.count(None) == 4


def test_run_once_independent_across_job_ids(redis_backed):
    ran = []

    @run_once("test_job_a")
    def job_a():
        ran.append("a")

    @run_once("test_job_b")
    def job_b():
        ran.append("b")

    job_a()
    job_b()

    assert sorted(ran) == ["a", "b"]


def test_run_once_fails_safe_when_redis_down(monkeypatch):
    class Boom:
        def set(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(redis_client, "_client", Boom())

    calls = []

    @run_once("test_job_down")
    def job():
        calls.append(1)

    # Redis unreachable -> skip, never run (a duplicated backup/push is worse
    # than a missed run).
    assert job() is None
    assert calls == []
