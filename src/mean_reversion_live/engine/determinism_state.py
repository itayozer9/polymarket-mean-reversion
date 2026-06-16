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
from mean_reversion_live.engine.det_features import (
    ResearchRVol, RollingMove, utc_hour_dow, symbol_of)
from mean_reversion_live.engine.print_model import PrintModel, p_settle_up


def _struct_get(row, name: str) -> float:
    """NaN-safe field read that works for both a numpy struct row (live/replay) and a
    plain dict, returning NaN when the field is absent — so the dual-oracle gate is a
    pure no-op on tick streams (e.g. replay CSVs) that don't carry `cl_dist_bps`."""
    try:
        return float(row[name])
    except (KeyError, ValueError, IndexError, TypeError):
        return float("nan")


def _fee(shares: float, price: float, rate: float) -> float:
    p = min(max(price, 0.0), 1.0)
    return shares * rate * p * (1.0 - p)


class DailyLossGuard:
    """Per-strategy daily-loss circuit breaker (UTC day). Shared across all of a
    strategy's per-window states so the cap is enforced strategy-wide.

    Modes (see docs/research/daily_cap_redesign.md), selected per strategy via the
    `daily_loss_mode` yaml key:
      - "soft_settled" (default, legacy): blocks when the day's SETTLED PnL <= -cap.
        Re-arms if a later settlement recovers; blind to still-open positions; does
        NOT survive a restart. Preserved byte-for-byte so existing strategies and
        the `det_sqp_v1_capped` paper twin are unchanged.
      - "hard_worstcase": reserves each open position's worst-case loss against the
        cap at entry, so the day's realized PnL provably never drops below -cap.
        Re-arming is safe (a settling winner restores headroom without risking the
        bound).
      - "hard_worstcase_latch": same hard bound, but once the limit is reached it
        stops for the rest of the UTC day (no resume on recovery).

    Interface: would_block() at entry, on_entry() once a position is committed,
    on_settle() at resolution, replay_settled() on startup (hard modes). The legacy
    blocked()/record() remain as thin shims so older callers/tests keep working.
    """

    _MODES = ("soft_settled", "hard_worstcase", "hard_worstcase_latch")

    def __init__(self, max_daily_loss_usd: Optional[float] = None,
                 mode: str = "soft_settled"):
        if mode not in self._MODES:
            raise ValueError(
                f"unknown daily_loss_mode {mode!r}; expected one of {self._MODES}")
        self.cap = max_daily_loss_usd
        self.mode = mode
        self._date: Optional[str] = None
        self._settled = 0.0          # realized PnL of trades resolved today
        self._open_worst = 0.0       # Σ worst-case loss of currently-open positions
        self._open: dict = {}        # pos_id -> reserved worst-case loss
        self._latched = False

    @staticmethod
    def _utc_date(ts_ms: int) -> str:
        return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")

    def _roll(self, ts_ms: int) -> None:
        d = self._utc_date(ts_ms)
        if d != self._date:
            self._date, self._settled, self._open_worst, self._latched = d, 0.0, 0.0, False
            self._open = {}

    def _worst_case(self) -> float:
        """Day PnL if every currently-open position loses fully."""
        return self._settled - self._open_worst

    # ── entry side ──────────────────────────────────────────────────────────
    def would_block(self, ts_ms: int, new_max_loss: float = 0.0) -> bool:
        """True if a new entry risking `new_max_loss` should be skipped."""
        if self.cap is None:
            return False
        self._roll(ts_ms)
        if self.mode == "soft_settled":
            return self._settled <= -abs(self.cap)
        if self._latched:
            return True
        return (self._worst_case() - new_max_loss) < -abs(self.cap)

    def on_entry(self, ts_ms: int, pos_id, max_loss: float) -> None:
        """Reserve a newly-committed position's worst-case loss against the cap."""
        if self.cap is None or self.mode == "soft_settled":
            return
        self._roll(ts_ms)
        if pos_id not in self._open:
            self._open[pos_id] = max_loss
            self._open_worst += max_loss
        if self.mode == "hard_worstcase_latch" and self._worst_case() <= -abs(self.cap):
            self._latched = True

    # ── settle side ─────────────────────────────────────────────────────────
    def on_settle(self, ts_ms: int, pos_id, pnl: float) -> None:
        """Release a position's reservation and book its realized PnL."""
        if self.cap is None:
            return
        self._roll(ts_ms)
        reserved = self._open.pop(pos_id, None)
        if reserved is not None:
            self._open_worst -= reserved
        self._settled += pnl

    # ── startup (restart-safety; hard modes only) ─────────────────────────────
    def replay_settled(self, ts_ms: int, pnl: float) -> None:
        if self.cap is None or self.mode == "soft_settled":
            return
        self._roll(ts_ms)
        self._settled += pnl

    # ── legacy shims (older callers / tests) ──────────────────────────────────
    def blocked(self, ts_ms: int) -> bool:
        return self.would_block(ts_ms, 0.0)

    def record(self, ts_ms: int, pnl: float) -> None:
        self.on_settle(ts_ms, None, pnl)


