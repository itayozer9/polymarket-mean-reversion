"""Late-window determinism strategy — the Phase 1 edge, made live.

A DIFFERENT strategy type from the mean-reversion PerMarketState machine. It does
NOT arm/trail/profit-target; it enters once and holds to resolution:

  In the last `t_max_sec` of a 15m window, if spot is >= `dist_min_bps` from the
  strike (favourite agrees with spot) and the favourite's taker ask is in
  [min_ask, max_ask], buy the favourite for `fixed_bet_usd` and HOLD to
  resolution (winners redeem at $1, one-way cost only).

Validated in `docs/research/{oracle_mechanics,gauntlet_verdict}.md` (OOS +$1.68/
trade, 91% WR; survives combined cost-stress; calibrated-null p<0.0001).

Interface matches what PaperEngine calls on PerMarketState:
  on_tick(arr_row, portfolio, rng, outcome=None) -> Optional[Trade]
so it slots into the existing engine with no change to the mean-reversion path.

Live vs backtest deltas (documented, accepted for paper):
  - distance uses the tick's `move_pct` (coinbase vs strike) ×100 = bps — the same
    coinbase basis the backtest used (vs the Chainlink-stream settlement);
  - fills at TOP of book (the tick struct has no 10-level ladder) capped by quoted
    depth — at $10 the backtest filled at level 1 ~always, so this is faithful;
  - forced-resolution settles to 1/0 by the final tick's move sign (the engine
    passes outcome=None), the hold-to-resolution payoff.
"""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
from typing import Optional, Callable

from mean_reversion_live.adapters.arb_imports import Portfolio, Trade
from mean_reversion_live.engine.det_features import RollingMove, utc_hour_dow, symbol_of


def _fee(shares: float, price: float, rate: float) -> float:
    p = min(max(price, 0.0), 1.0)
    return shares * rate * p * (1.0 - p)


class DailyLossGuard:
    """Per-strategy daily-loss circuit breaker (UTC day). Shared across all of a
    strategy's per-window states so the cap is enforced strategy-wide. Once the
    day's realized PnL hits -cap, no new entries until the next UTC day."""

    def __init__(self, max_daily_loss_usd: Optional[float] = None):
        self.cap = max_daily_loss_usd
        self._date: Optional[str] = None
        self._pnl = 0.0

    @staticmethod
    def _utc_date(ts_ms: int) -> str:
        return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")

    def record(self, ts_ms: int, pnl: float) -> None:
        d = self._utc_date(ts_ms)
        if d != self._date:
            self._date, self._pnl = d, 0.0
        self._pnl += pnl

    def blocked(self, ts_ms: int) -> bool:
        if self.cap is None:
            return False
        d = self._utc_date(ts_ms)
        if d != self._date:
            return False   # new UTC day → reset
        return self._pnl <= -abs(self.cap)


@dataclass
class DetParams:
    t_min_sec: int = 1
    t_max_sec: int = 60
    dist_min_bps: float = 5.0
    max_ask: float = 0.90
    min_ask: float = 0.50
    fixed_bet_usd: float = 10.0
    fee_rate: float = 0.07
    max_daily_loss_usd: Optional[float] = None


