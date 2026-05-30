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
import os
import shutil
import signal
import time

import structlog

from mean_reversion_live.collectors.chainlink_collector import (
    DEFAULT_POLYGON_RPC,
    ChainlinkCsvGzAppender,
    chainlink_loop,
)
from mean_reversion_live.collectors.l2_writer import L2CsvGzAppender
from mean_reversion_live.collectors.macro_writer import MacroCsvGzAppender
from mean_reversion_live.collectors.outcome_writer import OutcomeWriter
from mean_reversion_live.collectors.spot_collector import SpotPriceCache, spot_loop
from mean_reversion_live.collectors.spot_ws_collector import (
    CoinbaseSpotWsCollector,
    SpotCsvGzAppender,
)
from mean_reversion_live.collectors.thread_runner import ThreadedCollectorRunner
from mean_reversion_live.collectors.tick_writer import CrashSafeCsvGzAppender
from mean_reversion_live.collectors.trades_writer import TradesCsvGzAppender
from mean_reversion_live.collectors.ws_collector import WsCollector
from mean_reversion_live.config import get_settings
from mean_reversion_live.engine.paper_engine import PaperEngine
from mean_reversion_live.engine.registry import load_strategies
from mean_reversion_live.logging_config import configure_logging
from mean_reversion_live.markets.discovery import MarketDiscovery

log = structlog.get_logger(__name__)


