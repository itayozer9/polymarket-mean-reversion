"""Multi-strategy tick router.

Pulls TickEvents off the collector's queue, builds a numpy structured row
matching TICK_DTYPE, and routes to each enabled strategy's PerMarketState.
"""
from __future__ import annotations
import asyncio
import time
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


# Extend the shared TICK_DTYPE locally (do NOT modify polymarket-arb's definition) with the
# dual-oracle field + the settlement-print-model features (mode="psettle" twins,
# 2026-06-11) + the co-terminal 5m cross-book fields (mode="xb" twin, 2026-06-12).
# NaN-filled by default so the determinism gate / psettle gate / xb gate treat them as
# "unavailable" (fail-closed) unless ws_collector / a backtest explicitly
# populates them. Additive fields only — existing strategies read named fields and are
# unaffected.
_TICK_DTYPE_EXT = np.dtype(TICK_DTYPE.descr + [
    ("cl_dist_bps", "f8"),
    ("cl_cb_basis_bps", "f8"),
    ("cl_oracle_age_s", "f8"),
    # mode="xb": co-terminal 5m market's YES top-of-book + captured strike, at
    # THIS second (15m rows only, last 300s; NaN otherwise / pre-strike-capture).
    ("xb5_yes_bid", "f8"),
    ("xb5_yes_ask", "f8"),
    ("xb5_yes_bid_sz", "f8"),
    ("xb5_yes_ask_sz", "f8"),
    ("xb5_k5", "f8"),
    # mode="xb5y": parent 15m market's YES top-of-book + strike, at THIS second
    # (co-terminal 5m rows only; NaN otherwise / pre-strike-capture).
    ("xb15_yes_bid", "f8"),
    ("xb15_yes_ask", "f8"),
    ("xb15_yes_bid_sz", "f8"),
    ("xb15_k15", "f8"),
])

_XB_FIELDS = ("xb5_yes_bid", "xb5_yes_ask", "xb5_yes_bid_sz", "xb5_yes_ask_sz",
              "xb5_k5", "xb15_yes_bid", "xb15_yes_ask", "xb15_yes_bid_sz",
              "xb15_k15")