class DeterminismState:
    """One window's state for the determinism strategy."""

    def __init__(self, slug: str, params: DetParams, window_duration_sec: int,
                 observer: Optional[Callable[[dict], None]] = None,
                 guard: Optional[DailyLossGuard] = None):
        self.slug = slug
        self.p = params
        self.window = window_duration_sec
        self._obs = observer
        self._guard = guard          # shared per-strategy daily-loss breaker
        self.state = "FLAT"
        self._traded = False
        self.pos: Optional[dict] = None
        self._roll = RollingMove()   # live vol/velocity buffer for this window
        self.last_ctx: Optional[dict] = None  # entry context, surfaced at settle

    def on_tick(self, row, portfolio: Portfolio, rng, outcome=None) -> Optional[Trade]:
        sec = int(row["seconds_into_window"])
        ts = int(row["timestamp_ms"])
        move = float(row["move_pct"])           # (spot-strike)/strike*100  (percent)
        ymid = float(row["yes_mid"])
        time_left = self.window - sec
        self._roll.push(sec, move)
        d = {"ts_ms": ts, "slug": self.slug, "decision": "flat",
             "state_before": self.state, "side_signal": None, "near_miss": None,
             "trade_closed": False, "features": None}

        # ── HOLDING: never self-settle on a tick. The position is held to the
        #    true market resolution, settled by the engine's on_close
        #    (settle(), below) at the real end_price. A tick-derived settle was
        #    measured to be either optimistic (move-sign: WR 0.96 vs true 0.89)
        #    or far too conservative (last-tick bid: +$0.31 vs true +$1.58), so
        #    we wait for the authoritative outcome.
        if self.state == "HOLDING":
            d["decision"] = "holding"
            self._emit(d)
            return None

        # ── FLAT ──
        if self._traded:
            d["decision"] = "skipped_already_traded"; self._emit(d); return None
        if not (self.p.t_min_sec <= time_left <= self.p.t_max_sec):
            self._emit(d); return None
        # Book-health guard (CRITICAL): in the final 60s many books are decided /
        # collapsed (crossed quotes, yes_mid -> 0/1). The research edge was
        # measured ONLY on healthy two-sided books; entering on a collapsed book
        # picks unpredictable windows (validated to drop WR from 0.89 to ~0.47).
        yb = float(row["yes_best_bid"]); ya = float(row["yes_best_ask"])
        if not (0.001 < yb < 0.999 and 0.001 < ya < 0.999 and yb < ya
                and float(row["spread_yes"]) > 0):
            d["decision"] = "skipped_unhealthy_book"; self._emit(d); return None
        if not portfolio.can_enter(ts):
            d["decision"] = "skipped_can_enter"; self._emit(d); return None
        if self._guard is not None and self._guard.blocked(ts):
            d["decision"] = "skipped_daily_loss_cap"; self._emit(d); return None

        fav_yes = ymid >= 0.5
        side = "UP" if fav_yes else "DOWN"
        fav_ask = float(row["yes_best_ask"]) if fav_yes else float(row["no_best_ask"])
        dist_bps = abs(move) * 100.0
        spot_fav_yes = move > 0
        consistent = (fav_yes and spot_fav_yes) or ((not fav_yes) and (not spot_fav_yes))

        if not (consistent and dist_bps >= self.p.dist_min_bps
                and self.p.min_ask <= fav_ask <= self.p.max_ask):
            self._emit(d); return None

        depth_shares = float(row["yes_ask_depth"]) if fav_yes else float(row["no_ask_depth"])
        if depth_shares * fav_ask < self.p.fixed_bet_usd:   # not enough top-of-book USD
            d["decision"] = "skipped_no_fill"; self._emit(d); return None

        shares = self.p.fixed_bet_usd / fav_ask
        depth_usd = depth_shares * fav_ask
        hour, dow = utc_hour_dow(ts)
        # Complete per-trade context for post-hoc filter discovery (time-of-day,
        # regime, depth, distance, velocity) — written to trades_detailed.jsonl.
        ctx = {
            "strategy_kind": "determinism", "symbol": symbol_of(self.slug),
            "utc_hour": hour, "dow": dow, "entry_sec": sec, "time_left": time_left,
            "dist_bps": round(dist_bps, 2), "fav_side": side,
            "entry_ask": round(fav_ask, 4), "yes_mid": round(ymid, 4),
            "spread_yes": round(float(row["spread_yes"]), 4),
            "ask_depth_usd": round(depth_usd, 1),
            "spot_vel_10s_bps": round(self._roll.vel_bps(10), 2),
            "spot_vel_30s_bps": round(self._roll.vel_bps(30), 2),
            "rvol_60s_bps": round(self._roll.rvol_bps(60), 2),
        }
        self.pos = {"side": side, "entry": fav_ask, "shares": shares,
                    "bet": self.p.fixed_bet_usd,
                    "fee_entry": _fee(shares, fav_ask, self.p.fee_rate),
                    "ts": ts, "entry_sec": sec, "ctx": ctx}
        self.state = "HOLDING"
        portfolio.on_entry(ts)
        d["decision"] = "fired"; d["side_signal"] = side
        d["features"] = {"dist_bps": ctx["dist_bps"], "fav_ask": ctx["entry_ask"],
                         "time_left": time_left}
        self._emit(d)
        return None

    def settle(self, outcome_up: bool, ts_ms: int, portfolio: Portfolio) -> Optional[Trade]:
        """Settle a held position at the TRUE window resolution (called by the
        engine on_close with the real end_price). Winners redeem at $1, losers $0
        (hold-to-resolution = one-way cost). Returns the Trade, or None if flat."""
        if self.state != "HOLDING" or self.pos is None:
            return None
        won = outcome_up if self.pos["side"] == "UP" else (not outcome_up)
        exit_price = 1.0 if won else 0.0
        fee_exit = _fee(self.pos["shares"], exit_price, self.p.fee_rate)
        pnl = (self.pos["shares"] * exit_price - self.pos["bet"]
               - self.pos["fee_entry"] - fee_exit)
        held = max(0, (ts_ms - self.pos["ts"]) // 1000)
        trade = Trade(
            slug=self.slug, side=self.pos["side"],
            entry_ts_ms=self.pos["ts"], exit_ts_ms=ts_ms,
            entry_price=self.pos["entry"], exit_price=exit_price,
            shares=self.pos["shares"], bet_usd=self.pos["bet"],
            fee_total=self.pos["fee_entry"] + fee_exit, pnl=pnl,
            exit_reason="resolution", seconds_held=int(held))
        # surface entry context + outcome for the detailed log
        self.last_ctx = {**self.pos.get("ctx", {}), "outcome_up": int(bool(outcome_up)),
                         "won": int(bool(won)), "pnl": round(pnl, 4)}
        portfolio.on_exit(trade)
        if self._guard is not None:
            self._guard.record(ts_ms, pnl)
        self.pos = None
        self.state = "FLAT"
        self._traded = True
        self._emit({"ts_ms": ts_ms, "slug": self.slug, "decision": "trade_closed_resolution",
                    "trade_closed": True, "state_before": "HOLDING", "state_after": "FLAT",
                    "side_signal": trade.side, "near_miss": None, "features": None})
        return trade

    def _emit(self, d: dict) -> None:
        d["state_after"] = self.state
        if self._obs is not None:
            try:
                self._obs(d)
            except Exception:
                pass
