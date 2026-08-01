"""Per-strategy state: SimConfig, Portfolio, file paths, PerMarketState cache."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np
import structlog

from mean_reversion_live.adapters.arb_imports import Portfolio, SimConfig, Trade
from mean_reversion_live.engine.per_market_state import PerMarketState
from mean_reversion_live.engine.determinism_state import DetParams, DeterminismState, DailyLossGuard
from mean_reversion_live.engine.stale_quote_state import StaleQuoteParams, StaleQuoteState
from mean_reversion_live.engine.persistence import JsonlAppender, atomic_write_json

log = structlog.get_logger(__name__)


# Decisions we always want in signals.jsonl, regardless of throttle.
_ALWAYS_LOG_DECISIONS = frozenset({
    "armed_new",
    "armed_waiting",
    "fired",
    "rejected_fill",
    "skipped_no_fill",
    "trade_closed_profit_target",
    "trade_closed_stop_loss",
    "trade_closed_trailing_stop",
    "trade_closed_max_hold",
    "trade_closed_forced_resolution",
})

# Chatty decisions we throttle to 1/sec/slug.
_THROTTLED_DECISIONS = frozenset({
    "flat",
    "near_miss",
    "holding",
    "skipped_already_traded",
    "skipped_can_enter",
    "skipped_skip_prob",
})


@dataclass
class StrategyHandle:
    id: str
    name: str
    cfg: SimConfig
    timeframe: str
    starting_capital_usd: float
    data_dir: Path
    # When set, this strategy uses a custom state class instead of the
    # mean-reversion PerMarketState. Additive: defaults None => unchanged path.
    det_params: Optional[DetParams] = None
    sq_params: Optional[StaleQuoteParams] = None
    # When True, this strategy ALSO emits immediate-flush entry intents to
    # data/live/intents.jsonl for the standalone live executor (real orders).
    # Additive: paper accounting is unchanged; the live executor is a separate
    # process. Default False => no behaviour change.
    live: bool = False
    portfolio: Portfolio = None  # type: ignore
    rng: np.random.Generator = None  # type: ignore
    states: Dict[str, PerMarketState] = field(default_factory=dict)
    trade_log: Optional[JsonlAppender] = None
    signal_log: Optional[JsonlAppender] = None
    snapshot_log: Optional[JsonlAppender] = None
    trade_detail_log: Optional[JsonlAppender] = None
    live_intent_log: Optional[JsonlAppender] = None
    # Macro-snapshot provider, injected by the paper engine. Returns a dict.
    _macro_snapshot: Optional[Callable[[int], dict]] = None
    # Per-slug throttle: ts of last logged event for each slug (in seconds).
    _last_logged_sec_per_slug: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self.portfolio = Portfolio(human=self.cfg.human, bankroll=self.starting_capital_usd)
        self.rng = np.random.default_rng(int(self.cfg.hash_id(), 16) % (2 ** 32))
        # One daily-loss guard shared across all per-window states of a det /
        # stale-quote strategy (None for mean-reversion strategies).
        _cap = None
        _mode = "soft_settled"
        if self.det_params is not None:
            _cap = self.det_params.max_daily_loss_usd
            _mode = getattr(self.det_params, "daily_loss_mode", "soft_settled")
        elif self.sq_params is not None:
            _cap = self.sq_params.max_daily_loss_usd
            _mode = getattr(self.sq_params, "daily_loss_mode", "soft_settled")
        self._det_guard = (DailyLossGuard(_cap, mode=_mode)
                           if (self.det_params or self.sq_params) is not None else None)
        self._setup_io()
        self._replay_existing_trades()

    def _replay_existing_trades(self) -> None:
        """Rebuild portfolio aggregates from trades.jsonl on startup.

        Restart-safe persistence: the engine appends every closed trade to
        data/jsonl/<sid>/trades.jsonl. On startup we replay those rows so
        n_trades, win_rate, total_pnl, total_fees, today's count, and any
        post-loss cooldown are carried forward across stop/start cycles.
        """
        trades_path = self.data_dir / "jsonl" / self.id / "trades.jsonl"
        if not trades_path.exists():
            return
        n = 0
        try:
            with open(trades_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    trade = Trade(
                        slug=rec["slug"],
                        side=rec["side"],
                        entry_ts_ms=int(rec["entry_ts_ms"]),
                        exit_ts_ms=int(rec["exit_ts_ms"]),
                        entry_price=float(rec["entry_price"]),
                        exit_price=float(rec["exit_price"]),
                        shares=float(rec["shares"]),
                        bet_usd=float(rec["bet_usd"]),
                        fee_total=float(rec["fee_total"]),
                        pnl=float(rec["pnl"]),
                        exit_reason=rec["exit_reason"],
                        seconds_held=int(rec["seconds_held"]),
                    )
                    # on_entry → on_exit pair recreates portfolio aggregates
                    # (trades_today bucketed by UTC date; open_positions nets to 0).
                    self.portfolio.on_entry(trade.entry_ts_ms)
                    self.portfolio.on_exit(trade)
                    if self._det_guard is not None:
                        # hard modes rebuild today's settled PnL across restarts;
                        # soft mode no-ops (preserves legacy amnesia). Keyed by the
                        # settle ts so it lands in the correct UTC-day bucket.
                        self._det_guard.replay_settled(trade.exit_ts_ms, trade.pnl)
                    n += 1
        except Exception as e:
            log.warning("trade_replay_failed", strategy=self.id, err=str(e), n_replayed=n)
            return
        if n > 0:
            log.info(
                "trade_replay_completed",
                strategy=self.id,
                n_replayed=n,
                total_pnl=round(self.portfolio.total_pnl, 4),
                wins=self.portfolio.wins,
                win_rate=round(self.portfolio.win_rate, 4),
            )

    def _setup_io(self):
        sd = self.data_dir / "jsonl" / self.id
        sd.mkdir(parents=True, exist_ok=True)
        self.trade_log = JsonlAppender(sd / "trades.jsonl")
        # Signals are diagnostic (hundreds per second across 8 strategies × N
        # markets). Use block buffering + larger fsync window so each write
        # doesn't trigger an OS flush; the engine consumer was queue-saturating
        # because line-buffered writes turned this into ~200 syscalls/sec.
        self.signal_log = JsonlAppender(sd / "signals.jsonl", fsync_every_n=500, line_buffered=False)
        self.snapshot_log = JsonlAppender(sd / "portfolio_snapshots.jsonl")
        # Rich per-trade context (det/stale-quote strategies) for post-hoc filter
        # discovery. Line-buffered + small fsync window: trades are infrequent and
        # we want each one durable for the weekly review.
        self.trade_detail_log = (
            JsonlAppender(sd / "trades_detailed.jsonl", fsync_every_n=1)
            if (self.det_params is not None or self.sq_params is not None) else None)
        # Live executor bridge: immediate-flush (fsync every record) so the
        # standalone order placer sees a "fired" intent with minimal latency.
        if self.live:
            live_dir = self.data_dir / "live"
            live_dir.mkdir(parents=True, exist_ok=True)
            self.live_intent_log = JsonlAppender(live_dir / "intents.jsonl", fsync_every_n=1)

    @property
    def portfolio_path(self) -> Path:
        return self.data_dir / "portfolios" / f"{self.id}.json"

    def set_macro_snapshot(self, fn: Callable[[int], dict]) -> None:
        self._macro_snapshot = fn

    def applies_to_tick(self, market_slug: str, market_timeframe: str) -> bool:
        return market_timeframe == self.timeframe

    def get_or_create_state(self, slug: str, window_duration: int):
        pms = self.states.get(slug)
        if pms is None:
            if self.det_params is not None:
                pms = DeterminismState(
                    slug=slug,
                    params=self.det_params,
                    window_duration_sec=window_duration,
                    observer=self._observer,
                    guard=self._det_guard,
                )
            elif self.sq_params is not None:
                pms = StaleQuoteState(
                    slug=slug,
                    params=self.sq_params,
                    window_duration_sec=window_duration,
                    observer=self._observer,
                    guard=self._det_guard,
                )
            else:
                pms = PerMarketState(
                    slug=slug,
                    cfg=self.cfg,
                    window_duration_sec=window_duration,
                    observer=self._observer,
                )
            self.states[slug] = pms
        return pms

    def _observer(self, event: dict) -> None:
        """Receive a per-tick decision event from PerMarketState and (maybe) log it.

        Throttling: chatty states (flat/near_miss/holding/skipped_*) are logged
        at most once per second per slug. Important transitions (armed_new,
        fired, rejected_fill, trade_closed_*) are ALWAYS logged.
        """
        decision = event.get("decision", "unknown")
        slug = event.get("slug", "")
        ts_ms = event.get("ts_ms", 0)
        sec = ts_ms // 1000

        if decision in _THROTTLED_DECISIONS:
            last_sec = self._last_logged_sec_per_slug.get(slug, -1)
            if last_sec == sec:
                return
            # Also drop "flat" entirely — it's the dominant decision and would
            # explode the log file. We keep "near_miss" (which is interesting),
            # "holding" (rare-ish), and "skipped_*" (rare).
            if decision == "flat":
                self._last_logged_sec_per_slug[slug] = sec
                return

        # Live executor bridge: emit an immediate-flush ENTRY INTENT on "fired"
        # (fired is never throttled) so the standalone order placer can act fast.
        if self.live and decision == "fired" and self.live_intent_log is not None:
            feat = event.get("features") or {}
            # Per-strategy live caps travel WITH the intent so the standalone executor
            # isolates each strategy's bankroll + daily-loss cap (strategies.yaml is the
            # single source of truth). bankroll = the strategy's starting capital; the
            # daily cap comes from whichever params object this strategy uses.
            _params = self.det_params if self.det_params is not None else self.sq_params
            _daily_cap = getattr(_params, "max_daily_loss_usd", None) if _params is not None else None
            try:
                self.live_intent_log.append({
                    "ts_ms": ts_ms, "strategy_id": self.id, "slug": slug,
                    "symbol": slug.split("-", 1)[0],
                    "side": event.get("side_signal"),
                    "entry_ask": feat.get("fav_ask"),
                    "time_left": feat.get("time_left"),
                    "bet_usd": float(self.cfg.human.fixed_bet_usd),
                    "bankroll_usd": float(self.starting_capital_usd),
                    "max_daily_loss_usd": float(_daily_cap) if _daily_cap is not None else 25.0,
                    # The EFFECTIVE price ceiling for THIS entry — the executor's laddered fill chases
                    # only within [entry_ask, max_ask] and never overpays above it. With a dynamic
                    # ceiling the engine raises it on deep Chainlink locks, so prefer the per-trade
                    # eff_max_ask from the signal; fall back to the static param.
                    "max_ask": float(feat.get("eff_max_ask",
                                     getattr(_params, "max_ask", 0.85) if _params is not None else 0.85)),
                })
            except Exception as e:  # pragma: no cover
                log.warning("live_intent_append_failed", err=str(e), strategy=self.id)

        macro = {}
        if self._macro_snapshot is not None:
            try:
                macro = self._macro_snapshot(ts_ms)
            except Exception:
                macro = {}

        record = {
            "ts_ms": ts_ms,
            "strategy_id": self.id,
            "slug": slug,
            "decision": decision,
            "side_signal": event.get("side_signal"),
            "near_miss": event.get("near_miss"),
            "state_before": event.get("state_before"),
            "state_after": event.get("state_after"),
            "trade_closed": event.get("trade_closed", False),
            "features": event.get("features"),
            "macro": macro,
        }
        try:
            self.signal_log.append(record)
            self._last_logged_sec_per_slug[slug] = sec
        except Exception as e:  # pragma: no cover
            log.warning("signal_log_append_failed", err=str(e), strategy=self.id)

    def record_trade(self, trade: Trade) -> None:
        self.trade_log.append({
            "strategy_id": self.id,
            "slug": trade.slug,
            "side": trade.side,
            "entry_ts_ms": trade.entry_ts_ms,
            "exit_ts_ms": trade.exit_ts_ms,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "shares": trade.shares,
            "bet_usd": trade.bet_usd,
            "fee_total": trade.fee_total,
            "pnl": trade.pnl,
            "exit_reason": trade.exit_reason,
            "seconds_held": trade.seconds_held,
        })

    def record_trade_detailed(self, trade: Trade, ctx: dict) -> None:
        """Write a single analysis-ready row: trade outcome + full entry context
        (hour, symbol, distance, price, spread, depth, vol, velocity). Used by the
        weekly review to find condition/time filters that lift WR + profit."""
        if self.trade_detail_log is None or not ctx:
            return
        rec = {
            "strategy_id": self.id, "slug": trade.slug, "side": trade.side,
            "entry_ts_ms": trade.entry_ts_ms, "exit_ts_ms": trade.exit_ts_ms,
            "entry_price": trade.entry_price, "exit_price": trade.exit_price,
            "shares": trade.shares, "fee_total": trade.fee_total,
            "pnl": trade.pnl, "seconds_held": trade.seconds_held,
            **ctx,
        }
        try:
            self.trade_detail_log.append(rec)
        except Exception as e:  # pragma: no cover
            log.warning("trade_detail_append_failed", err=str(e), strategy=self.id)

    def persist_portfolio(self) -> None:
        atomic_write_json(self.portfolio_path, {
            "strategy_id": self.id,
            "name": self.name,
            "timeframe": self.timeframe,
            "starting_capital_usd": self.starting_capital_usd,
            "n_trades": self.portfolio.n_trades,
            "wins": self.portfolio.wins,
            "win_rate": self.portfolio.win_rate,
            "total_pnl": self.portfolio.total_pnl,
            "total_fees": self.portfolio.total_fees,
            "open_positions": self.portfolio.open_positions,
            "trades_today": self.portfolio.trades_today,
            "cooldown_until_ms": self.portfolio.cooldown_until_ms,
            "active_markets": len(self.states),
        })

    def snapshot(self, ts_iso: str) -> None:
        self.snapshot_log.append({
            "ts_iso": ts_iso,
            "strategy_id": self.id,
            "n_trades": self.portfolio.n_trades,
            "wins": self.portfolio.wins,
            "total_pnl": self.portfolio.total_pnl,
            "total_fees": self.portfolio.total_fees,
            "open_positions": self.portfolio.open_positions,
            "trades_today": self.portfolio.trades_today,
            "active_markets": len(self.states),
        })

    def close(self) -> None:
        for log_ in (self.trade_log, self.signal_log, self.snapshot_log,
                     self.trade_detail_log, self.live_intent_log):
            if log_ is not None:
                log_.close()
