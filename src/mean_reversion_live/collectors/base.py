"""Base async collector with SIGTERM + KILL-sentinel handling."""
from __future__ import annotations
import asyncio
import signal
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class AsyncCollector:
    """Subclass override `_run()`. We handle SIGTERM/SIGINT and the KILL file."""

    def __init__(self, kill_sentinel: Path):
        self._stop = asyncio.Event()
        self._kill_sentinel = kill_sentinel

    def stop(self) -> None:
        self._stop.set()

    async def _wait_kill_sentinel(self) -> None:
        while not self._stop.is_set():
            if self._kill_sentinel.exists():
                log.warning("kill_sentinel_seen", path=str(self._kill_sentinel))
                self.stop()
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:
                # Windows or non-main thread
                pass
        watcher = asyncio.create_task(self._wait_kill_sentinel())
        try:
            await self._run()
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        raise NotImplementedError
