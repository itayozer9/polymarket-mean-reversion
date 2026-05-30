"""Phase 2 — mid-window stale-quote pickoff, made live.

Bets WITH the spot-implied fair value when the book is stale right after a fast
spot jump. Fair value is a FROZEN empirical curve P(Up | z), z = signed
distance-to-strike in units of expected remaining move
(z = dist_bps / (rvol_bps * sqrt(time_left))).

Rule: mid-window (t_lo_left..t_hi_left seconds remaining), if
|P(Up|z) - yes_mid| in [margin, max_mispricing] AND a recent spot jump
(|vel_10s| >= jump_bps), buy the model's side as a taker and HOLD to resolution.
One trade per window, healthy book only, settled at the TRUE outcome on_close.

Validated (`docs/research/stale_quote.md`): jump-gated OOS +$2.7-3.7/trade, but
HIGHER VARIANCE than Phase 1 (WR ~50%, outlier-driven). The max_mispricing cap
drops the deep-longshot lottery tickets. Secondary to the determinism edge.

Shares the live plumbing of DeterminismState (book-health guard, DailyLossGuard,
true-outcome settle via engine.settle_window, rich per-trade context).
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional, Callable, List

from mean_reversion_live.adapters.arb_imports import Portfolio, Trade
from mean_reversion_live.engine.determinism_state import _fee, DailyLossGuard
from mean_reversion_live.engine.det_features import RollingMove, utc_hour_dow, symbol_of


def _interp(x: float, xs: List[float], ys: List[float]) -> float:
    if not xs:
        return 0.5
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            dx = xs[i] - xs[i - 1]
            t = (x - xs[i - 1]) / dx if dx else 0.0
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return ys[-1]


@dataclass
class StaleQuoteParams:
    zc: List[float] = field(default_factory=list)       # frozen curve x (z)
    p_up: List[float] = field(default_factory=list)     # frozen curve y (P(Up))
    margin: float = 0.08            # min |model_p - mid| to act
    max_mispricing: float = 0.30    # cap: drop deep-longshot lottery tickets
    jump_bps: float = 8.0           # required recent spot move (|vel_10s|)
    t_lo_left: int = 60             # mid-window only: >= this many sec left ...
    t_hi_left: int = 840            # ... and <= this many (exclude open noise)
    min_ask: float = 0.05
    max_ask: float = 0.95
    fixed_bet_usd: float = 10.0
    fee_rate: float = 0.07
    max_daily_loss_usd: Optional[float] = None


class StaleQuoteState:
    def __init__(self, slug: str, params: StaleQuoteParams, window_duration_sec: int,
                 observer: Optional[Callable[[dict], None]] = None,
                 guard: Optional[DailyLossGuard] = None):
        self.slug = slug
        self.p = params
        self.window = window_duration_sec
        self._obs = observer
        self._guard = guard
        self.state = "FLAT"
        self._traded = False
        self.pos: Optional[dict] = None
        self._roll = RollingMove()
        self.last_ctx: Optional[dict] = None

    def on_tick(self, row, portfolio: Portfolio, rng, outcome=None) -> Optional[Trade]:
        sec = int(row["seconds_into_window"])
        ts = int(row["timestamp_ms"])
        move = float(row["move_pct"])
        ymid = float(row["yes_mid"])
        time_left = self.window - sec
        self._roll.push(sec, move)
        d = {"ts_ms": ts, "slug": self.slug, "decision": "flat",
             "state_before": self.state, "side_signal": None, "near_miss": None,
             "trade_closed": False, "features": None}

        if self.state == "HOLDING":
            d["decision"] = "holding"; self._emit(d); return None
        if self._traded:
            d["decision"] = "skipped_already_traded"; self._emit(d); return None
        if not (self.p.t_lo_left <= time_left <= self.p.t_hi_left):
            self._emit(d); return None
        yb = float(row["yes_best_bid"]); ya = float(row["yes_best_ask"])
        if not (0.001 < yb < 0.999 and 0.001 < ya < 0.999 and yb < ya
                and float(row["spread_yes"]) > 0):
            d["decision"] = "skipped_unhealthy_book"; self._emit(d); return None
        if not portfolio.can_enter(ts):
            d["decision"] = "skipped_can_enter"; self._emit(d); return None
        if self._guard is not None and self._guard.blocked(ts):
            d["decision"] = "skipped_daily_loss_cap"; self._emit(d); return None

        rvol = self._roll.rvol_bps(60)
        if rvol <= 1e-6:
            self._emit(d); return None
        z = (move * 100.0) / (rvol * math.sqrt(max(time_left, 1)))
        model_p = _interp(z, self.p.zc, self.p.p_up)
        mis = model_p - ymid                  # >0 => model says YES underpriced
        jump = abs(self._roll.vel_bps(10))
        amis = abs(mis)
        if not (self.p.margin <= amis <= self.p.max_mispricing and jump >= self.p.jump_bps):
            self._emit(d); return None

        buy_yes = mis > 0
        side = "UP" if buy_yes else "DOWN"
        fav_ask = ya if buy_yes else float(row["no_best_ask"])
        if not (self.p.min_ask <= fav_ask <= self.p.max_ask):
            self._emit(d); return None
        depth_shares = float(row["yes_ask_depth"]) if buy_yes else float(row["no_ask_depth"])
        if depth_shares * fav_ask < self.p.fixed_bet_usd:
            d["decision"] = "skipped_no_fill"; self._emit(d); return None

        shares = self.p.fixed_bet_usd / fav_ask
        hour, dow = utc_hour_dow(ts)
        ctx = {
            "strategy_kind": "stale_quote", "symbol": symbol_of(self.slug),
            "utc_hour": hour, "dow": dow, "entry_sec": sec, "time_left": time_left,
            "fav_side": side, "entry_ask": round(fav_ask, 4), "yes_mid": round(ymid, 4),
            "model_p": round(model_p, 4), "mispricing": round(mis, 4), "z": round(z, 3),
            "dist_bps": round(abs(move) * 100.0, 2),
            "spread_yes": round(float(row["spread_yes"]), 4),
            "ask_depth_usd": round(depth_shares * fav_ask, 1),
            "spot_vel_10s_bps": round(self._roll.vel_bps(10), 2),
            "rvol_60s_bps": round(rvol, 2),
        }
        self.pos = {"side": side, "entry": fav_ask, "shares": shares,
                    "bet": self.p.fixed_bet_usd,
                    "fee_entry": _fee(shares, fav_ask, self.p.fee_rate),
                    "ts": ts, "entry_sec": sec, "ctx": ctx}
        self.state = "HOLDING"
        portfolio.on_entry(ts)
        d["decision"] = "fired"; d["side_signal"] = side
        d["features"] = {"mispricing": ctx["mispricing"], "z": ctx["z"],
                         "jump_bps": ctx["spot_vel_10s_bps"], "entry_ask": ctx["entry_ask"]}
        self._emit(d)
        return None

    def settle(self, outcome_up: bool, ts_ms: int, portfolio: Portfolio) -> Optional[Trade]:
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
