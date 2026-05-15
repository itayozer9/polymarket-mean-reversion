"""1Hz spot price quotes for BTC/ETH/SOL/XRP from Coinbase.

Writes into a shared in-memory `latest_spot` dict; consumers (ws_collector)
read it at row-build time. No disk I/O.
"""
from __future__ import annotations
import asyncio
import time
from typing import Dict, List, Optional

import aiohttp
import structlog

from mean_reversion_live.clients import coinbase

log = structlog.get_logger(__name__)


class SpotPriceCache:
    """Thread-safe-ish (asyncio) latest spot price per symbol."""

    def __init__(self, symbols: List[str]):
        self._prices: Dict[str, float] = {s: 0.0 for s in symbols}
        self._ts_ms: Dict[str, int] = {s: 0 for s in symbols}

    def get(self, symbol: str) -> Optional[float]:
        p = self._prices.get(symbol, 0.0)
        return p if p > 0 else None

    def set(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price
        self._ts_ms[symbol] = int(time.time() * 1000)

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
