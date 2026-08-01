"""Real-time Coinbase spot via WebSocket `ticker` channel.

ADDITIVE, separate stream. The existing `spot_collector.py` REST poll
(~14 s effective refresh) feeds the `coinbase_price` column of the sacred
23-column tick CSV and is LEFT UNTOUCHED — the decision path keeps using it.

This module adds a second, faster feed purely for offline research
(lead-lag analysis of spot vs the prediction-market book). It subscribes to
`wss://ws-feed.exchange.coinbase.com` `ticker` channel for BTC/ETH/SOL/XRP-USD
and writes every tick (1 Hz floor, plus every update if faster) to
`data/live_spot/<symbol>_<date>.csv.gz`.

Files: `data/live_spot/<symbol>_<date>.csv.gz`.
"""
from __future__ import annotations
import asyncio
import datetime as dt
import gzip
import json
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import structlog
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

log = structlog.get_logger(__name__)

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"

_SYMBOL_TO_PRODUCT = {
    "btc": "BTC-USD",
    "eth": "ETH-USD",
    "sol": "SOL-USD",
    "xrp": "XRP-USD",
    "bnb": "BNB-USD",
    "doge": "DOGE-USD",
    "hype": "HYPE-USD",
}
_PRODUCT_TO_SYMBOL = {v: k for k, v in _SYMBOL_TO_PRODUCT.items()}

# Fixed schema for the fast-spot stream.
SPOT_COLUMNS: List[str] = [
    "timestamp_ms",   # local receive time (ms epoch, UTC)
    "event_time",     # Coinbase `time` field from the ticker payload (ISO-8601)
    "symbol",         # btc | eth | sol | xrp
    "product_id",     # BTC-USD etc.
    "price",          # last trade price
    "best_bid",       # top-of-book bid
    "best_ask",       # top-of-book ask
    "last_size",      # size of the last trade
    "volume_24h",     # 24h rolling volume
]


