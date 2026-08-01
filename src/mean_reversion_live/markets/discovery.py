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
# (market, coinbase_end, chainlink_start, chainlink_end) — chainlink_* are the
# Polymarket resolution basis (open vs close); None when the oracle feed is
# unavailable (caller falls back to the Coinbase end_price).
CloseCallback = Callable[
    [gamma.MarketInfo, Optional[float], Optional[float], Optional[float]], Awaitable[None]
]
# (symbol, t_ms) -> chainlink price at-or-before t_ms, or None. Supplied by the
# ChainlinkPriceCache so windows settle on the oracle Polymarket actually pays.
ChainlinkAsof = Callable[[str, int], Optional[float]]
# (symbol, t_ms) -> (price, updated_at_s|None) at-or-before t_ms, or None. Same row
# price_asof returns (ChainlinkPriceCache.read_asof) plus the round's on-chain
# updated_at — ws_collector derives the per-tick cl_oracle_age_s (settlement-print
# model feature) from it. Optional: None keeps the legacy price-only path.
ChainlinkReadAsof = Callable[[str, int], Optional[tuple]]
# (symbol, t_ms) -> Coinbase spot at-or-before t_ms, or None. Supplied by the
# SpotPriceCache so the window strike (SIGNAL basis) is frozen at the spot AT
# window open, not the live spot ~24s late at poll time (the look-ahead fix).
SpotAsof = Callable[[str, int], Optional[float]]


