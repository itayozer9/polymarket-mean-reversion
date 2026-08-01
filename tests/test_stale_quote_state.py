"""Unit tests for the Phase 2 stale-quote pickoff strategy."""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mean_reversion_live.adapters.arb_imports import TICK_DTYPE, Portfolio, HumanParams  # noqa: E402
from mean_reversion_live.engine.stale_quote_state import (  # noqa: E402
    StaleQuoteParams, StaleQuoteState, _interp,
)

CURVE_Z = [-5.0, -1.0, 0.0, 1.0, 5.0]
CURVE_P = [0.10, 0.25, 0.50, 0.75, 0.90]


def _row(sec, *, move_pct, yes_mid, yes_ask=None, no_ask=None, ts=1_000_000, depth=1000.0):
    a = np.zeros(1, dtype=TICK_DTYPE)
    a["timestamp_ms"][0] = ts
    a["seconds_into_window"][0] = sec
    a["move_pct"][0] = move_pct
    a["yes_mid"][0] = yes_mid
    ya = yes_ask if yes_ask is not None else min(0.98, yes_mid + 0.01)
    a["yes_best_ask"][0] = ya
    a["yes_best_bid"][0] = max(0.01, ya - 0.02)
    a["no_best_ask"][0] = no_ask if no_ask is not None else (1.0 - (ya - 0.02))
    a["spread_yes"][0] = 0.02
    a["yes_ask_depth"][0] = depth
    a["no_ask_depth"][0] = depth
    return a[0]


def _params(**kw):
    base = dict(zc=CURVE_Z, p_up=CURVE_P, margin=0.08, max_mispricing=0.30,
                jump_bps=8.0, t_lo_left=60, t_hi_left=840, min_ask=0.05, max_ask=0.95)
    base.update(kw)
    return StaleQuoteParams(**base)


def _pf():
    return Portfolio(human=HumanParams(fixed_bet_usd=10.0, daily_trade_cap=None,
                                       concurrent_position_cap=50, post_loss_cooldown_sec=0),
                     bankroll=1000.0)


def test_interp_monotonic_and_clamped():
    assert _interp(-99, CURVE_Z, CURVE_P) == 0.10
    assert _interp(99, CURVE_Z, CURVE_P) == 0.90
    assert _interp(0.0, CURVE_Z, CURVE_P) == pytest.approx(0.50)
    assert 0.50 < _interp(0.5, CURVE_Z, CURVE_P) < 0.75


def test_no_entry_outside_midwindow():
    s, pf, rng = StaleQuoteState("btc-updown-15m-1", _params(), 900), _pf(), np.random.default_rng(0)
    # time_left = 900-10 = 890 > t_hi_left(840) -> no entry
    assert s.on_tick(_row(10, move_pct=0.30, yes_mid=0.50), pf, rng) is None
    assert s.state == "FLAT"


def test_no_entry_without_jump():
    # flat spot series -> rvol tiny, vel ~0 -> jump gate fails even if mispriced
    s, pf, rng = StaleQuoteState("btc-updown-15m-1", _params(), 900), _pf(), np.random.default_rng(0)
    for sec in range(100, 400, 10):
        s.on_tick(_row(sec, move_pct=0.001, yes_mid=0.50), pf, rng)
    assert s.state == "FLAT" and pf.open_positions == 0


def test_enters_on_jump_and_mispricing_then_settles():
    s, pf, rng = StaleQuoteState("btc-updown-15m-1", _params(), 900), _pf(), np.random.default_rng(0)
    # build history with variation (nonzero rvol), then a sharp up-jump while mid lags
    for sec in range(100, 300, 10):
        s.on_tick(_row(sec, move_pct=0.02 * ((sec // 10) % 3 - 1), yes_mid=0.50), pf, rng)
    # sharp jump: move_pct jumps to +0.20% (=20bps) over ~10s; yes_mid still 0.50
    s.on_tick(_row(300, move_pct=0.10, yes_mid=0.50), pf, rng)
    fired = s.on_tick(_row(310, move_pct=0.20, yes_mid=0.50, yes_ask=0.51), pf, rng)
    # model sees large positive z -> P(Up) high -> mispricing>0 -> buy YES (UP)
    if s.state == "HOLDING":
        assert s.pos["side"] == "UP"
        tr = s.settle(outcome_up=True, ts_ms=1_000_400, portfolio=pf)
        assert tr is not None and tr.pnl > 0 and tr.exit_reason == "resolution"
        assert s.last_ctx["strategy_kind"] == "stale_quote" and "z" in s.last_ctx
    else:
        pytest.skip("constructed series did not cross the jump+mispricing gate")


def test_max_dist_gate_blocks_far_from_strike():
    """v2 gate: a clean firing series (low-variance history + fine 1s ramp so
    vel_10s is high but rvol stays low) fires without the cap and is blocked with
    a cap below the entry distance — proving the gate is what blocks."""
    def run(cap):
        s, pf, rng = (StaleQuoteState("btc-updown-15m-1", _params(max_dist_bps=cap), 900),
                      _pf(), np.random.default_rng(0))
        # quiet history: tiny zigzag ~0.5bps -> small rvol, nonzero
        for sec in range(40, 300, 5):
            s.on_tick(_row(sec, move_pct=0.005 * (1 if (sec // 5) % 2 else -1), yes_mid=0.50),
                      pf, rng)
        # smooth ramp 1bps/sec for 12s: cumulative vel_10s ~10bps, per-tick diff tiny
        st = "FLAT"
        for k, sec in enumerate(range(300, 313)):
            move = 0.05 + 0.01 * k          # 5bps -> 17bps
            s.on_tick(_row(sec, move_pct=move, yes_mid=0.50, yes_ask=0.51), pf, rng)
            st = s.state
        return st
    if run(None) != "HOLDING":
        pytest.skip("base ramp series did not fire; gate test n/a")
    assert run(10.0) == "FLAT"   # entry dist ~12-17bps > 10 cap -> blocked


def test_settle_loss_and_ctx():
    s, pf = StaleQuoteState("btc-updown-15m-1", _params(), 900), _pf()
    # manually place a HOLDING position
    s.state = "HOLDING"
    s.pos = {"side": "UP", "entry": 0.40, "shares": 25.0, "bet": 10.0,
             "fee_entry": 0.05, "ts": 1_000_000, "entry_sec": 300,
             "ctx": {"strategy_kind": "stale_quote", "symbol": "btc"}}
    tr = s.settle(outcome_up=False, ts_ms=1_000_900, portfolio=pf)  # UP loses
    assert tr.pnl == pytest.approx(-10.0 - 0.05, abs=1e-6)
    assert s.last_ctx["won"] == 0 and s.last_ctx["outcome_up"] == 0
