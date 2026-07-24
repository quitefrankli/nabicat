"""rmw_lock: cross-worker mutex for read-modify-write spans.

Backed by fakeredis in tests (see tests/conftest.py). The lock uses SET NX EX
plus a token-checked release so it works without Lua scripting.
"""
import threading

import pytest

from web_app.redis_client import rmw_lock, get_redis, _LOCK_PREFIX


def test_lock_is_mutually_exclusive_across_threads():
    # Reentrancy is per-thread, so a *different* thread (standing in for another
    # gunicorn worker) must be blocked while the lock is held.
    result = {}

    def contender():
        try:
            with rmw_lock("t_excl", timeout_s=5, blocking_timeout_s=0.2):
                result["acquired"] = True
        except TimeoutError:
            result["acquired"] = False

    with rmw_lock("t_excl", timeout_s=5, blocking_timeout_s=1):
        assert get_redis().get(_LOCK_PREFIX + "t_excl") is not None
        t = threading.Thread(target=contender)
        t.start()
        t.join()

    assert result["acquired"] is False


def test_lock_released_on_exit():
    with rmw_lock("t_rel", timeout_s=5, blocking_timeout_s=1):
        pass
    # Key is gone, so a fresh acquire succeeds immediately.
    assert get_redis().get(_LOCK_PREFIX + "t_rel") is None
    with rmw_lock("t_rel", timeout_s=5, blocking_timeout_s=1):
        pass


def test_lock_is_reentrant_within_a_thread():
    # A caller wrapping a span and an inner save locking the same name must not
    # deadlock (same thread re-enters).
    with rmw_lock("t_reentry", timeout_s=5, blocking_timeout_s=1):
        with rmw_lock("t_reentry", timeout_s=5, blocking_timeout_s=0.2):
            assert get_redis().get(_LOCK_PREFIX + "t_reentry") is not None
        # Still held after the inner context exits (only the outer releases).
        assert get_redis().get(_LOCK_PREFIX + "t_reentry") is not None
    # Outer exit releases it.
    assert get_redis().get(_LOCK_PREFIX + "t_reentry") is None


def test_distinct_names_do_not_block_each_other():
    with rmw_lock("t_a", timeout_s=5, blocking_timeout_s=1):
        with rmw_lock("t_b", timeout_s=5, blocking_timeout_s=0.2):
            assert True