@dataclass
class DetParams:
    t_min_sec: int = 1
    t_max_sec: int = 60
    dist_min_bps: float = 5.0
    max_ask: float = 0.90
    min_ask: float = 0.50
    # Entry mode (additive; default reproduces det_lwd byte-for-byte):
    #   "consistent" — book favourite AGREES with spot, buy the favourite (det_lwd).
    #   "disagree"   — book favourite DISAGREES with spot (E4), buy the cheap
    #                  spot-implied side the book has as underdog. Verified edge
    #                  (docs/research/IMPROVEMENT_FINDINGS_2026-06-04.md).
    mode: str = "consistent"
    fixed_bet_usd: float = 10.0
    fee_rate: float = 0.07
    max_daily_loss_usd: Optional[float] = None
    daily_loss_mode: str = "soft_settled"  # soft_settled|hard_worstcase|hard_worstcase_latch
    # v2 loss-avoidance filters (docs/research/loss_pattern_filters.md).
    # Defaults are no-ops so v1 behaviour is byte-identical.
    adverse_vel_max_bps: Optional[float] = None  # skip if spot is reverting toward
                                                 # strike faster than this (10s, bps)
    min_strike_crossings: int = 0                # require >= N strike crossings in the
                                                 # window before entry (drop 0-crossing
                                                 # already-decided blowout windows)
    # Favourite-value extensions (2026-06-05 Chainlink edge hunt; no-op defaults keep
    # det_lwd/E4 byte-identical). Let DeterminismState express the verified mid-window
    # favourite-underpricing edges (low-vol cheap-fav, deep DOWN-fav, momentum-confirmed).
    vol_max_bps: Optional[float] = None          # skip if 60s realized vol > this (lo-vol regime)
    restrict_fav_side: Optional[str] = None      # "yes"|"no": only enter when THAT side is the favourite
    # Dual-oracle gate (2026-06-09): the SIGNAL is Coinbase (move_pct) but Polymarket SETTLES
    # on Chainlink, so near-strike windows flip and a flip turns a +$1 favourite-win into a -$5
    # loss. The tick carries `cl_dist_bps` (Chainlink spot-vs-strike at entry, from ws_collector
    # live / dual_oracle_features offline); the gate requires Chainlink to AGREE with Coinbase on
    # direction and/or be far enough from its own strike. No-op default (oracle_gate=None).
    oracle_gate: Optional[str] = None            # None | "agree" | "dist" | "agree_and_dist"
    cl_dist_min_bps: Optional[float] = None       # min |chainlink dist| for "dist"/"agree_and_dist"
    oracle_gate_on_missing: str = "skip"          # "skip" (fail-closed, live-safe) | "allow"
    # Dynamic (adaptive) max_ask: the expensive favourites (above the base max_ask) are low-EV ON
    # AVERAGE, but those where Chainlink shows a DEEP lock are genuinely safe — so raise the ceiling
    # to `max_ask_hi` only when |cl_dist_bps| >= cl_dist_hi_bps, else use the base max_ask. No-op
    # default (max_ask_hi=None). Backtest: cl-dyn 0.78->0.85 @|cld|>=20 ≈ flat-0.78 EV/tr with +17%
    # volume / tighter future-CI (docs/research/DUAL_ORACLE_2026-06-09.md).
    max_ask_hi: Optional[float] = None            # higher ceiling used only on deep Chainlink locks
    cl_dist_hi_bps: Optional[float] = None         # |cl_dist| threshold to switch to max_ask_hi
    # ── mode="psettle" (2026-06-11): settlement-print-model edges, PAPER ───────────
    # The live twin of the research p_settle rules (ORACLE_PRINT_2026-06-10 +
    # SWEEP_LIVECOST_2026-06-10). The frozen logistic (engine/print_model.py, parity-
    # pinned to research p_settle) is evaluated at every candidate tick on features
    # the collector now carries (cl_dist_bps, cl_cb_basis_bps, cl_oracle_age_s) plus
    # engine-computed cb_dist (= move_pct*100), time_left and the research-parity
    # realized_vol (ResearchRVol). All fields default off => other modes byte-identical.
    #   psettle_side="underdog" (sweep family side="cheap", spec psettle_2246):
    #     buy the NON-favourite when p_settle(that side) - its YES-equivalent ask
    #     >= psettle_margin, ask within [min_ask, max_ask], tl in [t_min, t_max].
    #   psettle_side="fade" (OP4 near-strike fade):
    #     book favourite (YES-equivalent ask) >= psettle_fav_ask_min BUT
    #     p_settle(favourite) <= psettle_p_fav_max -> buy the CHEAP side at its
    #     YES-equivalent ask within [min_ask, max_ask].
    # Ask convention is the RESEARCH one: yes side = yes_best_ask, no side =
    # 1 - yes_best_bid (the joined frame has no NO-book columns; on Polymarket the
    # NO book mirrors the YES book, so this equals no_best_ask up to feed timing).
    # Missing/NaN model features ALWAYS fail closed (skip) — same live-safety as the
    # dual-oracle gate; psettle_on_missing exists for explicitness and only "skip"
    # is accepted.
    psettle_side: Optional[str] = None            # "underdog" | "fade"
    psettle_margin: Optional[float] = None        # underdog: require p_side - ask >= this
    psettle_p_fav_max: Optional[float] = None     # fade: require p_settle(fav) <= this
    psettle_fav_ask_min: Optional[float] = None   # fade: require fav YES-equiv ask >= this
    psettle_cl_floor_bps: Optional[float] = None  # optional |cl_dist| floor (spec cl_floor)
    psettle_on_missing: str = "skip"              # only "skip" (fail-closed) is valid
    psettle_model: Optional[PrintModel] = None    # loaded oracle_print_model artifact
    # ── mode="xb" (2026-06-12): 5m↔15m cross-book monotonicity twin, PAPER ────────
    # CAUSAL variant of research rule XI4 (research/analysis/external_inputs.py
    # monotonic_signal / build_xi4_join; test_ledger § "XI4 AMENDMENT 2026-06-12").
    # In the final ~300s of a 15m window the co-terminal 5m market (same close,
    # strike K5 = spot at 15m_start+600s) bounds the 15m price by strike
    # monotonicity. When the bound is violated at EXECUTABLE quotes — the 15m
    # side's YES-equivalent ask + xb_premium <= the 5m implied side's bid, with
    # |K5 vs K15| >= xb_gap_min_bps and >= xb_min_ref_usd notional behind the
    # referenced 5m quote — buy that 15m side and hold to resolution.
    # The tick carries the co-terminal 5m book + strike (ws_collector xb5_*
    # fields); xb5_k5 is NaN until discovery captures the 5m strike, so the
    # engine is causal BY CONSTRUCTION (the acausal back-fill that sank the
    # original XI4 headline cannot happen live). Every xb5_* NaN fails closed.
    xb_premium: Optional[float] = None            # require 15m ask + this <= co5 ref bid
    xb_gap_min_bps: Optional[float] = None        # min strike gap |K5-K15|/K15 (bps)
    xb_min_ref_usd: float = 1.0                   # $ floor on the referenced 5m quote
                                                  # (XI4_MIN_REF_NOTIONAL)
    xb_on_missing: str = "skip"                   # only "skip" (fail-closed) is valid
    # ── mode="tadiv_approx" (2026-06-16): TA-divergence APPROXIMATION twin, PAPER ──
    # Live twin of research fam_ta_divergence (docs/research/TA_STRATEGIES_2026-06-16.md,
    # spec ta_divergence_2585/2588). Buys the spot-move-implied side when the 30s spot
    # move >= tadiv_ret_min_bps and the 10s velocity agrees in sign (EMA-slope proxy),
    # in time_left [t_min_sec, t_max_sec], bought-side ask in [min_ask, max_ask].
    # Uses RollingMove.vel_bps only — NO new tick field. APPROXIMATION: vel_bps is bps
    # vs strike, research ta_ret_30s is bps vs spot (<1% diff); the parity-faithful
    # mode="tadiv" is a separate follow-up. All-None default keeps other modes
    # byte-identical.
    tadiv_ret_min_bps: Optional[float] = None     # |30s spot move| floor (bps), the ret_min


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
        self._crossings = 0          # # times spot crossed the strike this window
        self._last_sign = 0          # last non-zero sign of (spot - strike)
        # mode="psettle": research-parity realized_vol buffer + fail-fast config check.
        self._rv: Optional[ResearchRVol] = None
        if params.mode == "psettle":
            if params.psettle_model is None:
                raise ValueError("mode='psettle' requires psettle_model (model_file)")
            if params.psettle_side not in ("underdog", "fade"):
                raise ValueError(
                    f"psettle_side must be 'underdog'|'fade', got {params.psettle_side!r}")
            if params.psettle_side == "underdog" and params.psettle_margin is None:
                raise ValueError("psettle_side='underdog' requires psettle_margin")
            if params.psettle_side == "fade" and (
                    params.psettle_p_fav_max is None or params.psettle_fav_ask_min is None):
                raise ValueError(
                    "psettle_side='fade' requires psettle_p_fav_max + psettle_fav_ask_min")
            if params.psettle_on_missing != "skip":
                raise ValueError("psettle_on_missing only supports 'skip' (fail-closed)")
            self._rv = ResearchRVol()
        # mode="xb": fail-fast config check (cross-book 5m↔15m twin).
        if params.mode == "xb":
            if params.xb_premium is None:
                raise ValueError("mode='xb' requires xb_premium")
            if params.xb_gap_min_bps is None:
                raise ValueError("mode='xb' requires xb_gap_min_bps")
            if params.xb_on_missing != "skip":
                raise ValueError("xb_on_missing only supports 'skip' (fail-closed)")
        # mode="tadiv_approx": fail-fast config check (TA-divergence approximation twin).
        if params.mode == "tadiv_approx" and params.tadiv_ret_min_bps is None:
            raise ValueError("mode='tadiv_approx' requires tadiv_ret_min_bps")

    def on_tick(self, row, portfolio: Portfolio, rng, outcome=None) -> Optional[Trade]:
        sec = int(row["seconds_into_window"])
        ts = int(row["timestamp_ms"])
        move = float(row["move_pct"])           # (spot-strike)/strike*100  (percent)
        ymid = float(row["yes_mid"])
        time_left = self.window - sec
        self._roll.push(sec, move)
        if self._rv is not None:        # psettle: research-parity vol sees EVERY tick
            self._rv.push(move)
        # Track strike crossings cumulatively (zeros carry the previous sign).
        s_now = 1 if move > 0 else (-1 if move < 0 else 0)
        if s_now != 0:
            if self._last_sign != 0 and s_now != self._last_sign:
                self._crossings += 1
            self._last_sign = s_now
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
        if self._guard is not None and self._guard.would_block(
                ts, self.p.fixed_bet_usd * (1.0 + self.p.fee_rate)):
            d["decision"] = "skipped_daily_loss_cap"; self._emit(d); return None

        # ── mode="psettle": settlement-print-model entry. A SEPARATE path that
        #    returns here — the legacy consistent/disagree flow below is untouched
        #    (byte-identical for every existing strategy, incl. the *_live ones). ──
        if self.p.mode == "psettle":
            return self._psettle_entry(row, d, ts, time_left, move, ymid, portfolio)

        # ── mode="xb": 5m↔15m cross-book entry. Same separate-path pattern as
        #    psettle — returns here, legacy flow below byte-identical. ──
        if self.p.mode == "xb":
            return self._xb_entry(row, d, ts, time_left, move, ymid, portfolio)

        fav_yes = ymid >= 0.5
        dist_bps = abs(move) * 100.0
        spot_fav_yes = move > 0
        consistent = (fav_yes and spot_fav_yes) or ((not fav_yes) and (not spot_fav_yes))

        # mode="disagree" (E4): book favourite disagrees with spot -> buy the cheap
        # SPOT-implied side the book has wrong. mode="consistent" is the original
        # det_lwd path (byte-identical: buy_yes==fav_yes, gate_ok==consistent).
        if self.p.mode == "disagree":
            buy_yes = spot_fav_yes
            gate_ok = (not consistent)
        else:
            buy_yes = fav_yes
            gate_ok = consistent
        side = "UP" if buy_yes else "DOWN"
        fav_ask = float(row["yes_best_ask"]) if buy_yes else float(row["no_best_ask"])

        # Chainlink distance at this tick (NaN if unavailable) — drives BOTH the dynamic ceiling
        # (below) and the dual-oracle gate (further down). Read once, reuse.
        cl_dist = _struct_get(row, "cl_dist_bps")
        cl_present = cl_dist == cl_dist  # False when NaN
        # Dynamic ceiling: raise to max_ask_hi only on a deep Chainlink lock; else the base max_ask
        # (also the conservative fallback when Chainlink is missing).
        eff_max_ask = self.p.max_ask
        if (self.p.max_ask_hi is not None and self.p.cl_dist_hi_bps is not None
                and cl_present and abs(cl_dist) >= self.p.cl_dist_hi_bps):
            eff_max_ask = self.p.max_ask_hi

        if not (gate_ok and dist_bps >= self.p.dist_min_bps
                and self.p.min_ask <= fav_ask <= eff_max_ask):
            self._emit(d); return None

        # ── v2 loss-avoidance gates (no-ops at default params) ──
        # adverse velocity: spot reverting toward the strike (against the lock).
        # adverse = -sign(dist) * spot_vel_10s ; >0 means moving back toward strike.
        adverse_vel = -(1.0 if move > 0 else -1.0) * self._roll.vel_bps(10)
        if (self.p.adverse_vel_max_bps is not None
                and adverse_vel > self.p.adverse_vel_max_bps):
            d["decision"] = "skipped_adverse_vel"; self._emit(d); return None
        # require the window to have genuinely two-sided action (>= N strike
        # crossings) — skip already-decided 0-crossing blowout windows.
        if self._crossings < self.p.min_strike_crossings:
            d["decision"] = "skipped_few_crossings"; self._emit(d); return None
        # favourite-value regime gates (no-op at defaults)
        if self.p.vol_max_bps is not None and self._roll.rvol_bps(60) > self.p.vol_max_bps:
            d["decision"] = "skipped_high_vol"; self._emit(d); return None
        if (self.p.restrict_fav_side is not None
                and ("yes" if fav_yes else "no") != self.p.restrict_fav_side):
            d["decision"] = "skipped_wrong_fav_side"; self._emit(d); return None

        # ── dual-oracle gate (no-op when oracle_gate is None) ──
        # The Coinbase signal can disagree with the Chainlink outcome Polymarket pays; a flip
        # turns a +$1 favourite-win into a -$5 loss. `cl_dist_bps` = Chainlink spot-vs-strike at
        # this tick. Require Chainlink to agree with Coinbase on direction (sign(cl_dist) ==
        # spot_fav_yes) and/or be >= cl_dist_min_bps from its own strike. Fail-closed on missing
        # Chainlink (live-safe). Same agreement test the offline sweep uses (dual_oracle_*).
        if self.p.oracle_gate is not None:
            if not cl_present:  # Chainlink unavailable at this tick
                if self.p.oracle_gate_on_missing == "skip":
                    d["decision"] = "skipped_oracle_missing"; self._emit(d); return None
                # "allow": fall through ungated
            else:
                cl_up = cl_dist > 0.0
                if (self.p.oracle_gate in ("agree", "agree_and_dist")
                        and cl_up != spot_fav_yes):
                    d["decision"] = "skipped_oracle_disagree"; self._emit(d); return None
                if (self.p.oracle_gate in ("dist", "agree_and_dist")
                        and abs(cl_dist) < (self.p.cl_dist_min_bps or 0.0)):
                    d["decision"] = "skipped_cl_dist"; self._emit(d); return None

        depth_shares = float(row["yes_ask_depth"]) if buy_yes else float(row["no_ask_depth"])
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
            "strike_crossings": self._crossings,
            "adverse_vel_10s_bps": round(adverse_vel, 2),
        }
        self.pos = {"side": side, "entry": fav_ask, "shares": shares,
                    "bet": self.p.fixed_bet_usd,
                    "fee_entry": _fee(shares, fav_ask, self.p.fee_rate),
                    "ts": ts, "entry_sec": sec, "ctx": ctx}
        self.state = "HOLDING"
        portfolio.on_entry(ts)
        if self._guard is not None:
            self._guard.on_entry(ts, self.slug, self.pos["bet"] + self.pos["fee_entry"])
        d["decision"] = "fired"; d["side_signal"] = side
        d["features"] = {"dist_bps": ctx["dist_bps"], "fav_ask": ctx["entry_ask"],
                         "time_left": time_left, "eff_max_ask": round(eff_max_ask, 4)}
        self._emit(d)
        return None

    def _psettle_entry(self, row, d: dict, ts: int, time_left: int, move: float,
                       ymid: float, portfolio: Portfolio) -> None:
        """mode="psettle" gate + entry (research rules psettle_2246 / OP4 fade).

        Reached only after the shared gates (time band, healthy book, can_enter,
        daily-loss guard) — which mirror the research universe (book_healthy +
        first qualifying tick per window; tl band == the spec's t_lo..t_hi).
        Feature conventions are the RESEARCH ones (see DetParams comment); any
        non-finite model feature fails closed.
        """
        p = self.p
        sec = int(row["seconds_into_window"])
        fav_yes = ymid >= 0.5                       # research _base: yes_mid >= 0.5
        yes_ask = float(row["yes_best_ask"])
        no_ask = 1.0 - float(row["yes_best_bid"])   # YES-equivalent NO ask (research)

        cl_dist = _struct_get(row, "cl_dist_bps")
        cl_basis = _struct_get(row, "cl_cb_basis_bps")
        cl_age = _struct_get(row, "cl_oracle_age_s")
        cb_dist = move * 100.0                      # move_pct (percent) -> bps
        rvol = self._rv.value() if self._rv is not None else 0.0
        p_up = p_settle_up(
            p.psettle_model, cl_dist_bps=cl_dist, cb_dist_bps=cb_dist,
            cl_cb_basis_bps=cl_basis, cl_oracle_age_s=cl_age,
            time_left_sec=float(time_left), realized_vol=rvol,
            symbol=symbol_of(self.slug))
        if p_up != p_up:  # NaN -> a model feature is unavailable; fail closed
            d["decision"] = "skipped_psettle_missing"; self._emit(d); return None
        if (p.psettle_cl_floor_bps is not None and p.psettle_cl_floor_bps > 0
                and abs(cl_dist) < p.psettle_cl_floor_bps):
            d["decision"] = "skipped_cl_dist"; self._emit(d); return None

        p_fav = p_up if fav_yes else 1.0 - p_up
        fav_ask_yeq = yes_ask if fav_yes else no_ask
        if p.psettle_side == "underdog":
            # sweep family side="cheap": buy the NON-favourite when the model says
            # it is underpriced by >= margin at its YES-equivalent ask.
            buy_yes = not fav_yes
            ask = yes_ask if buy_yes else no_ask
            p_side = p_up if buy_yes else 1.0 - p_up
            edge = p_side - ask
            if not (edge >= p.psettle_margin and p.min_ask <= ask <= p.max_ask
                    and ask > 0.01):
                self._emit(d); return None
        else:  # "fade" (OP4): rich favourite the model doubts -> buy the cheap side
            buy_yes = not fav_yes
            ask = yes_ask if buy_yes else no_ask    # the CHEAP side's YES-equiv ask
            p_side = 1.0 - p_fav
            edge = p_side - ask
            if not (fav_ask_yeq >= p.psettle_fav_ask_min
                    and p_fav <= p.psettle_p_fav_max
                    and p.min_ask <= ask <= p.max_ask and ask > 0.01):
                self._emit(d); return None

        side = "UP" if buy_yes else "DOWN"
        depth_shares = float(row["yes_ask_depth"]) if buy_yes else float(row["no_ask_depth"])
        if depth_shares * ask < p.fixed_bet_usd:    # not enough top-of-book USD
            d["decision"] = "skipped_no_fill"; self._emit(d); return None

        shares = p.fixed_bet_usd / ask
        hour, dow = utc_hour_dow(ts)
        adverse_vel = -(1.0 if move > 0 else -1.0) * self._roll.vel_bps(10)
        ctx = {
            "strategy_kind": "determinism", "symbol": symbol_of(self.slug),
            "utc_hour": hour, "dow": dow, "entry_sec": sec, "time_left": time_left,
            "dist_bps": round(abs(cb_dist), 2), "fav_side": side,
            "entry_ask": round(ask, 4), "yes_mid": round(ymid, 4),
            "spread_yes": round(float(row["spread_yes"]), 4),
            "ask_depth_usd": round(depth_shares * ask, 1),
            "spot_vel_10s_bps": round(self._roll.vel_bps(10), 2),
            "spot_vel_30s_bps": round(self._roll.vel_bps(30), 2),
            "rvol_60s_bps": round(self._roll.rvol_bps(60), 2),
            "strike_crossings": self._crossings,
            "adverse_vel_10s_bps": round(adverse_vel, 2),
            # psettle decision features (post-hoc analysis / weekly review)
            "psettle_side": p.psettle_side,
            "p_settle_up": round(p_up, 4), "p_side": round(p_side, 4),
            "psettle_edge": round(edge, 4), "p_fav": round(p_fav, 4),
            "fav_ask_yeq": round(fav_ask_yeq, 4),
            "cl_dist_bps": round(cl_dist, 2),
            "cl_cb_basis_bps": round(cl_basis, 2),
            "cl_oracle_age_s": round(cl_age, 1),
            "realized_vol_pct": round(rvol, 6),
        }
        self.pos = {"side": side, "entry": ask, "shares": shares,
                    "bet": p.fixed_bet_usd,
                    "fee_entry": _fee(shares, ask, p.fee_rate),
                    "ts": ts, "entry_sec": sec, "ctx": ctx}
        self.state = "HOLDING"
        portfolio.on_entry(ts)
        if self._guard is not None:
            self._guard.on_entry(ts, self.slug, self.pos["bet"] + self.pos["fee_entry"])
        d["decision"] = "fired"; d["side_signal"] = side
        d["features"] = {"fav_ask": ctx["entry_ask"], "time_left": time_left,
                         "p_side": ctx["p_side"], "psettle_edge": ctx["psettle_edge"],
                         "eff_max_ask": round(p.max_ask, 4)}
        self._emit(d)
        return None

    def _xb_entry(self, row, d: dict, ts: int, time_left: int, move: float,
                  ymid: float, portfolio: Portfolio) -> None:
        """mode="xb" gate + entry — CAUSAL twin of research rule XI4
        (external_inputs.monotonic_signal on build_xi4_join, m=xb_premium,
        g=xb_gap_min_bps; ceiling = max_ask).

        Reached only after the shared gates (time band == the rule's
        seconds-into-window overlap, healthy 15m book == the research
        book_healthy, can_enter, daily-loss guard). Conventions are the RESEARCH
        ones: the 15m DOWN ask is YES-equivalent (1 - yes_best_bid); the
        referenced 5m quote is the co-terminal 5m market's YES book at the SAME
        second (ws_collector reads both books from one `_books` dict in one
        pass, so the engine's view equals the 5m CSV row research joined on).

        CAUSALITY (the whole point of this twin): xb5_k5 is NaN until discovery
        captures the 5m strike (median ~24s after the 5m open), and every NaN
        fails closed — the engine physically cannot fire on the acausal
        back-filled ticks that invalidated XI4's registered backtest numbers
        (test_ledger § "XI4 AMENDMENT 2026-06-12"). Forward EV is UNPROVEN;
        this state exists to MEASURE it.
        """
        p = self.p
        sec = int(row["seconds_into_window"])
        yb = float(row["yes_best_bid"])
        ya = float(row["yes_best_ask"])
        k15 = float(row["start_price"])             # 15m strike (Coinbase at open)
        y5b = _struct_get(row, "xb5_yes_bid")       # co-terminal 5m YES top-of-book
        y5a = _struct_get(row, "xb5_yes_ask")
        y5b_sz = _struct_get(row, "xb5_yes_bid_sz")
        y5a_sz = _struct_get(row, "xb5_yes_ask_sz")
        k5 = _struct_get(row, "xb5_k5")             # NaN until discovery captures it

        # Fail closed on ANY missing input: co5 market/book not visible, the 5m
        # strike not yet captured (the causal gate), or no 15m strike.
        vals = (y5b, y5a, y5b_sz, y5a_sz, k5)
        if any(v != v for v in vals) or k5 <= 0.0 or k15 <= 0.0:
            d["decision"] = "skipped_xb_missing"; self._emit(d); return None
        # Research sane-5m filter (build_xi4_join): two-sided, in [0.01, 0.99],
        # spread <= 0.15 — a collapsed/decided 5m book is not an opinion.
        if not (0.01 <= y5b <= 0.99 and 0.01 <= y5a <= 0.99 and y5a > y5b
                and (y5a - y5b) <= 0.15):
            d["decision"] = "skipped_xb_unhealthy_5m"; self._emit(d); return None

        gap_bps = (k5 - k15) / k15 * 1e4
        # UP:   K5 above K15 by >= gap AND yes15_ask + premium <= yes5_bid
        #       (P(close>K15) >= P(close>K5) is violated tradeably -> 15m UP cheap).
        # DOWN: K5 below K15 by >= gap AND yes5_ask + premium <= yes15_bid
        #       (equivalent NO-side bound) -> 15m DOWN cheap.
        # Each leg requires >= xb_min_ref_usd notional behind the referenced 5m quote.
        up = (gap_bps >= p.xb_gap_min_bps
              and ya + p.xb_premium <= y5b
              and y5b * y5b_sz >= p.xb_min_ref_usd)
        dn = (gap_bps <= -p.xb_gap_min_bps
              and y5a + p.xb_premium <= yb
              and y5a * y5a_sz >= p.xb_min_ref_usd)
        if not (up or dn):                          # mutually exclusive by gap sign
            self._emit(d); return None
        buy_yes = up
        ask = ya if buy_yes else 1.0 - yb           # research YES-equivalent ask
        if not (p.min_ask <= ask <= p.max_ask):     # XI4 ceiling (0.90); no floor
            self._emit(d); return None

        side = "UP" if buy_yes else "DOWN"
        depth_shares = float(row["yes_ask_depth"]) if buy_yes else float(row["no_ask_depth"])
        if depth_shares * ask < p.fixed_bet_usd:    # not enough top-of-book USD
            d["decision"] = "skipped_no_fill"; self._emit(d); return None

        shares = p.fixed_bet_usd / ask
        hour, dow = utc_hour_dow(ts)
        adverse_vel = -(1.0 if move > 0 else -1.0) * self._roll.vel_bps(10)
        # xb edge = how far through the violated bound the entry is (in price).
        xb_edge = (y5b - ya) if buy_yes else (yb - y5a)
        ctx = {
            "strategy_kind": "determinism", "symbol": symbol_of(self.slug),
            "utc_hour": hour, "dow": dow, "entry_sec": sec, "time_left": time_left,
            "dist_bps": round(abs(move) * 100.0, 2), "fav_side": side,
            "entry_ask": round(ask, 4), "yes_mid": round(ymid, 4),
            "spread_yes": round(float(row["spread_yes"]), 4),
            "ask_depth_usd": round(depth_shares * ask, 1),
            "spot_vel_10s_bps": round(self._roll.vel_bps(10), 2),
            "spot_vel_30s_bps": round(self._roll.vel_bps(30), 2),
            "rvol_60s_bps": round(self._roll.rvol_bps(60), 2),
            "strike_crossings": self._crossings,
            "adverse_vel_10s_bps": round(adverse_vel, 2),
            # xb decision features (post-hoc analysis / weekly review)
            "xb_gap_bps": round(gap_bps, 2), "xb_edge": round(xb_edge, 4),
            "xb_k5": k5, "xb_k15": k15,
            "xb5_yes_bid": round(y5b, 4), "xb5_yes_ask": round(y5a, 4),
            "xb5_ref_usd": round((y5b * y5b_sz) if buy_yes else (y5a * y5a_sz), 1),
            "xb_s5": sec - 600,                     # seconds into the 5m window
        }
        self.pos = {"side": side, "entry": ask, "shares": shares,
                    "bet": p.fixed_bet_usd,
                    "fee_entry": _fee(shares, ask, p.fee_rate),
                    "ts": ts, "entry_sec": sec, "ctx": ctx}
        self.state = "HOLDING"
        portfolio.on_entry(ts)
        if self._guard is not None:
            self._guard.on_entry(ts, self.slug, self.pos["bet"] + self.pos["fee_entry"])
        d["decision"] = "fired"; d["side_signal"] = side
        d["features"] = {"fav_ask": ctx["entry_ask"], "time_left": time_left,
                         "xb_gap_bps": ctx["xb_gap_bps"], "xb_edge": ctx["xb_edge"],
                         "eff_max_ask": round(p.max_ask, 4)}
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
            self._guard.on_settle(ts_ms, self.slug, pnl)
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
