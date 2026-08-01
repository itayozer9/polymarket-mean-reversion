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

try:  # WS message decode is the process's hottest CPU path (profiled 2026-06-12:
    # stdlib scan_once/scanstring ~30-40% of the core at ~700 msg/s). orjson is
    # a drop-in C decoder ~8x faster; stdlib remains the fallback.
    import orjson

    def _loads(raw):
        return orjson.loads(raw)
except ImportError:  # pragma: no cover
    def _loads(raw):
        return json.loads(raw)

from mean_reversion_live.collectors.l2_writer import L2CsvGzAppender, book_to_l2_row
from mean_reversion_live.collectors.spot_collector import SpotPriceCache
from mean_reversion_live.collectors.tick_writer import CrashSafeCsvGzAppender
from mean_reversion_live.collectors.trades_writer import TradesCsvGzAppender, parse_trade_event
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

    def top_levels(self, n: int) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
        """Return ([ (bid_px, bid_sz), ... ], [ (ask_px, ask_sz), ... ]).

        Bids sorted DESC by price (best first), asks sorted ASC by price
        (best first). Each list has exactly `n` entries — short sides are
        padded with (0.0, 0.0) so the CSV schema is fixed-width.
        """
        bid_prices = sorted(self.bids.keys(), reverse=True)[:n]
        ask_prices = sorted(self.asks.keys())[:n]
        bids = [(p, self.bids[p]) for p in bid_prices]
        asks = [(p, self.asks[p]) for p in ask_prices]
        while len(bids) < n:
            bids.append((0.0, 0.0))
        while len(asks) < n:
            asks.append((0.0, 0.0))
        return bids, asks