class MarketDiscovery:
    def __init__(
        self,
        on_subscribe: SubscribeCallback,
        on_close: CloseCallback,
        poll_interval_sec: int = 30,
        timeframes: tuple = ("15m", "5m"),
        chainlink_price_asof: Optional[ChainlinkAsof] = None,
        chainlink_read_asof: Optional[ChainlinkReadAsof] = None,
        spot_price_asof: Optional[SpotAsof] = None,
    ):
        self._on_subscribe = on_subscribe
        self._on_close = on_close
        self._poll_interval_sec = poll_interval_sec
        self._timeframes = timeframes
        self._chainlink_price_asof = chainlink_price_asof
        self._chainlink_read_asof = chainlink_read_asof
        self._spot_price_asof = spot_price_asof
        self._stop = asyncio.Event()
        self._active: Dict[str, gamma.MarketInfo] = {}      # slug -> info
        self._chainlink_start: Dict[str, float] = {}        # slug -> chainlink strike (open)
        self._known_subscriptions: set = set()              # asset_ids we've subscribed to
        self._closed_slugs: set = set()                     # already closed+settled this session
                                                            # (Gamma keeps returning ended windows;
                                                            # this stops re-close every poll)

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
        # Drop windows we've ALREADY closed+settled this session. Gamma keeps returning
        # recently-ended markets for a while; without this they get carried back into
        # `_active` (step 4 below) and re-"closed" every poll — a harmless idempotent
        # re-settle, but it spams `market_closed` (chainlink_start=None after the strike
        # was popped) and burns a Coinbase fetch per stale slug per poll.
        if self._closed_slugs:
            now_active = {s: m for s, m in now_active.items() if s not in self._closed_slugs}

        # 2) capture start_price (the resolution strike) for each market.
        #
        # The strike is the spot price at `window_start_ts` — when the window
        # actually opens. Discovery probes slugs up to k=+2 slots EARLY
        # (~30 min ahead for 15m) so the order book can be warmed up before
        # the window opens; capturing the strike at first sight would freeze
        # `start_price` at the spot ~30 min too early (Task 8c forensic bug).
        #
        # Gamma's market doc does NOT expose the numeric strike anywhere
        # (verified: no field, not in `description`/`question`/event). The
        # resolution oracle is Chainlink; Coinbase spot tracks it closely
        # (Task 4: 94.69% outcome agreement). So we sample `coinbase.get_spot()`
        # at/after `window_start_ts`, NOT on early discovery.
        for slug, m in now_active.items():
            m_known = self._active.get(slug)
            if m_known is not None and m_known.start_price > 0:
                # Strike already captured — preserve it. Never re-sample.
                m = gamma.MarketInfo(**{**m.__dict__, "start_price": m_known.start_price})
                now_active[slug] = m
                continue
            if m.start_price <= 0 and now >= m.window_start_ts:
                # Window is open: NOW is the correct time to freeze the strike.
                # Prefer the spot AS-OF window open (the SpotPriceCache history) so
                # the strike is the true open price, NOT the live spot at poll time
                # (median ~24s late on the 30s poll — a weak-look-ahead source for
                # any rule firing in the first ~30s; test_ledger "XI4 AMENDMENT").
                # Falls back to a live get_spot when the cache has no sample at open
                # (fresh boot / feed gap) — preserves the legacy behaviour.
                sp = None
                strike_basis = "spot_asof"
                if self._spot_price_asof is not None:
                    sp = self._spot_price_asof(m.symbol, m.window_start_ts * 1000)
                if sp is None or sp <= 0:
                    sp = await coinbase.get_spot(session, m.symbol)
                    strike_basis = "live_fallback"
                if sp is None or sp <= 0:
                    sp = 0.0
                # Capture the CHAINLINK strike (resolution-oracle basis) at the SAME
                # instant. start_price (Coinbase) stays the SIGNAL basis (book lags
                # Coinbase spot); chainlink_start is used only to settle the outcome
                # the way Polymarket does (chainlink open vs chainlink close).
                cl_start = None
                if self._chainlink_price_asof is not None:
                    # window_start_ts is SECONDS; the cache is keyed in MILLISECONDS.
                    cl_start = self._chainlink_price_asof(m.symbol, m.window_start_ts * 1000)
                    if cl_start and cl_start > 0:
                        self._chainlink_start[slug] = cl_start
                if sp > 0:
                    log.info(
                        "strike_captured",
                        slug=slug,
                        start_price=sp,
                        strike_basis=strike_basis,
                        chainlink_start=cl_start,
                        seconds_into_window=now - m.window_start_ts,
                    )
                m = gamma.MarketInfo(**{**m.__dict__, "start_price": sp})
                now_active[slug] = m
            # else: future window (k>0) — leave start_price=0.0 until it opens.

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
            # Resolution basis = CHAINLINK open vs close (what Polymarket pays). We
            # may detect the close up to one poll (~30 s) after window_end_ts, so we
            # query the oracle AT the boundary timestamp (not wall-clock now), exactly
            # as the offline re-settle does.
            cl_start = self._chainlink_start.pop(m.slug, None)
            cl_end = None
            if self._chainlink_price_asof is not None:
                # window_end_ts is SECONDS; the cache is keyed in MILLISECONDS.
                cl_end = self._chainlink_price_asof(m.symbol, m.window_end_ts * 1000)
            log.info("market_closed", slug=m.slug, end_price=end_price,
                     chainlink_start=cl_start, chainlink_end=cl_end)
            try:
                await self._on_close(m, end_price, cl_start, cl_end)
            except Exception as e:
                log.warning("on_close_error", slug=m.slug, err=str(e))
            # Mark settled so a re-discovered ended window is not re-closed next poll.
            self._closed_slugs.add(m.slug)

        # Prune closed-slug memory to ~last 2h (older windows won't be re-discovered),
        # so the set stays bounded over a long-running session.
        if len(self._closed_slugs) > 256:
            cutoff = now - 7200
            self._closed_slugs = {
                s for s in self._closed_slugs
                if (s.rsplit("-", 1)[-1].isdigit() and int(s.rsplit("-", 1)[-1]) > cutoff)
            }

        # 4) update active set — exclude anything closed this poll so it is never
        # carried forward and re-closed next poll (close fires exactly once per window).
        self._active = {s: m for s, m in now_active.items() if s not in self._closed_slugs}

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
