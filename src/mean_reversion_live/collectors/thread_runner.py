"""Run an async collector coroutine in its own OS thread + event loop.

Why this exists
---------------
The combined process runs the paper engine (many strategies, large signal
history) on the main asyncio event loop. That work is CPU-bound and bursty; it
can starve low-priority 1 Hz async tasks on the same loop for minutes at a time
(observed: the pre-existing `macro_dumper` and this repo's fast-spot collector
both freeze while the tick/L2 path — which does big batches then yields — stays
healthy).

A network collector that must keep draining a WebSocket cannot tolerate that
starvation: the socket buffer fills and the server drops the connection.

`ThreadedCollectorRunner` hosts a collector on a *separate* OS thread with its
own event loop, fully isolated from the main loop's CPU contention. This is
purely additive — the thread only ever appends to its own gzip-CSV writer and
shares no state with the decision path.

The hosted object must expose:
    async def run(self)   -> the collector's main coroutine
    def stop(self)        -> signal the coroutine to finish (thread-safe-ish;
                             must only flip an asyncio.Event / plain flag)

A plain coroutine factory is also accepted (see `run_coro_in_thread`).
"""
from __future__ import annotations
import asyncio
import threading
from typing import Awaitable, Callable, Optional

import structlog

log = structlog.get_logger(__name__)


class ThreadedCollectorRunner:
    """Host one async collector on a dedicated thread + event loop."""

    def __init__(self, name: str, coro_factory: Callable[[], Awaitable[None]],
                 on_stop: Optional[Callable[[], None]] = None):
        """`coro_factory` is called *inside* the new thread/loop to build the
        coroutine to run. `on_stop` (optional) is invoked from the caller's
        thread to ask the coroutine to wind down before the loop is closed.
        """
        self._name = name
        self._coro_factory = coro_factory
        self._on_stop = on_stop
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._started = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._thread_main, name=f"collector-{self._name}", daemon=True
        )
        self._thread.start()
        # Wait briefly for the loop to be assigned so stop() is well-defined.
        self._started.wait(timeout=5.0)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._started.set()
        try:
            loop.run_until_complete(self._coro_factory())
        except Exception as e:  # never let the thread crash silently
            log.warning("threaded_collector_crashed", name=self._name, err=str(e))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            log.info("threaded_collector_stopped", name=self._name)

    def stop(self, join_timeout: float = 10.0) -> None:
        """Ask the hosted coroutine to finish and join the thread."""
        if self._on_stop is not None and self._loop is not None:
            # The stop signal (flipping an Event) must run on the collector's
            # own loop so the Event's waiters wake up.
            try:
                self._loop.call_soon_threadsafe(self._on_stop)
            except RuntimeError:
                # Loop already closed.
                pass
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