def _row_dict_to_struct(row: dict) -> np.ndarray:
    """Convert the dict produced by ws_collector to a (cl-extended) TICK_DTYPE structured row."""
    arr = np.zeros(1, dtype=_TICK_DTYPE_EXT)
    arr["cl_dist_bps"][0] = float(row.get("cl_dist_bps", float("nan")))
    arr["cl_cb_basis_bps"][0] = float(row.get("cl_cb_basis_bps", float("nan")))
    arr["cl_oracle_age_s"][0] = float(row.get("cl_oracle_age_s", float("nan")))
    for f in _XB_FIELDS:
        arr[f][0] = float(row.get(f, float("nan")))
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
        # Debounced portfolio persistence. Per-trade persist_portfolio()
        # (atomic_write_json: tmp-write + os.replace) ran on the event loop for
        # EVERY close; at 15m settlement boundaries up to ~360 closes pile into
        # <2s, ~1s of blocking I/O that backs the bounded tick queue up to its
        # 1000 cap and drops rows (the saturation seen 2026-06-06). We mark
        # strategies dirty on close and flush at most once/sec/strategy instead.
        # Trade durability is unaffected (jsonl trade_log fsyncs per close).
        self._dirty_strategies: set = set()
        self._last_flush_mono = 0.0
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
                self._flush_dirty()
                continue
            await self._on_tick(row)
            self._maybe_snapshot()
            self._flush_dirty()
        log.info("paper_engine_stopping")
        self._flush_dirty(force=True)
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
                self._dirty_strategies.add(s)

    def settle_window(self, market, end_price, chainlink_start=None, chainlink_end=None) -> None:
        """Settle any open DETERMINISM positions for a closed window at the TRUE
        outcome. Called from the runner's on_close. Mean-reversion strategies are
        unaffected (they settle on-tick).

        Polymarket resolves crypto Up/Down markets on **Chainlink** (open vs close),
        NOT on Coinbase. When the Chainlink boundary prices are available we settle on
        them (tie -> Up). Coinbase (`end_price` vs `start_price`) is a FALLBACK only,
        used when the oracle feed had a gap — and logged as such — so the paper bot's
        WR/PnL tracks what Polymarket actually pays. `start_price` (Coinbase) remains
        the SIGNAL basis and is untouched here. See memory
        [[backtest-settles-coinbase-not-chainlink]].
        """
        start_price = float(getattr(market, "start_price", 0.0) or 0.0)
        slug = getattr(market, "slug", None)
        if slug is None:
            return

        cl_ok = (chainlink_start is not None and chainlink_end is not None
                 and float(chainlink_start) > 0 and float(chainlink_end) > 0)
        if cl_ok:
            outcome_up = float(chainlink_end) >= float(chainlink_start)   # tie -> Up
            basis = "chainlink"
        elif end_price is not None and start_price > 0:
            outcome_up = float(end_price) >= start_price
            basis = "coinbase_fallback"
            log.warning("settle_fallback_coinbase", slug=slug,
                        reason="chainlink_unavailable",
                        chainlink_start=chainlink_start, chainlink_end=chainlink_end)
        else:
            log.warning("settle_skipped_no_basis", slug=slug,
                        end_price=end_price, start_price=start_price,
                        chainlink_start=chainlink_start, chainlink_end=chainlink_end)
            return

        # Audit trail: did the (legacy) Coinbase outcome disagree with the Chainlink
        # one Polymarket pays? Recorded per-trade in last_ctx below.
        cb_outcome_up = (end_price is not None and start_price > 0
                         and float(end_price) >= start_price)
        cl_ctx = {
            "settle_basis": basis,
            "chainlink_start": float(chainlink_start) if chainlink_start else None,
            "chainlink_end": float(chainlink_end) if chainlink_end else None,
            "coinbase_start": start_price or None,
            "coinbase_end": float(end_price) if end_price is not None else None,
            "outcome_up_chainlink": int(bool(outcome_up)),
            "outcome_up_coinbase": int(bool(cb_outcome_up)) if end_price is not None else None,
            "oracle_disagree": (int(bool(outcome_up) != bool(cb_outcome_up))
                                if (cl_ok and end_price is not None and start_price > 0) else None),
        }

        ts = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
        for s in self.strategies:
            st = s.states.get(slug)
            # hold-to-resolution strategies expose settle(); mean-reversion does not
            if st is None or not hasattr(st, "settle"):
                continue
            try:
                trade = st.settle(outcome_up, ts, s.portfolio)
            except Exception as e:
                log.warning("det_settle_error", strategy=s.id, slug=slug, err=str(e))
                continue
            if trade is not None:
                log.info("det_settled", strategy=s.id, slug=slug, basis=basis,
                         side=trade.side, pnl=round(trade.pnl, 3), won=(trade.exit_price > 0),
                         oracle_disagree=cl_ctx["oracle_disagree"])
                s.record_trade(trade)
                # Enrich the detailed log with the resolution basis (replay-parity safe:
                # settle() already populated last_ctx; we only annotate the record).
                ctx = dict(getattr(st, "last_ctx", None) or {})
                ctx.update(cl_ctx)
                s.record_trade_detailed(trade, ctx)
                s.persist_portfolio()

    def _flush_dirty(self, force: bool = False) -> None:
        """Persist portfolio JSONs for strategies that just closed a trade,
        throttled to <=1 write/sec/strategy.

        Replaces the per-close persist_portfolio() that was the consumer's
        hot-path bottleneck (see __init__). Keeps the portfolio JSON fresh within
        ~1s so the hourly status check stays accurate, while removing the
        settlement-burst I/O storm. Trade durability is unaffected — record_trade
        already fsyncs to the jsonl trade_log per close."""
        if not self._dirty_strategies:
            return
        now_mono = time.monotonic()
        if not force and (now_mono - self._last_flush_mono) < 1.0:
            return
        for s in self._dirty_strategies:
            try:
                s.persist_portfolio()
            except Exception as e:  # pragma: no cover
                log.warning("persist_portfolio_failed", strategy=s.id, err=str(e))
        self._dirty_strategies.clear()
        self._last_flush_mono = now_mono

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
