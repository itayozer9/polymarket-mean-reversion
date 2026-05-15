"""WebSocket consumer for Polymarket CLOB book stream.

Maintains per-token-id orderbooks from `event_type:"book"` + `event_type:"price_change"`
messages. A separate per-second aggregator joins YES/NO books for each active
market and emits one row to (a) the tick CSV.gz writer and (b) an asyncio queue
for the paper trading engine.
"""
from __future__ import annotations
import asyncio
import json
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

import structlog
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from mean_reversion_live.collectors.spot_collector import SpotPriceCache
from mean_reversion_live.collectors.tick_writer import CrashSafeCsvGzAppender
from mean_reversion_live.config import get_settings
from mean_reversion_live.markets.discovery import MarketDiscovery
from mean_reversion_live.markets import windows as win

log = structlog.get_logger(__name__)


class OrderBook:
    """Single-token book. Top-of-book + total depth at top level only."""

    __slots__ = ("bids", "asks", "last_msg_ms")

    def __init__(self):
        # bids: price (float) -> total size at that price
        # asks: price (float) -> total size at that price
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.last_msg_ms: int = 0

    def apply_book_snapshot(self, msg: dict) -> None:
        self.bids = {}
        self.asks = {}
        for lvl in msg.get("bids") or []:
            try:
                p, s = float(lvl["price"]), float(lvl["size"])
            except (KeyError, ValueError, TypeError):
                continue
            if s > 0:
                self.bids[p] = s
        for lvl in msg.get("asks") or []:
            try:
                p, s = float(lvl["price"]), float(lvl["size"])
            except (KeyError, ValueError, TypeError):
                continue
            if s > 0:
                self.asks[p] = s
        self.last_msg_ms = int(time.time() * 1000)

    def apply_price_change(self, msg: dict) -> None:
        """`changes`: list of {price, side, size}. side ∈ {"BUY","SELL"} (BUY=bid)."""
        for ch in msg.get("changes") or []:
            try:
                p = float(ch.get("price"))
                s = float(ch.get("size"))
                side = (ch.get("side") or "").upper()
            except (TypeError, ValueError):
                continue
            book = self.bids if side == "BUY" else self.asks
            if s <= 0:
                book.pop(p, None)
            else:
                book[p] = s
        self.last_msg_ms = int(time.time() * 1000)

    def best_bid_ask(self) -> Tuple[float, float, float, float]:
        """Return (best_bid, best_ask, bid_size_at_best, ask_size_at_best). Zeros if empty."""
        bb = max(self.bids) if self.bids else 0.0
        ba = min(self.asks) if self.asks else 0.0
        bb_size = self.bids.get(bb, 0.0) if bb > 0 else 0.0
        ba_size = self.asks.get(ba, 0.0) if ba > 0 else 0.0
        return bb, ba, bb_size, ba_size


