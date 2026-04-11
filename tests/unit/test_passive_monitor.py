"""Unit tests for the passive telemetry monitor coroutine (P2).

Covers:
- _passive_monitor calls request_abort when a threshold violation is detected.
- _passive_monitor exits cleanly when stop event is set before any violation.
- No abort is issued when check_thresholds returns an empty list.

Note: asyncio.Event() is created inside async context to ensure compatibility
across Python versions (required on 3.8; still correct on 3.10+).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.planner import _passive_monitor


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_env(violations: list) -> MagicMock:
    """Return a minimal env stub whose interlock reports the given violations."""
    env = MagicMock()
    env._memory.snapshot.return_value.telemetry = {"temperature": 25.0}
    env._interlock.check_thresholds.return_value = violations
    return env


def _make_sched() -> MagicMock:
    sched = MagicMock()
    sched.request_abort = AsyncMock()
    return sched


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

def test_passive_monitor_aborts_on_violation():
    """A threshold violation must trigger request_abort exactly once."""
    violation = {"key": "temperature", "value": 95.0, "spec": "<90"}
    env = _make_env([violation])
    sched = _make_sched()

    async def _run():
        stop = asyncio.Event()
        await _passive_monitor(env, sched, stop)

    asyncio.run(_run())

    sched.request_abort.assert_awaited_once()
    call_kwargs = sched.request_abort.call_args
    assert str(violation) in str(call_kwargs)


def test_passive_monitor_no_abort_without_violation():
    """When thresholds are clean the monitor must not call request_abort."""
    env = _make_env([])
    sched = _make_sched()

    async def _run():
        stop = asyncio.Event()
        # Set the stop event immediately so the loop exits after one iteration.
        stop.set()
        await _passive_monitor(env, sched, stop)

    asyncio.run(_run())

    sched.request_abort.assert_not_awaited()


def test_passive_monitor_exits_on_stop_event():
    """Setting stop before the first sleep must cause a clean exit."""
    env = _make_env([])
    sched = _make_sched()

    async def _run():
        stop = asyncio.Event()
        stop.set()  # pre-set — coroutine should not even sleep
        await _passive_monitor(env, sched, stop)

    asyncio.run(_run())

    sched.request_abort.assert_not_awaited()
