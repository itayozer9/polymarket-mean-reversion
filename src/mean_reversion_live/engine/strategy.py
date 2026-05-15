"""Per-strategy state: SimConfig, Portfolio, file paths, PerMarketState cache."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import structlog

from mean_reversion_live.adapters.arb_imports import Portfolio, SimConfig, Trade
from mean_reversion_live.engine.per_market_state import PerMarketState
from mean_reversion_live.engine.persistence import JsonlAppender, atomic_write_json

log = structlog.get_logger(__name__)


@dataclass
class StrategyHandle:
    id: str
    name: str
    cfg: SimConfig
    timeframe: str
    starting_capital_usd: float
    data_dir: Path
    portfolio: Portfolio = None  # type: ignore
    rng: np.random.Generator = None  # type: ignore
    states: Dict[str, PerMarketState] = field(default_factory=dict)
    trade_log: Optional[JsonlAppender] = None
    signal_log: Optional[JsonlAppender] = None
    snapshot_log: Optional[JsonlAppender] = None

    def __post_init__(self):
        self.portfolio = Portfolio(human=self.cfg.human, bankroll=self.starting_capital_usd)
        self.rng = np.random.default_rng(int(self.cfg.hash_id(), 16) % (2 ** 32))
        self._setup_io()

    def _setup_io(self):
        sd = self.data_dir / "jsonl" / self.id
        sd.mkdir(parents=True, exist_ok=True)
        self.trade_log = JsonlAppender(sd / "trades.jsonl")
        self.signal_log = JsonlAppender(sd / "signals.jsonl")
        self.snapshot_log = JsonlAppender(sd / "portfolio_snapshots.jsonl")

    @property
    def portfolio_path(self) -> Path:
        return self.data_dir / "portfolios" / f"{self.id}.json"

    def applies_to_tick(self, market_slug: str, market_timeframe: str) -> bool:
        return market_timeframe == self.timeframe

    def get_or_create_state(self, slug: str, window_duration: int) -> PerMarketState:
        pms = self.states.get(slug)
        if pms is None:
            pms = PerMarketState(slug=slug, cfg=self.cfg, window_duration_sec=window_duration)
            self.states[slug] = pms
        return pms

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
        for log_ in (self.trade_log, self.signal_log, self.snapshot_log):
            if log_ is not None:
                log_.close()
