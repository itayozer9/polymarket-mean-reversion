"""Combined runner: collector + paper engine in one process, sharing a tick queue.

This is the recommended deployment for week 1. Both pieces start, both share
a single asyncio.Queue, and the engine consumes ticks as the collector
produces them.

In production we'd split these into 2 processes and use a Unix socket.
"""
from __future__ import annotations
import asyncio
import datetime as dt
import json
import signal
import time

import structlog

from mean_reversion_live.collectors.outcome_writer import OutcomeWriter
from mean_reversion_live.collectors.spot_collector import SpotPriceCache, spot_loop
from mean_reversion_live.collectors.tick_writer import CrashSafeCsvGzAppender
from mean_reversion_live.collectors.ws_collector import WsCollector
from mean_reversion_live.config import get_settings
from mean_reversion_live.engine.paper_engine import PaperEngine
from mean_reversion_live.engine.registry import load_strategies
from mean_reversion_live.logging_config import configure_logging
from mean_reversion_live.markets.discovery import MarketDiscovery

log = structlog.get_logger(__name__)


async def amain() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format, settings.logs_path / "combined.log")
    log.info("combined_starting", symbols=settings.symbol_list)

    # Strategies
    strategies = load_strategies(settings.strategies_path, settings.data_path)
    log.info("strategies_loaded", n=len(strategies), ids=[s.id for s in strategies])

    # Spot
    spot_cache = SpotPriceCache(settings.symbol_list)

    # Writers
    tick_writer = CrashSafeCsvGzAppender(settings.live_data_path)
    outcome_writer = OutcomeWriter(settings.outcomes_path)

    # Shared queue
    tick_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    # Discovery and WS
    ws_collector = None  # forward declaration

    async def on_subscribe(asset_ids):
        if ws_collector is not None:
            await ws_collector.update_subscriptions(asset_ids)

    async def on_close(market, end_price):
        try:
            outcome_writer.append(market, end_price)
        except Exception as e:
            log.warning("outcome_write_failed", slug=market.slug, err=str(e))

    discovery = MarketDiscovery(on_subscribe=on_subscribe, on_close=on_close)
    ws_collector = WsCollector(
        discovery=discovery,
        spot_cache=spot_cache,
        tick_writer=tick_writer,
        out_queue=tick_queue,
    )

    # Paper engine
    engine = PaperEngine(strategies=strategies, queue=tick_queue, data_dir=settings.data_path)

    # Lifecycle
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
                log.warning("kill_sentinel_seen", path=str(settings.kill_sentinel))
                stop.set()
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    async def heartbeat():
        path = settings.state_path / "last_tick.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        while not stop.is_set():
            try:
                hb = {
                    "ts_ms": int(time.time() * 1000),
                    "queue_size": tick_queue.qsize(),
                    "active_markets": len(discovery.active_markets),
                    "n_strategies": len(strategies),
                    "strategy_pnl": {s.id: round(s.portfolio.total_pnl, 3) for s in strategies},
                    "strategy_trades": {s.id: s.portfolio.n_trades for s in strategies},
                }
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(hb))
                import os as _os
                _os.replace(tmp, path)
            except Exception as e:
                log.warning("heartbeat_failed", err=str(e))
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    tasks = [
        asyncio.create_task(spot_loop(spot_cache, settings.symbol_list, stop)),
        asyncio.create_task(discovery.run()),
        asyncio.create_task(ws_collector.run()),
        asyncio.create_task(engine.run()),
        asyncio.create_task(kill_watcher()),
        asyncio.create_task(heartbeat()),
    ]
    log.info("combined_running")

    await stop.wait()
    log.info("combined_stopping")
    discovery.stop()
    ws_collector.stop()
    engine.stop()
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    tick_writer.close()
    log.info("combined_stopped")


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
