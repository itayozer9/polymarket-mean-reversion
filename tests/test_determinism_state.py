"""Unit tests for the late-window determinism strategy (Phase 1 edge, live)."""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mean_reversion_live.adapters.arb_imports import TICK_DTYPE, Portfolio, HumanParams  # noqa: E402
from mean_reversion_live.engine.determinism_state import (  # noqa: E402
    DetParams, DeterminismState, DailyLossGuard,
)


def _row(sec, *, move_pct, yes_mid, yes_ask, no_ask, ts=1_000_000, depth=1000.0):
    a = np.zeros(1, dtype=TICK_DTYPE)
    a["timestamp_ms"][0] = ts
    a["seconds_into_window"][0] = sec
    a["move_pct"][0] = move_pct
    a["yes_mid"][0] = yes_mid
    a["yes_best_ask"][0] = yes_ask
    a["no_best_ask"][0] = no_ask
    # Healthy two-sided YES book (the strategy's book-health guard requires it).
    a["yes_best_bid"][0] = max(0.01, yes_ask - 0.02)
    a["spread_yes"][0] = 0.02
    a["yes_ask_depth"][0] = depth
    a["no_ask_depth"][0] = depth
    return a[0]


def _pf():
    return Portfolio(human=HumanParams(fixed_bet_usd=10.0, daily_trade_cap=None,
                                       concurrent_position_cap=50,
                                       post_loss_cooldown_sec=0), bankroll=1000.0)


def _state():
    return DeterminismState("btc-updown-15m-1", DetParams(), window_duration_sec=900)


def test_no_entry_outside_window():
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    # time_left = 900-600 = 300 > 60 -> no entry
    assert s.on_tick(_row(600, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16),
                     pf, rng) is None
    assert s.state == "FLAT" and pf.open_positions == 0


def test_enter_then_settle_true_win():
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    # last 60s, dist=10bps, fav=yes ask 0.85<=0.90, consistent (move>0) -> enter
    assert s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16),
                     pf, rng) is None
    assert s.state == "HOLDING" and pf.open_positions == 1
    # a later tick must NOT self-settle (we hold to true resolution)
    assert s.on_tick(_row(899, move_pct=0.12, yes_mid=0.99, yes_ask=0.99, no_ask=0.02),
                     pf, rng) is None
    assert s.state == "HOLDING"
    # engine settles at the true outcome (Up) -> exit 1.0
    tr = s.settle(outcome_up=True, ts_ms=1_000_900, portfolio=pf)
    assert tr is not None and tr.side == "UP" and tr.exit_reason == "resolution"
    shares = 10.0 / 0.85
    fee_entry = shares * 0.07 * 0.85 * 0.15
    assert tr.pnl == pytest.approx(shares * 1.0 - 10.0 - fee_entry, abs=1e-6)
    assert tr.pnl > 0 and pf.open_positions == 0 and pf.n_trades == 1


def test_enter_then_settle_true_loss():
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
    # true outcome Down -> UP loses, exit 0.0
    tr = s.settle(outcome_up=False, ts_ms=1_000_900, portfolio=pf)
    assert tr is not None and tr.pnl < 0
    shares = 10.0 / 0.85
    fee_entry = shares * 0.07 * 0.85 * 0.15
    assert tr.pnl == pytest.approx(-10.0 - fee_entry, abs=1e-6)


def test_settle_noop_when_flat():
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    assert s.settle(outcome_up=True, ts_ms=1, portfolio=pf) is None


def test_daily_loss_guard_blocks_after_cap():
    g = DailyLossGuard(max_daily_loss_usd=15.0)
    assert not g.blocked(1_000_000_000_000)
    g.record(1_000_000_000_000, -10.0)
    assert not g.blocked(1_000_000_000_000)   # -10 > -15
    g.record(1_000_000_000_000, -8.0)         # cumulative -18 <= -15
    assert g.blocked(1_000_000_000_000)
    # next UTC day resets (advance ~2 days in ms)
    assert not g.blocked(1_000_000_000_000 + 2 * 86_400_000)


def test_guard_stops_new_entries_strategy_wide():
    g = DailyLossGuard(max_daily_loss_usd=5.0)
    g.record(1_000_000, -6.0)   # already over cap today
    s = DeterminismState("btc-updown-15m-1", DetParams(), 900, guard=g)
    pf, rng = _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16, ts=1_000_000),
              pf, rng)
    assert s.state == "FLAT" and pf.open_positions == 0   # blocked by daily-loss cap


def test_no_entry_when_inconsistent():
    # favourite is YES but spot is BELOW strike (move<0) -> spot contradicts -> skip
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=-0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
    assert s.state == "FLAT" and pf.open_positions == 0


def test_no_entry_ask_too_high():
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.95, yes_ask=0.95, no_ask=0.06), pf, rng)
    assert s.state == "FLAT"


def test_no_entry_dist_too_small():
    # dist = 0.02*100 = 2 bps < 5 -> skip
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=0.02, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
    assert s.state == "FLAT"


def test_one_trade_per_window():
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
    s.settle(outcome_up=True, ts_ms=1_000_900, portfolio=pf)
    # after settling, a later (defensive) tick must not re-enter
    assert s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16),
                     pf, rng) is None
    assert pf.n_trades == 1


def test_skip_when_depth_too_thin():
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    # depth_shares * ask = 5 * 0.85 = 4.25 < 10 -> no fill
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16, depth=5.0),
              pf, rng)
    assert s.state == "FLAT"