def _count_signals_today(jsonl_root) -> int:
    """Best-effort count of signal events written since UTC midnight today.

    Uses file mtime + a single read of each signals.jsonl. Cheap enough at 5s
    cadence. Returns 0 on any error.
    """
    try:
        if not jsonl_root.exists():
            return 0
        midnight = int(dt.datetime.now(dt.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp() * 1000)
        total = 0
        for sid_dir in jsonl_root.iterdir():
            f = sid_dir / "signals.jsonl"
            if not f.exists():
                continue
            # mtime ≥ midnight is the cheap pre-check; we still need to count
            # only today's lines for accuracy, but if the file hasn't been
            # touched since midnight we can skip it entirely.
            try:
                if f.stat().st_mtime * 1000 < midnight:
                    continue
            except OSError:
                continue
            try:
                with open(f, "r") as fh:
                    for line in fh:
                        try:
                            rec = json.loads(line)
                            if rec.get("ts_ms", 0) >= midnight:
                                total += 1
                        except (json.JSONDecodeError, ValueError):
                            continue
            except OSError:
                continue
        return total
    except Exception:
        return 0


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
    macro_writer = MacroCsvGzAppender(settings.data_path / "live_macro", symbols=settings.symbol_list)
    # Additive full-depth (L2) capture — separate stream, does not touch the
    # 23-column tick schema or the decision path. Used for offline research.
    l2_writer = L2CsvGzAppender(settings.data_path / "live_l2")
    # Additive collector-v2 streams. All three are separate gzip-CSV writers;
    # none touch the 23-column tick schema, tick_writer, or the decision path.
    #   Stream 1: executed-trade prints from the CLOB `market` WS channel.
    #   Stream 2: real-time Coinbase spot via WS ticker channel.
    #   Stream 3: Chainlink on-chain oracle reads from Polygon.
    trades_writer = TradesCsvGzAppender(settings.data_path / "live_trades")
    spot_ws_writer = SpotCsvGzAppender(settings.data_path / "live_spot")
    chainlink_writer = ChainlinkCsvGzAppender(settings.data_path / "live_chainlink")
    polygon_rpc_url = os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC)

    # Streams 2 + 3 are network collectors that must keep draining their
    # sockets / hitting their RPC on a steady cadence. The main event loop is
    # CPU-saturated by the paper engine and starves 1 Hz async tasks for
    # minutes at a time (the pre-existing macro_dumper has the same problem).
    # We therefore host the fast-spot WS collector and the Chainlink poll loop
    # each on their own OS thread + event loop, fully isolated from that
    # contention. They remain purely additive — separate gzip-CSV writers,
    # no shared state with the decision path.
    # Pass the shared spot_cache so the fast WS price feeds the engine's
    # coinbase_price/move_pct (fresh), not just the research CSV. This makes the
    # late-window determinism strategy faithful (it keys on distance-to-strike).
    spot_ws_collector = CoinbaseSpotWsCollector(spot_ws_writer, settings.symbol_list,
                                                spot_cache=spot_cache)
    spot_thread = ThreadedCollectorRunner(
        name="spot_ws",
        coro_factory=spot_ws_collector.run,
        on_stop=spot_ws_collector.stop,
    )

    _chainlink_stop_holder: dict = {}

    async def _chainlink_coro():
        # The stop Event must be created inside the collector thread's own
        # event loop; stash it so the caller thread can set it on shutdown.
        ev = asyncio.Event()
        _chainlink_stop_holder["event"] = ev
        _chainlink_stop_holder["loop"] = asyncio.get_running_loop()
        await chainlink_loop(chainlink_writer, settings.symbol_list, ev,
                             rpc_url=polygon_rpc_url)

    def _chainlink_stop():
        ev = _chainlink_stop_holder.get("event")
        if ev is not None:
            ev.set()

    chainlink_thread = ThreadedCollectorRunner(
        name="chainlink",
        coro_factory=_chainlink_coro,
        on_stop=_chainlink_stop,
    )

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
        # Settle any open determinism positions for this window at the true outcome.
        try:
            engine.settle_window(market, end_price)
        except Exception as e:
            log.warning("det_settle_window_failed", slug=market.slug, err=str(e))

    discovery = MarketDiscovery(on_subscribe=on_subscribe, on_close=on_close)
    ws_collector = WsCollector(
        discovery=discovery,
        spot_cache=spot_cache,
        tick_writer=tick_writer,
        out_queue=tick_queue,
        l2_writer=l2_writer,
        trades_writer=trades_writer,
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
                try:
                    disk_free_gb = round(
                        shutil.disk_usage(settings.data_path).free / (1024 ** 3), 2
                    )
                except OSError:
                    disk_free_gb = None
                signals_today = _count_signals_today(settings.jsonl_path)
                hb = {
                    "ts_ms": int(time.time() * 1000),
                    "queue_size": tick_queue.qsize(),
                    "active_markets": len(discovery.active_markets),
                    "n_strategies": len(strategies),
                    "books_in_memory": len(ws_collector._books),
                    "disk_free_gb": disk_free_gb,
                    "signals_today": signals_today,
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

    async def macro_dumper():
        """Every 1s, snapshot the engine's MarketContext and append a row to
        data/live_macro/<date>.csv.gz. Joined with tick CSVs at review time
        via ts_ms.
        """
        while not stop.is_set():
            try:
                ts_ms = int(time.time() * 1000)
                snap = engine.market_context.snapshot(ts_ms)
                row = {"ts_ms": ts_ms, **snap}
                macro_writer.append(row)
            except Exception as e:
                log.warning("macro_dumper_failed", err=str(e))
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    async def disk_watcher():
        """Bail out gracefully if we're about to run out of disk.

        A 7-day run can write tens of GB of tick CSVs. Hitting ENOSPC mid-gzip
        corrupts the frame. We'd rather stop cleanly with 2GB free.
        """
        threshold_gb = 2.0
        while not stop.is_set():
            try:
                free_gb = shutil.disk_usage(settings.data_path).free / (1024 ** 3)
                if free_gb < threshold_gb:
                    log.critical(
                        "disk_low_stopping",
                        free_gb=round(free_gb, 2),
                        threshold_gb=threshold_gb,
                    )
                    stop.set()
                    return
            except OSError as e:
                log.warning("disk_watcher_failed", err=str(e))
            try:
                await asyncio.wait_for(stop.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                pass

    tasks = [
        asyncio.create_task(spot_loop(spot_cache, settings.symbol_list, stop)),
        asyncio.create_task(discovery.run()),
        asyncio.create_task(ws_collector.run()),
        asyncio.create_task(engine.run()),
        asyncio.create_task(kill_watcher()),
        asyncio.create_task(heartbeat()),
        asyncio.create_task(disk_watcher()),
        asyncio.create_task(macro_dumper()),
    ]
    # Additive collector-v2 streams. Stream 1 (trade prints) rides the existing
    # ws_collector task; Streams 2 + 3 run on dedicated threads so the CPU-bound
    # paper engine on the main loop cannot starve them.
    spot_thread.start()
    chainlink_thread.start()
    log.info("combined_running")

    await stop.wait()
    log.info("combined_stopping")
    discovery.stop()
    ws_collector.stop()
    engine.stop()
    # Stop the dedicated-thread collectors (Streams 2 + 3) and join their
    # threads before closing their writers, so no append races a close().
    spot_thread.stop()
    chainlink_thread.stop()
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    tick_writer.close()
    macro_writer.close()
    l2_writer.close()
    trades_writer.close()
    spot_ws_writer.close()
    chainlink_writer.close()
    log.info("combined_stopped")


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
