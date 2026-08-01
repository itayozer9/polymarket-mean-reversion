"""1Hz spot price quotes for BTC/ETH/SOL/XRP from Coinbase.

Writes into a shared in-memory `latest_spot` dict; consumers (ws_collector)
read it at row-build time. No disk I/O.
"""
from __future__ import annotations
import asyncio
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Tuple

import aiohttp
import structlog

from mean_reversion_live.clients import coinbase

log = structlog.get_logger(__name__)


class SpotPriceCache:
    """Thread-safe-ish (asyncio + collector thread) latest spot price per symbol,
    plus a short rolling history for as-of lookups.

    The history backs `price_asof` — used to freeze the window strike at the spot
    AT window open (window_start_ts), instead of the live spot at discovery-poll
    time (~24s late). That late capture was a weak-look-ahead source for any rule
    firing in the first ~30s of a window (test_ledger "XI4 AMENDMENT"). It mirrors
    the Chainlink settlement basis, which already captures as-of window_start_ts.

    `history_max` bounds memory over a 7-day run: at ≤4 writes/s (spot_loop + WS
    collector) 1200 entries ≈ 5 min — far more than the ≤30s strike-capture lag.
    Concurrency model is the module's existing one: GIL-atomic dict writes +
    deque.append (thread-safe in CPython); reads snapshot via list().
    """

    def __init__(self, symbols: List[str], history_max: int = 1200):
        self._prices: Dict[str, float] = {s: 0.0 for s in symbols}
        self._ts_ms: Dict[str, int] = {s: 0 for s in symbols}
        self._history_max = history_max
        self._hist: Dict[str, Deque[Tuple[int, float]]] = defaultdict(
            lambda: deque(maxlen=history_max))

    def get(self, symbol: str) -> Optional[float]:
        p = self._prices.get(symbol, 0.0)
        return p if p > 0 else None

    def set(self, symbol: str, price: float, ts_ms: Optional[int] = None) -> None:
        t = int(ts_ms) if ts_ms is not None else int(time.time() * 1000)
        self._prices[symbol] = price
        self._ts_ms[symbol] = t
        self._hist[symbol].append((t, price))

    def price_asof(self, symbol: str, t_ms: int) -> Optional[float]:
        """Spot at-or-before t_ms (the latest such sample), or None if no sample
        was recorded at/before t_ms. Never returns a value observed AFTER t_ms —
        that's the anti-look-ahead guarantee. Robust to mildly out-of-order writes."""
        hist = self._hist.get(symbol)
        if not hist:
            return None
        best_ts, best_px = -1, None
        for ts, px in list(hist):     # snapshot: safe under concurrent append
            if ts <= t_ms and ts > best_ts:
                best_ts, best_px = ts, px
        return best_px

    def age_ms(self, symbol: str) -> int:
        ts = self._ts_ms.get(symbol, 0)
        if ts == 0:
            return -1
        return int(time.time() * 1000) - ts


async def spot_loop(cache: SpotPriceCache, symbols: List[str], stop_event: asyncio.Event) -> None:
    """Poll Coinbase once per second per symbol, fill cache."""
    async with aiohttp.ClientSession() as session:
        while not stop_event.is_set():
            t0 = time.time()
            # Stagger the 4 requests slightly so we don't hammer at the same instant.
            for s in symbols:
                if stop_event.is_set():
                    break
                try:
                    p = await coinbase.get_spot(session, s)
                    if p is not None and p > 0:
                        cache.set(s, p)
                except Exception as e:
                    log.warning("spot_fetch_failed", symbol=s, err=str(e))
                await asyncio.sleep(0.1)  # 100ms between symbols
            elapsed = time.time() - t0
            sleep_left = max(0.0, 1.0 - elapsed)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_left)
            except asyncio.TimeoutError:
                pass
