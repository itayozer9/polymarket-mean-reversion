"""Multi-strategy tick router.

Pulls TickEvents off the collector's queue, builds a numpy structured row
matching TICK_DTYPE, and routes to each enabled strategy's PerMarketState.
"""
from __future__ import annotations
import asyncio
import datetime as dt
from pathlib import Path
from typing import List, Optional

import numpy as np
import structlog

from mean_reversion_live.adapters.arb_imports import TICK_DTYPE
from mean_reversion_live.engine.market_context import MarketContext
from mean_reversion_live.engine.strategy import StrategyHandle

log = structlog.get_logger(__name__)


def _slug_to_timeframe(slug: str) -> str:
    parts = slug.split("-")
    if len(parts) >= 3:
        return parts[2]
    return "15m"


def _row_dict_to_struct(row: dict) -> np.ndarray:
    """Convert the dict produced by ws_collector to a TICK_DTYPE structured row."""
    arr = np.zeros(1, dtype=TICK_DTYPE)
    arr["timestamp_ms"][0] = int(row.get("timestamp_ms", 0))
    arr["seconds_into_window"][0] = int(row.get("seconds_into_window", 0))
    arr["yes_best_bid"][0] = float(row.get("yes_best_bid", 0.0))
    arr["yes_best_ask"][0] = float(row.get("yes_best_ask", 0.0))
    arr["yes_bid_depth"][0] = float(row.get("yes_bid_depth", 0.0))
    arr["yes_ask_depth"][0] = float(row.get("yes_ask_depth", 0.0))
    arr["no_best_bid"][0] = float(row.get("no_best_bid", 0.0))
    arr["no_best_ask"][0] = float(row.get("no_best_ask", 0.0))
    arr["no_bid_depth"][0] = float(row.get("no_bid_depth", 0.0))
    arr["no_ask_depth"][0] = float(row.get("no_ask_depth", 0.0))
    arr["start_price"][0] = float(row.get("start_price", 0.0))
    arr["move_pct"][0] = float(row.get("move_pct", 0.0))
    arr["yes_mid"][0] = float(row.get("yes_mid", 0.0))
    arr["no_mid"][0] = float(row.get("no_mid", 0.0))
    arr["spread_yes"][0] = float(row.get("spread_yes", 0.0))
    arr["spread_no"][0] = float(row.get("spread_no", 0.0))
    return arr[0]


class PaperEngine:
    def __init__(self, strategies: List[StrategyHandle], queue: asyncio.Queue, data_dir: Path):
        self.strategies = strategies
        self._queue = queue
        self._data_dir = data_dir
        self._stop = asyncio.Event()
        self._last_snapshot_hour = -1
        self.market_context = MarketContext()
        # Wire each strategy's signal observer to read from our shared MarketContext.
        for s in self.strategies:
            s.set_macro_snapshot(self.market_context.snapshot)

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("paper_engine_starting", strategies=[s.id for s in self.strategies])
        while not self._stop.is_set():
            try:
                row = await asyncio.wait_for(self._queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                self._maybe_snapshot()
                continue
            await self._on_tick(row)
            self._maybe_snapshot()
        log.info("paper_engine_stopping")
        # Final flush
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        for s in self.strategies:
            s.snapshot(now_iso)
            s.persist_portfolio()
            s.close()
        log.info("paper_engine_stopped")

    async def _on_tick(self, row: dict) -> None:
        slug = row.get("market_slug", "")
        timeframe = _slug_to_timeframe(slug)
        window_duration = 300 if timeframe == "5m" else 900
        arr_row = _row_dict_to_struct(row)
        # Feed the cross-symbol macro context (cheap, additive).
        symbol = str(row.get("symbol") or "").lower()
        if symbol:
            self.market_context.update(
                symbol=symbol,
                yes_mid=float(row.get("yes_mid") or 0.0),
                no_mid=float(row.get("no_mid") or 0.0),
                ts_ms=int(row.get("timestamp_ms") or 0),
                spot_price=float(row.get("coinbase_price") or 0.0),
            )
        outcome = None  # outcomes are resolved by the collector via outcomes.csv; we don't pass it through here
        for s in self.strategies:
            if not s.applies_to_tick(slug, timeframe):
                continue
            pms = s.get_or_create_state(slug, window_duration)
            trade = pms.on_tick(arr_row, s.portfolio, s.rng, outcome=outcome)
            if trade is not None:
                log.info(
                    "trade_closed",
                    strategy=s.id,
                    slug=trade.slug,
                    side=trade.side,
                    pnl=round(trade.pnl, 3),
                    reason=trade.exit_reason,
                )
                s.record_trade(trade)
                s.persist_portfolio()

    def _maybe_snapshot(self) -> None:
        """Once an hour, write a portfolio snapshot for each strategy."""
        now = dt.datetime.now(dt.timezone.utc)
        if now.hour != self._last_snapshot_hour:
            iso = now.isoformat()
            for s in self.strategies:
                s.snapshot(iso)
                s.persist_portfolio()
            self._last_snapshot_hour = now.hour
            log.info("hourly_snapshot_written", hour=now.hour)