class WsCollector:
    """Consume Polymarket CLOB WS + run a 1Hz aggregator."""

    def __init__(
        self,
        discovery: MarketDiscovery,
        spot_cache: SpotPriceCache,
        tick_writer: CrashSafeCsvGzAppender,
        out_queue: Optional[asyncio.Queue] = None,
    ):
        self._discovery = discovery
        self._spot = spot_cache
        self._writer = tick_writer
        self._out_queue = out_queue  # bounded; paper engine reads here
        self._stop = asyncio.Event()
        self._books: Dict[str, OrderBook] = defaultdict(OrderBook)
        self._desired_subs: Set[str] = set()
        self._ws_lock = asyncio.Lock()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        # Polymarket CLOB WS accepts the subscription message once per connection.
        # When the subscription set changes we need to drop the current connection
        # and reconnect so the new subscribe lands at the start of the new session.
        self._reconnect_signal = asyncio.Event()
        self._has_subs = asyncio.Event()  # gates WS startup until first discovery

    def stop(self) -> None:
        self._stop.set()

    async def update_subscriptions(self, asset_ids: List[str]) -> None:
        """Called by MarketDiscovery when the active set changes.

        Polymarket CLOB WS only honors the subscription sent at session start.
        We update the desired-subs set + signal ws_consume to drop the current
        connection so the next reconnect sends the updated subscribe.
        """
        new_set = set(asset_ids)
        if new_set == self._desired_subs:
            return
        self._desired_subs = new_set
        log.info("ws_subscriptions_updated", n=len(asset_ids))
        self._has_subs.set()
        # Force reconnect by closing the current WS (if any).
        self._reconnect_signal.set()
        async with self._ws_lock:
            ws = self._ws
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass

    async def _ws_consume(self) -> None:
        url = get_settings().clob_ws_url
        backoff = 1.0
        # Wait for first subscription before opening the WS — Polymarket only
        # accepts the subscribe sent at session start.
        log.info("ws_waiting_for_subscriptions")
        try:
            await asyncio.wait_for(self._has_subs.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            log.error("ws_no_subscriptions_in_120s")
            return
        while not self._stop.is_set():
            self._reconnect_signal.clear()
            try:
                async with websockets.connect(url, ping_interval=30, ping_timeout=20) as ws:
                    async with self._ws_lock:
                        self._ws = ws
                    subs = sorted(self._desired_subs)
                    if not subs:
                        log.warning("ws_no_subs_skip_send")
                    else:
                        await ws.send(json.dumps({"type": "market", "assets_ids": subs}))
                        log.info("ws_subscribed", n_assets=len(subs))
                    backoff = 1.0
                    # Race: either the WS yields a message OR a reconnect is signaled.
                    recv_task = asyncio.create_task(self._recv_loop(ws))
                    reconnect_task = asyncio.create_task(self._reconnect_signal.wait())
                    stop_task = asyncio.create_task(self._stop.wait())
                    done, pending = await asyncio.wait(
                        [recv_task, reconnect_task, stop_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass
                    if reconnect_task in done and not self._stop.is_set():
                        log.info("ws_reconnect_requested")
                        # Drop out of the async-with to close + loop reconnects
                        continue
            except (ConnectionClosed, WebSocketException, asyncio.TimeoutError, OSError) as e:
                if self._stop.is_set():
                    break
                log.warning("ws_disconnect", err=str(e), backoff=backoff)
                async with self._ws_lock:
                    self._ws = None
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
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payload = [payload]
            for msg in payload:
                self._handle_ws_msg(msg)

    def _handle_ws_msg(self, msg: dict) -> None:
        kind = msg.get("event_type") or msg.get("type")
        token_id = str(msg.get("asset_id") or msg.get("market") or "")
        if not token_id:
            return
        book = self._books[token_id]
        if kind == "book":
            book.apply_book_snapshot(msg)
        elif kind == "price_change":
            book.apply_price_change(msg)
        # ignore other event types

    async def _aggregator(self) -> None:
        """Once per second, emit one CSV row per active market."""
        next_tick = int(time.time()) + 1
        ticks_seen = 0
        last_status_ts = next_tick
        while not self._stop.is_set():
            now = time.time()
            sleep_for = max(0.0, next_tick - now)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                break
            second_ts = next_tick
            next_tick = second_ts + 1
            n = await self._emit_for_second(second_ts)
            ticks_seen += n
            # Status log every 10s — shows the aggregator is alive even if no rows.
            if second_ts - last_status_ts >= 10:
                log.info(
                    "aggregator_status",
                    elapsed_sec=second_ts - last_status_ts,
                    rows_written=ticks_seen,
                    active_markets=len(self._discovery.active_markets),
                    books_seen=len(self._books),
                )
                ticks_seen = 0
                last_status_ts = second_ts

    async def _emit_for_second(self, second_ts: int) -> int:
        active = self._discovery.active_markets
        ts_ms = second_ts * 1000
        emitted = 0
        skipped_window = 0
        skipped_book = 0
        for slug, m in active.items():
            if second_ts < m.window_start_ts or second_ts >= m.window_end_ts:
                skipped_window += 1
                continue
            yes_book = self._books.get(m.yes_token_id)
            no_book = self._books.get(m.no_token_id)
            if yes_book is None or no_book is None:
                skipped_book += 1
                continue
            ybb, yba, ybd, yad = yes_book.best_bid_ask()
            nbb, nba, nbd, nad = no_book.best_bid_ask()
            coinbase_price = self._spot.get(m.symbol) or 0.0
            sp = m.start_price or 0.0
            move_pct = ((coinbase_price - sp) / sp * 100) if (sp > 0 and coinbase_price > 0) else 0.0
            yes_mid = (ybb + yba) / 2.0 if (ybb > 0 and yba > 0) else 0.0
            no_mid = (nbb + nba) / 2.0 if (nbb > 0 and nba > 0) else 0.0
            spread_yes = (yba - ybb) if (ybb > 0 and yba > 0) else 0.0
            spread_no = (nba - nbb) if (nbb > 0 and nba > 0) else 0.0
            total_mid = yes_mid + no_mid
            row = {
                "timestamp_ms": ts_ms,
                "market_slug": slug,
                "symbol": m.symbol,
                "window_start_ts": m.window_start_ts,
                "window_end_ts": m.window_end_ts,
                "seconds_into_window": second_ts - m.window_start_ts,
                "yes_best_bid": ybb,
                "yes_best_ask": yba,
                "yes_bid_depth": ybd,
                "yes_ask_depth": yad,
                "no_best_bid": nbb,
                "no_best_ask": nba,
                "no_bid_depth": nbd,
                "no_ask_depth": nad,
                "chainlink_price": 0.0,  # not used; column reserved for future Chainlink
                "coinbase_price": coinbase_price,
                "start_price": sp,
                "move_pct": round(move_pct, 6),
                "yes_mid": yes_mid,
                "no_mid": no_mid,
                "spread_yes": round(spread_yes, 6),
                "spread_no": round(spread_no, 6),
                "total_mid": round(total_mid, 6),
            }
            self._writer.append(row)
            emitted += 1
            if self._out_queue is not None:
                try:
                    self._out_queue.put_nowait(row)
                except asyncio.QueueFull:
                    # Backpressure: paper engine fell behind. Drop the row.
                    # Future improvement: log to errors.jsonl.
                    pass
        if second_ts % 10 == 0:
            log.info(
                "emit_summary",
                second_ts=second_ts,
                emitted=emitted,
                skipped_window=skipped_window,
                skipped_book=skipped_book,
                total=len(active),
            )
        return emitted

    async def run(self) -> None:
        ws_task = asyncio.create_task(self._ws_consume())
        agg_task = asyncio.create_task(self._aggregator())
        try:
            await self._stop.wait()
        finally:
            ws_task.cancel()
            agg_task.cancel()
            for t in (ws_task, agg_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
