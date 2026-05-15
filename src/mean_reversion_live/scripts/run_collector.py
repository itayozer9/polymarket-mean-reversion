"""Entry point: live tick collector.

Wires:
  MarketDiscovery (Gamma poller) →
  WsCollector (WS book stream + 1Hz aggregator) →
    tick_writer (data/live/<symbol>_<date>.csv.gz)
    outcome_writer (data/outcomes.csv on window close)
    asyncio.Queue (consumed by paper trader, if it's running)

Runs forever. Graceful shutdown on SIGTERM/SIGINT or data/KILL.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Optional

import structlog

from mean_reversion_live.collectors.outcome_writer import OutcomeWriter
from mean_reversion_live.collectors.spot_collector import SpotPriceCache, spot_loop
from mean_reversion_live.collectors.tick_writer import CrashSafeCsvGzAppender
from mean_reversion_live.collectors.ws_collector import WsCollector
from mean_reversion_live.config import get_settings
from mean_reversion_live.logging_config import configure_logging
from mean_reversion_live.markets.discovery import MarketDiscovery

log = structlog.get_logger(__name__)


async def amain() -> None:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        log_file=settings.logs_path / "collector.log",
    )
    log.info("collector_starting", symbols=settings.symbol_list, ws=settings.clob_ws_url)

    # 1. Spot cache + collector
    spot_cache = SpotPriceCache(settings.symbol_list)

    # 2. Writers
    tick_writer = CrashSafeCsvGzAppender(settings.live_data_path)
    outcome_writer = OutcomeWriter(settings.outcomes_path)

    # 3. Paper-engine queue (file-backed for inter-process communication later;
    # for now it's just in-memory). The paper trader runs in the same process
    # in dev — for production we'd use a Unix socket or a Redis stream.
    tick_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    # 4. Discovery and WS
    ws_collector: Optional[WsCollector] = None  # set after we build it
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

    # 5. Stop event
    stop = asyncio.Event()
    import signal
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    # 6. KILL sentinel watcher
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

    # 7. last_tick.json heartbeat writer
    async def heartbeat():
        path = settings.state_path / "last_tick.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        while not stop.is_set():
            try:
                import time
                hb = {
                    "ts_ms": int(time.time() * 1000),
                    "queue_size": tick_queue.qsize(),
                    "active_markets": len(discovery.active_markets),
                    "subscriptions": len(getattr(discovery, "_known_subscriptions", set())),
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

    # 8. Run all tasks
    tasks = [
        asyncio.create_task(spot_loop(spot_cache, settings.symbol_list, stop)),
        asyncio.create_task(discovery.run()),
        asyncio.create_task(ws_collector.run()),
        asyncio.create_task(kill_watcher()),
        asyncio.create_task(heartbeat()),
    ]
    log.info("collector_running")

    await stop.wait()
    log.info("collector_stopping")
    discovery.stop()
    ws_collector.stop()
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    tick_writer.close()
    log.info("collector_stopped")


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