class WsCollector:
    """Consume Polymarket CLOB WS + run a 1Hz aggregator."""

    def __init__(
        self,
        discovery: MarketDiscovery,
        spot_cache: SpotPriceCache,
        tick_writer: CrashSafeCsvGzAppender,
        out_queue: Optional[asyncio.Queue] = None,
        l2_writer: Optional[L2CsvGzAppender] = None,
        trades_writer: Optional[TradesCsvGzAppender] = None,
        ws_url: Optional[str] = None,
        n_shards: int = 4,
        keepalive_s: float = 5.0,
    ):
        self._discovery = discovery
        self._spot = spot_cache
        self._writer = tick_writer
        self._l2_writer = l2_writer  # additive full-depth stream; None disables
        self._trades_writer = trades_writer  # additive trade-print stream; None disables
        self._out_queue = out_queue  # bounded; paper engine reads here
        self._trades_seen = 0  # diagnostic counter for the trade-print stream
        self._stop = asyncio.Event()
        self._books: Dict[str, OrderBook] = defaultdict(OrderBook)
        self._desired_subs: Set[str] = set()
        self._ws_url = ws_url or get_settings().clob_ws_url
        # Polymarket's WS server enforces a per-CONNECTION message-rate cap and
        # silently RSTs any connection exceeding it (observed ~1.6k msg/s dies
        # in ~10s; 15m+5m books together exceed it, each half alone does not —
        # 2026-06-12 outage). The asset set is therefore sharded across
        # n_shards independent connections (stable int(token) % n_shards), all
        # feeding the same _books dict.
        self._n_shards = max(1, int(n_shards))
        self._keepalive_s = float(keepalive_s)
        self._ws_lock = asyncio.Lock()
        self._ws_conns: List[Optional[websockets.WebSocketClientProtocol]] = (
            [None] * self._n_shards)
        # Polymarket CLOB WS accepts the subscription message once per connection.
        # When a shard's subset changes we drop THAT connection and reconnect so
        # the new subscribe lands at the start of the new session. Other shards
        # stay up (15m books must not churn when 5m windows rotate).
        self._reconnect_signals: List[asyncio.Event] = (
            [asyncio.Event() for _ in range(self._n_shards)])
        self._shard_subs: List[Set[str]] = [set() for _ in range(self._n_shards)]

    def _shard_of(self, token_id: str) -> int:
        try:
            return int(token_id) % self._n_shards
        except (TypeError, ValueError):
            return hash(token_id) % self._n_shards

    def stop(self) -> None:
        self._stop.set()

    async def update_subscriptions(self, asset_ids: List[str]) -> None:
        """Called by MarketDiscovery when the active set changes.

        Polymarket CLOB WS only honors the subscription sent at session start.
        We split the set across shards; ONLY shards whose subset changed get
        their connection dropped so the next reconnect sends the new subscribe.
        """
        new_set = set(asset_ids)
        if new_set == self._desired_subs:
            return
        self._desired_subs = new_set
        # GC orderbooks for tokens we no longer subscribe to. Otherwise _books
        # accumulates one entry per ever-seen token over 7+ days (~3,000 entries).
        stale = [tok for tok in self._books if tok not in new_set]
        for tok in stale:
            del self._books[tok]
        new_shards: List[Set[str]] = [set() for _ in range(self._n_shards)]
        for tok in new_set:
            new_shards[self._shard_of(tok)].add(tok)
        changed = [i for i in range(self._n_shards)
                   if new_shards[i] != self._shard_subs[i]]
        self._shard_subs = new_shards
        log.info("ws_subscriptions_updated", n=len(asset_ids), books_gc=len(stale),
                 shards_changed=changed)
        # Force reconnect of the changed shards by closing their WS (if any).
        for i in changed:
            self._reconnect_signals[i].set()
        async with self._ws_lock:
            for i in changed:
                ws = self._ws_conns[i]
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass

    async def _keepalive(self, ws) -> None:
        """App-level heartbeat. Polymarket idle-kills clients that never send;
        the protocol-level PING frames of the websockets lib do not count."""
        while True:
            await asyncio.sleep(self._keepalive_s)
            await ws.send("PING")

    async def _ws_consume(self, shard: int) -> None:
        url = self._ws_url
        backoff = 1.0
        log.info("ws_waiting_for_subscriptions", shard=shard)
        while not self._stop.is_set():
            if not self._shard_subs[shard]:
                # Nothing for this shard yet — wait for a subscription change.
                sig_task = asyncio.create_task(self._reconnect_signals[shard].wait())
                stop_task = asyncio.create_task(self._stop.wait())
                await asyncio.wait([sig_task, stop_task],
                                   return_when=asyncio.FIRST_COMPLETED)
                for t in (sig_task, stop_task):
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
                self._reconnect_signals[shard].clear()
                continue
            self._reconnect_signals[shard].clear()
            try:
                async with websockets.connect(url, ping_interval=30, ping_timeout=20) as ws:
                    async with self._ws_lock:
                        self._ws_conns[shard] = ws
                    subs = sorted(self._shard_subs[shard])
                    await ws.send(json.dumps({"type": "market", "assets_ids": subs}))
                    log.info("ws_subscribed", shard=shard, n_assets=len(subs))
                    backoff = 1.0
                    # Race: WS yields/dies, keepalive dies, reconnect signaled, or stop.
                    recv_task = asyncio.create_task(self._recv_loop(ws))
                    ka_task = asyncio.create_task(self._keepalive(ws))
                    reconnect_task = asyncio.create_task(
                        self._reconnect_signals[shard].wait())
                    stop_task = asyncio.create_task(self._stop.wait())
                    done, pending = await asyncio.wait(
                        [recv_task, ka_task, reconnect_task, stop_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass
                    # Retrieve completed-task exceptions: makes the death VISIBLE
                    # (the old single-conn path dropped them -> silent reconnect
                    # churn + asyncio 'Task exception was never retrieved' spam).
                    if recv_task in done:
                        exc = recv_task.exception()
                        log.warning("ws_recv_ended", shard=shard,
                                    err=str(exc) if exc else "clean")
                    if ka_task in done:
                        exc = ka_task.exception()
                        log.warning("ws_keepalive_ended", shard=shard,
                                    err=str(exc) if exc else "clean")
                    if reconnect_task in done and not self._stop.is_set():
                        log.info("ws_reconnect_requested", shard=shard)
                        # Drop out of the async-with to close + loop reconnects
                        continue
            except (ConnectionClosed, WebSocketException, asyncio.TimeoutError, OSError) as e:
                if self._stop.is_set():
                    break
                log.warning("ws_disconnect", shard=shard, err=str(e), backoff=backoff)
                async with self._ws_lock:
                    self._ws_conns[shard] = None
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
                payload = _loads(raw)
            except ValueError:  # covers json.JSONDecodeError + orjson.JSONDecodeError
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
        if kind in ("last_trade_price", "trade"):
            # Additive trade-print stream — never let it touch the book/decision
            # path. Best-effort: any failure is swallowed.
            self._handle_trade_event(msg)
            return
        book = self._books[token_id]
        if kind == "book":
            book.apply_book_snapshot(msg)
        elif kind == "price_change":
            book.apply_price_change(msg)
        # ignore other event types

    def _resolve_token(self, token_id: str):
        """Map a CLOB token id to (market_slug, symbol, outcome).

        outcome is "yes" / "no" / "" depending on which leg of the binary
        market the token is. Returns ("", "", "") if the token is not in any
        currently-active market.
        """
        for slug, m in self._discovery.active_markets.items():
            if token_id == m.yes_token_id:
                return slug, m.symbol, "yes"
            if token_id == m.no_token_id:
                return slug, m.symbol, "no"
        return "", "", ""

    def _handle_trade_event(self, msg: dict) -> None:
        """Capture an executed-trade print to the additive trades stream."""
        if self._trades_writer is None:
            return
        try:
            parsed = parse_trade_event(msg)
            token_id = parsed.get("asset_id") or ""
            slug, symbol, outcome = self._resolve_token(str(token_id))
            row = {
                "timestamp_ms": int(time.time() * 1000),
                "market_slug": slug,
                "symbol": symbol,
                "outcome": outcome,
                **parsed,
            }
            self._trades_writer.append(row)
            self._trades_seen += 1
        except Exception as e:
            log.warning("trade_write_failed", err=str(e))

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
                    trades_seen=self._trades_seen,
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
            # Dual-oracle: Chainlink distance-from-strike at this tick. The strike (cl_start) is
            # captured at window open in discovery._chainlink_start[slug]; cl_spot is the latest
            # Chainlink read at-or-before now. NaN when Chainlink is unavailable -> the engine's
            # determinism gate treats it as "missing" (fail-closed). Goes into the in-memory dict
            # only (the CSV writer ignores keys outside TICK_COLUMNS, so the schema is unchanged).
            #
            # Settlement-print model features (mode="psettle" twins, 2026-06-11), same research
            # conventions as research/dataset/chainlink_merge.py + oracle_print_model:
            #   cl_cb_basis_bps = (chainlink/coinbase - 1)*1e4 at the tick (no strike involved)
            #   cl_oracle_age_s = tick time (s) - round updated_at of the read visible now
            # Both NaN when unavailable; the psettle gate fails closed on NaN.
            cl_dist_bps = float("nan")
            cl_cb_basis_bps = float("nan")
            cl_oracle_age_s = float("nan")
            cl_asof = getattr(self._discovery, "_chainlink_price_asof", None)
            cl_read = getattr(self._discovery, "_chainlink_read_asof", None)
            cl_start = self._discovery._chainlink_start.get(slug) if hasattr(
                self._discovery, "_chainlink_start") else None
            cl_spot = None
            cl_upd = None
            if cl_read is not None:
                r = cl_read(m.symbol, ts_ms)
                if r is not None:
                    cl_spot, cl_upd = r
            elif cl_asof is not None:
                cl_spot = cl_asof(m.symbol, ts_ms)
            if cl_spot and cl_spot > 0:
                if cl_start and cl_start > 0:
                    cl_dist_bps = (cl_spot / cl_start - 1.0) * 1e4
                if coinbase_price > 0:
                    cl_cb_basis_bps = (cl_spot / coinbase_price - 1.0) * 1e4
                if cl_upd is not None and cl_upd > 0:
                    # research: clip(tick_s - updated_at, 0, None)
                    cl_oracle_age_s = max(0.0, float(second_ts) - float(cl_upd))
            # Cross-book (mode="xb") fields: the CO-TERMINAL 5m market's YES
            # top-of-book + captured strike, read at THIS second from the same
            # `_books` dict this pass reads the 15m books from — so the engine's
            # co5 view equals the 5m tick CSV row at the same second (the join
            # the XI4 research used). 15m rows only, last 300s of the window
            # (the 5m overlap). xb5_k5 stays NaN until discovery captures the 5m
            # strike (start_price>0) — entries before capture are impossible:
            # the CAUSAL constraint of xb_5m15m_causal_v1 (test_ledger § "XI4
            # AMENDMENT 2026-06-12"). All NaN fields fail closed in the engine.
            # Engine-only keys: the fixed-schema CSV writer ignores them.
            xb5_yes_bid = xb5_yes_ask = float("nan")
            xb5_yes_bid_sz = xb5_yes_ask_sz = xb5_k5 = float("nan")
            sec_iw = second_ts - m.window_start_ts
            if (m.window_end_ts - m.window_start_ts) == 900 and sec_iw >= 600:
                co5 = active.get(f"{m.symbol}-updown-5m-{m.window_start_ts + 600}")
                if (co5 is not None
                        and co5.window_start_ts == m.window_start_ts + 600
                        and co5.window_end_ts == m.window_end_ts):     # co-terminal
                    co5_yes = self._books.get(co5.yes_token_id)
                    co5_no = self._books.get(co5.no_token_id)
                    # Require BOTH books, mirroring this loop's own emit guard —
                    # so xb fields exist exactly when the co5 CSV row does.
                    if co5_yes is not None and co5_no is not None:
                        b5, a5, b5_sz, a5_sz = co5_yes.best_bid_ask()
                        xb5_yes_bid, xb5_yes_ask = b5, a5
                        xb5_yes_bid_sz, xb5_yes_ask_sz = b5_sz, a5_sz
                        if co5.start_price and co5.start_price > 0:
                            xb5_k5 = float(co5.start_price)
            # Mirror image for mode="xb5y" (Edge Hunt v3 g2bps-5y family): on the
            # CO-TERMINAL 5m market's rows, attach the parent 15m market's YES
            # top-of-book + strike, read at THIS second from the same `_books`
            # dict — so the engine's co15 view equals the 15m tick CSV row at the
            # same second (the xbook.py join convention). Only 5m windows whose
            # close aligns with a 15m close carry these; all NaN fields fail
            # closed in the engine. Engine-only keys (CSV writer ignores them).
            xb15_yes_bid = xb15_yes_ask = float("nan")
            xb15_yes_bid_sz = xb15_k15 = float("nan")
            # Co-terminality is verified RELATIVELY (parent starts 600s before us,
            # same close) — only the last 5m window of a 15m parent can match.
            if (m.window_end_ts - m.window_start_ts) == 300:
                co15 = active.get(f"{m.symbol}-updown-15m-{m.window_start_ts - 600}")
                if (co15 is not None
                        and co15.window_start_ts == m.window_start_ts - 600
                        and co15.window_end_ts == m.window_end_ts):   # co-terminal
                    co15_yes = self._books.get(co15.yes_token_id)
                    co15_no = self._books.get(co15.no_token_id)
                    if co15_yes is not None and co15_no is not None:
                        b15, a15, b15_sz, _ = co15_yes.best_bid_ask()
                        xb15_yes_bid, xb15_yes_ask = b15, a15
                        xb15_yes_bid_sz = b15_sz
                        if co15.start_price and co15.start_price > 0:
                            xb15_k15 = float(co15.start_price)
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
                "cl_dist_bps": cl_dist_bps,  # engine-only (ignored by the fixed-schema CSV writer)
                "cl_cb_basis_bps": cl_cb_basis_bps,  # engine-only (psettle model feature)
                "cl_oracle_age_s": cl_oracle_age_s,  # engine-only (psettle model feature)
                "xb5_yes_bid": xb5_yes_bid,  # engine-only (mode="xb" co-terminal 5m book)
                "xb5_yes_ask": xb5_yes_ask,  # engine-only (mode="xb")
                "xb5_yes_bid_sz": xb5_yes_bid_sz,  # engine-only (mode="xb")
                "xb5_yes_ask_sz": xb5_yes_ask_sz,  # engine-only (mode="xb")
                "xb5_k5": xb5_k5,  # engine-only (mode="xb"; NaN until 5m strike captured)
                "xb15_yes_bid": xb15_yes_bid,  # engine-only (mode="xb5y" co-terminal 15m book)
                "xb15_yes_ask": xb15_yes_ask,  # engine-only (mode="xb5y")
                "xb15_yes_bid_sz": xb15_yes_bid_sz,  # engine-only (mode="xb5y")
                "xb15_k15": xb15_k15,  # engine-only (mode="xb5y"; NaN until 15m strike captured)
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
            # Additive L2 snapshot — full-depth YES book, separate stream.
            # Best-effort: never let an L2 write failure disrupt the tick/decision path.
            if self._l2_writer is not None:
                try:
                    bids, asks = yes_book.top_levels(self._l2_writer.levels)
                    l2_row = book_to_l2_row(
                        timestamp_ms=ts_ms,
                        market_slug=slug,
                        symbol=m.symbol,
                        seconds_into_window=second_ts - m.window_start_ts,
                        bids=bids,
                        asks=asks,
                    )
                    self._l2_writer.append(l2_row)
                except Exception as e:
                    log.warning("l2_write_failed", slug=slug, err=str(e))
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
        tasks = [asyncio.create_task(self._ws_consume(i))
                 for i in range(self._n_shards)]
        tasks.append(asyncio.create_task(self._aggregator()))
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
