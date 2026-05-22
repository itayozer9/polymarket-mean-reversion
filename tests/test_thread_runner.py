"""Tests for ThreadedCollectorRunner.

The runner hosts an async collector on a dedicated OS thread + event loop so
the CPU-bound paper engine on the main loop cannot starve it. These tests
verify it starts, runs the coroutine, and stops cleanly.
"""
from __future__ import annotations
import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.collectors.thread_runner import (  # noqa: E402
    ThreadedCollectorRunner,
)


def test_runner_runs_and_stops_cleanly():
    """The hosted coroutine runs on its own loop and stop() ends it."""
    counter = {"n": 0}
    stop_holder = {}

    async def coro():
        ev = asyncio.Event()
        stop_holder["event"] = ev
        while not ev.is_set():
            counter["n"] += 1
            try:
                await asyncio.wait_for(ev.wait(), timeout=0.02)
            except asyncio.TimeoutError:
                pass

    def on_stop():
        ev = stop_holder.get("event")
        if ev is not None:
            ev.set()

    runner = ThreadedCollectorRunner("test", coro, on_stop=on_stop)
    runner.start()
    time.sleep(0.3)  # let the coroutine iterate
    assert counter["n"] > 1  # it actually ran on its own loop
    runner.stop(join_timeout=5.0)
    # Thread must have joined — counter stops advancing.
    final = counter["n"]
    time.sleep(0.1)
    assert counter["n"] == final


def test_runner_isolated_from_caller_thread():
    """A CPU-busy caller thread must NOT prevent the hosted coroutine from
    progressing — the whole point of the dedicated-thread design."""
    counter = {"n": 0}
    stop_holder = {}

    async def coro():
        ev = asyncio.Event()
        stop_holder["event"] = ev
        while not ev.is_set():
            counter["n"] += 1
            try:
                await asyncio.wait_for(ev.wait(), timeout=0.01)
            except asyncio.TimeoutError:
                pass

    def on_stop():
        ev = stop_holder.get("event")
        if ev is not None:
            ev.set()

    runner = ThreadedCollectorRunner("isolated", coro, on_stop=on_stop)
    runner.start()
    # Busy-spin the caller thread for ~0.3s (simulating a CPU-bound main loop).
    deadline = time.time() + 0.3
    while time.time() < deadline:
        _ = sum(range(1000))
    # Despite the busy caller, the hosted coroutine kept iterating.
    assert counter["n"] > 5
    runner.stop(join_timeout=5.0)


def test_runner_swallows_coroutine_exception():
    """A crashing coroutine must not crash the thread / propagate."""
    def on_stop():
        pass

    async def crashing():
        raise RuntimeError("boom")

    runner = ThreadedCollectorRunner("crash", crashing, on_stop=on_stop)
    runner.start()
    runner.stop(join_timeout=5.0)
    # No exception escaped to the test — reaching here is the assertion.