def _utc_date_str(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


class SpotCsvGzAppender:
    """Per-symbol-per-date gzip CSV appender for fast Coinbase spot ticks."""

    def __init__(self, base_dir: Path, fsync_every_n_rows: int = 60):
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._files: Dict[tuple, gzip.GzipFile] = {}
        self._rows_since_fsync: Dict[tuple, int] = {}
        self._fsync_every = fsync_every_n_rows
        self._lock = threading.Lock()

    @property
    def columns(self) -> List[str]:
        return list(SPOT_COLUMNS)

    def _open(self, symbol: str, date_str: str) -> gzip.GzipFile:
        path = self._base / f"{symbol}_{date_str}.csv.gz"
        is_new = not path.exists() or path.stat().st_size == 0
        f = gzip.open(str(path), mode="ab", compresslevel=6)
        if is_new:
            header = (",".join(SPOT_COLUMNS) + "\n").encode("utf-8")
            f.write(header)
        return f

    def append(self, row: Dict[str, object]) -> None:
        symbol = str(row.get("symbol") or "unknown")
        ts_ms = int(row.get("timestamp_ms", 0) or 0)
        date_str = _utc_date_str(ts_ms)
        key = (symbol, date_str)
        with self._lock:
            f = self._files.get(key)
            if f is None:
                f = self._open(symbol, date_str)
                self._files[key] = f
                self._rows_since_fsync[key] = 0
            values = []
            for c in SPOT_COLUMNS:
                v = row.get(c, "")
                values.append("" if v is None else str(v))
            line = (",".join(values) + "\n").encode("utf-8")
            f.write(line)
            self._rows_since_fsync[key] += 1
            if self._rows_since_fsync[key] >= self._fsync_every:
                try:
                    f.close()
                except Exception:
                    pass
                f = self._open(symbol, date_str)
                self._files[key] = f
                self._rows_since_fsync[key] = 0

    def close(self) -> None:
        with self._lock:
            for f in self._files.values():
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
            self._files.clear()


def parse_ticker_msg(msg: dict) -> Optional[Dict[str, object]]:
    """Normalise a Coinbase `ticker` WS message into a partial row dict.

    Returns None for non-ticker messages (subscriptions ack, heartbeat, error).
    `timestamp_ms` (local) is NOT set here — the collector stamps it.
    """
    if msg.get("type") != "ticker":
        return None
    product_id = msg.get("product_id") or ""
    symbol = _PRODUCT_TO_SYMBOL.get(product_id)
    if symbol is None:
        return None

    def _num(v):
        if v in (None, ""):
            return ""
        try:
            return float(v)
        except (TypeError, ValueError):
            return ""

    return {
        "event_time": msg.get("time") or "",
        "symbol": symbol,
        "product_id": product_id,
        "price": _num(msg.get("price")),
        "best_bid": _num(msg.get("best_bid")),
        "best_ask": _num(msg.get("best_ask")),
        "last_size": _num(msg.get("last_size")),
        "volume_24h": _num(msg.get("volume_24h")),
    }


class CoinbaseSpotWsCollector:
    """Subscribe to the Coinbase `ticker` channel and write ticks to disk.

    Best-effort: any failure (parse, write, disconnect) is caught and logged;
    it never propagates to the tick/decision path. At least one row per second
    per symbol is written (the latest ticker seen) even when the market is
    quiet; faster updates are written as they arrive.
    """

    def __init__(self, writer: SpotCsvGzAppender, symbols: List[str], min_interval_sec: float = 1.0,
                 spot_cache=None):
        self._writer = writer
        self._symbols = [s for s in symbols if s in _SYMBOL_TO_PRODUCT]
        self._products = [_SYMBOL_TO_PRODUCT[s] for s in self._symbols]
        self._min_interval = min_interval_sec
        # Optional shared SpotPriceCache. When provided, every fresh ticker also
        # updates it, so the engine's tick `coinbase_price`/`move_pct` track the
        # real-time WS price instead of the stale ~14s REST poll. This is what
        # makes the late-window determinism strategy faithful in the live engine
        # (it keys on distance-to-strike, which needs the fresh spot).
        self._spot_cache = spot_cache
        # The stop Event is created lazily inside run(), on whatever event loop
        # actually drives this collector. Creating it in __init__ would bind it
        # to the constructing thread's loop; this collector is hosted on its
        # own thread/loop (see ThreadedCollectorRunner), so a __init__-time
        # Event would raise "got Future attached to a different loop".
        self._stop: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # latest parsed row per symbol + last-write time, for the 1 Hz floor.
        self._latest: Dict[str, Dict[str, object]] = {}
        self._last_write_ms: Dict[str, int] = {}
        self._rows_written = 0

    def stop(self) -> None:
        """Signal the collector to wind down. Safe to call from any thread —
        the Event is set on its owning loop via call_soon_threadsafe."""
        ev, loop = self._stop, self._loop
        if ev is None or loop is None:
            return
        try:
            loop.call_soon_threadsafe(ev.set)
        except RuntimeError:
            # Loop already closed.
            pass

    async def run(self) -> None:
        """Supervise the WS-consume + floor-writer children.

        Either child dying must NOT silently kill the stream — under a busy /
        starved event loop a child can raise (e.g. a WS close timeout). We
        therefore restart any child that exits while we are not stopping, and
        always retrieve its exception so it is never swallowed.
        """
        if not self._products:
            log.warning("spot_ws_no_products")
            return
        # Bind the stop Event to *this* loop.
        self._stop = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        ws_task = asyncio.create_task(self._ws_consume())
        floor_task = asyncio.create_task(self._floor_writer())
        try:
            while not self._stop.is_set():
                done, _ = await asyncio.wait(
                    {ws_task, floor_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if self._stop.is_set():
                    break
                for t in done:
                    # Retrieve the exception so it is never "never retrieved",
                    # then respawn the child.
                    exc = t.exception() if not t.cancelled() else None
                    if t is ws_task:
                        log.warning("spot_ws_consume_exited_respawn",
                                    err=str(exc) if exc else None)
                        ws_task = asyncio.create_task(self._ws_consume())
                    elif t is floor_task:
                        log.warning("spot_ws_floor_exited_respawn",
                                    err=str(exc) if exc else None)
                        floor_task = asyncio.create_task(self._floor_writer())
        finally:
            for t in (ws_task, floor_task):
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    async def _ws_consume(self) -> None:
        backoff = 1.0
        sub_msg = json.dumps({
            "type": "subscribe",
            "product_ids": self._products,
            "channels": ["ticker"],
        })
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    COINBASE_WS_URL, ping_interval=30, ping_timeout=20
                ) as ws:
                    await ws.send(sub_msg)
                    log.info("spot_ws_subscribed", products=self._products)
                    backoff = 1.0
                    recv_task = asyncio.create_task(self._recv_loop(ws))
                    stop_task = asyncio.create_task(self._stop.wait())
                    done, pending = await asyncio.wait(
                        [recv_task, stop_task], return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass
            except (ConnectionClosed, WebSocketException, asyncio.TimeoutError, OSError) as e:
                if self._stop.is_set():
                    break
                log.warning("spot_ws_disconnect", err=str(e), backoff=backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=min(backoff, 30.0))
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    async def _recv_loop(self, ws) -> None:
        async for raw in ws:
            if self._stop.is_set():
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            try:
                row = parse_ticker_msg(msg)
            except Exception:
                continue
            if row is None:
                continue
            symbol = str(row["symbol"])
            self._latest[symbol] = row
            # Feed the shared cache so the engine's coinbase_price/move_pct are
            # fresh (the determinism strategy depends on this). Atomic dict set;
            # safe to call from this collector's own thread.
            if self._spot_cache is not None:
                px = row.get("price")
                if isinstance(px, (int, float)) and px > 0:
                    try:
                        self._spot_cache.set(symbol, float(px))
                    except Exception:
                        pass
            now_ms = int(time.time() * 1000)
            last = self._last_write_ms.get(symbol, 0)
            # Write immediately if it's been >= min_interval since the last
            # write for this symbol; otherwise the floor writer will flush it.
            if now_ms - last >= self._min_interval * 1000:
                self._write_row(symbol, row, now_ms)

    async def _floor_writer(self) -> None:
        """Guarantee at least one row per symbol per `min_interval`, even when
        Coinbase sends no fresh ticker (quiet market).

        Every iteration body is guarded — a transient error must never kill the
        floor writer, otherwise the stream silently stops at the first hiccup.
        """
        ticks = 0
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._min_interval)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                break
            try:
                now_ms = int(time.time() * 1000)
                for symbol, row in list(self._latest.items()):
                    last = self._last_write_ms.get(symbol, 0)
                    if now_ms - last >= self._min_interval * 1000:
                        self._write_row(symbol, row, now_ms)
            except Exception as e:
                log.warning("spot_ws_floor_iteration_failed", err=str(e))
                continue
            ticks += 1
            # Periodic liveness log so the stream is observably alive.
            if ticks <= 2 or ticks % 60 == 0:
                log.info("spot_ws_status", floor_ticks=ticks,
                         rows_written=self._rows_written,
                         symbols_seen=len(self._latest))

    def _write_row(self, symbol: str, row: Dict[str, object], now_ms: int) -> None:
        try:
            full = {"timestamp_ms": now_ms, **row}
            self._writer.append(full)
            self._last_write_ms[symbol] = now_ms
            self._rows_written += 1
        except Exception as e:
            log.warning("spot_ws_write_failed", symbol=symbol, err=str(e))
