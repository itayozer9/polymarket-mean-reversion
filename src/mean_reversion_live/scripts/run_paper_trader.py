"""Entry point: 24/7 paper trader.

Reads strategies.yaml, consumes ticks from the collector via a file-backed
queue (we use shared-memory asyncio.Queue when running collector + paper
trader in the same process — and that's what we do for simplicity in v1).

In v2 we'll split into separate processes and use a Unix socket or Redis.
For now: run both via `scripts/start_all.sh` and they share a process.

This script is the STANDALONE paper trader. For combined run, see
`scripts/run_combined.py` (added separately).
"""
from __future__ import annotations
import asyncio
import signal

import structlog

from mean_reversion_live.config import get_settings
from mean_reversion_live.engine.paper_engine import PaperEngine
from mean_reversion_live.engine.registry import load_strategies
from mean_reversion_live.logging_config import configure_logging

log = structlog.get_logger(__name__)


async def amain() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format, settings.logs_path / "paper_trader.log")

    strategies = load_strategies(settings.strategies_path, settings.data_path)
    log.info("paper_trader_starting", n_strategies=len(strategies))

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    engine = PaperEngine(strategies=strategies, queue=queue, data_dir=settings.data_path)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async def kill_watcher():
        while not stop.is_set():
            if settings.kill_sentinel.exists():
                stop.set()
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    engine_task = asyncio.create_task(engine.run())
    kill_task = asyncio.create_task(kill_watcher())

    log.warning("paper_trader_standalone_no_tick_source",
                msg="run_combined.py runs collector + engine together; this standalone version has no ticks")

    await stop.wait()
    engine.stop()
    await engine_task
    kill_task.cancel()
    try:
        await kill_task
    except asyncio.CancelledError:
        pass


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
