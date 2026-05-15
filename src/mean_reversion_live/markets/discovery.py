"""MarketDiscovery: poll Gamma for active 15m/5m markets, track open/close events.

Emits callbacks:
    on_subscribe(asset_ids: list[str])  — when the active asset_id set changes
    on_close(slug: str, end_price: float | None)  — when a window finishes

The 15m windows are aligned to UTC boundaries. We refresh every 30s.
"""
from __future__ import annotations
import asyncio
import time
from typing import Awaitable, Callable, Dict, List, Optional

import aiohttp
import structlog

from mean_reversion_live.clients import clob_rest, coinbase, gamma
from mean_reversion_live.config import get_settings
from mean_reversion_live.markets import windows

log = structlog.get_logger(__name__)


SubscribeCallback = Callable[[List[str]], Awaitable[None]]
CloseCallback = Callable[[gamma.MarketInfo, Optional[float]], Awaitable[None]]


class MarketDiscovery:
    def __init__(
        self,
        on_subscribe: SubscribeCallback,
        on_close: CloseCallback,
        poll_interval_sec: int = 30,
        timeframes: tuple = ("15m", "5m"),
    ):
        self._on_subscribe = on_subscribe
        self._on_close = on_close
        self._poll_interval_sec = poll_interval_sec
        self._timeframes = timeframes
        self._stop = asyncio.Event()
        self._active: Dict[str, gamma.MarketInfo] = {}      # slug -> info
        self._known_subscriptions: set = set()              # asset_ids we've subscribed to

    def stop(self) -> None:
        self._stop.set()

    @property
    def active_markets(self) -> Dict[str, gamma.MarketInfo]:
        return dict(self._active)

    def get_market(self, slug: str) -> Optional[gamma.MarketInfo]:
        return self._active.get(slug)

    async def run(self) -> None:
        settings = get_settings()
        symbols = settings.symbol_list
        async with aiohttp.ClientSession() as session:
            while not self._stop.is_set():
                try:
                    await self._tick(session, symbols)
                except Exception as e:
                    log.warning("discovery_tick_error", err=str(e))
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_sec)
                except asyncio.TimeoutError:
                    pass

    async def _tick(self, session: aiohttp.ClientSession, symbols: List[str]) -> None:
        now = windows.now_ts()
        # 1) discover fresh markets
        markets = await gamma.list_active_markets(session, symbols, timeframes=self._timeframes)
        now_active: Dict[str, gamma.MarketInfo] = {m.slug: m for m in markets}

        # 2) backfill start_price for markets we don't have one for yet
        for slug, m in now_active.items():
            if self._active.get(slug) is not None:
                # already known — preserve previous start_price
                m_known = self._active[slug]
                if m_known.start_price > 0:
                    m = gamma.MarketInfo(**{**m.__dict__, "start_price": m_known.start_price})
                    now_active[slug] = m
            if m.start_price <= 0:
                # Try Coinbase first (very cheap)
                sp = await coinbase.get_spot(session, m.symbol)
                if sp is None or sp <= 0:
                    sp = 0.0
                m = gamma.MarketInfo(**{**m.__dict__, "start_price": sp})
                now_active[slug] = m

        # 3) detect closes (slugs that disappeared OR whose window_end_ts < now)
        closed = []
        for slug, m in list(self._active.items()):
            if slug not in now_active or m.window_end_ts <= now:
                closed.append(m)
        for m in closed:
            self._active.pop(m.slug, None)
            try:
                end_price = await coinbase.get_spot(session, m.symbol)
            except Exception:
                end_price = None
            log.info("market_closed", slug=m.slug, end_price=end_price)
            try:
                await self._on_close(m, end_price)
            except Exception as e:
                log.warning("on_close_error", slug=m.slug, err=str(e))

        # 4) update active set
        self._active = now_active

        # 5) compute new asset_id set; if changed, callback
        new_subs = set()
        for m in self._active.values():
            new_subs.update(m.asset_ids)
        if new_subs != self._known_subscriptions:
            log.info(
                "subscription_change",
                added=len(new_subs - self._known_subscriptions),
                removed=len(self._known_subscriptions - new_subs),
                total=len(new_subs),
            )
            self._known_subscriptions = new_subs
            try:
                await self._on_subscribe(sorted(new_subs))
            except Exception as e:
                log.warning("on_subscribe_error", err=str(e))
