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


def test_hard_worstcase_open_exposure_blocks_across_windows():
    # One shared guard across windows; in hard_worstcase mode the cap reserves each
    # OPEN position's worst-case loss, so concurrent open exposure blocks new entries
    # even though nothing has settled yet (the leak the redesign fixes).
    g = DailyLossGuard(max_daily_loss_usd=30.0, mode="hard_worstcase")
    pf, rng = _pf(), np.random.default_rng(0)
    states = [DeterminismState(f"btc-updown-15m-{i}", DetParams(), 900, guard=g)
              for i in range(3)]
    for s in states:
        s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16,
                       ts=1_000_000), pf, rng)
    # ~$10.1 reserved per open position: first two fit under $30, the third blocks
    assert states[0].state == "HOLDING"
    assert states[1].state == "HOLDING"
    assert states[2].state == "FLAT"        # blocked by open-exposure reservation
    assert pf.open_positions == 2


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


# ── v2 loss-avoidance gates (docs/research/loss_pattern_filters.md) ──

def test_adverse_velocity_blocks_entry():
    """Spot reverting toward strike fast (adverse_vel>2) must skip; same series
    with the gate OFF (None) must enter — proves the gate is what blocks."""
    def run(cap):
        s, pf, rng = (DeterminismState("btc-updown-15m-1", DetParams(adverse_vel_max_bps=cap), 900),
                      _pf(), np.random.default_rng(0))
        # spot 20bps above strike, then falls to 10bps over ~15s (vel ~ -10bps/10s)
        s.on_tick(_row(835, move_pct=0.20, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)  # tl=65>60
        s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
        return s.state
    assert run(None) == "HOLDING"    # gate off: enters
    assert run(2.0) == "FLAT"        # adverse ~+10bps > 2 -> blocked


def test_strike_crossings_required_blocks_zero_crossing():
    p = DetParams(min_strike_crossings=1)
    s, pf, rng = DeterminismState("btc-updown-15m-1", p, 900), _pf(), np.random.default_rng(0)
    # spot stays above strike the whole window -> 0 crossings
    s.on_tick(_row(835, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)  # tl=65>60
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
    assert s.state == "FLAT" and pf.open_positions == 0


def test_strike_crossings_satisfied_enters():
    p = DetParams(min_strike_crossings=1)
    s, pf, rng = DeterminismState("btc-updown-15m-1", p, 900), _pf(), np.random.default_rng(0)
    s.on_tick(_row(820, move_pct=-0.05, yes_mid=0.40, yes_ask=0.85, no_ask=0.16), pf, rng)  # below strike
    s.on_tick(_row(830, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)   # crosses up -> 1
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)   # enters
    assert s.state == "HOLDING" and pf.open_positions == 1
    assert s._crossings >= 1


def test_v2_defaults_are_noop():
    # default params (no v2 fields) must still enter on the canonical setup
    s, pf, rng = _state(), _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
    assert s.state == "HOLDING"


# ── favourite-value gates (2026-06-05 Chainlink edge hunt) ──

def test_vol_max_blocks_high_vol():
    """rvol_bps = std(move_pct diffs)*100. A flat series (rvol 0) enters; a
    0.10/0.40/0.10 swing (rvol ~30bps) is blocked at vol_max_bps=1.0; gate off enters."""
    def run(vol_max, moves):
        s = DeterminismState("btc-updown-15m-1", DetParams(vol_max_bps=vol_max), 900)
        pf, rng = _pf(), np.random.default_rng(0)
        for sec, mv in zip((810, 825, 840), moves):   # 840 -> time_left=60, entry tick
            s.on_tick(_row(sec, move_pct=mv, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
        return s.state
    assert run(1.0, (0.10, 0.10, 0.10)) == "HOLDING"   # rvol 0 <= 1 -> enters
    assert run(1.0, (0.10, 0.40, 0.10)) == "FLAT"       # rvol ~30bps > 1 -> blocked
    assert run(None, (0.10, 0.40, 0.10)) == "HOLDING"   # gate off -> enters (proves the gate)


def test_restrict_fav_side_blocks_wrong_favourite():
    """restrict_fav_side='no' enters ONLY when NO is the favourite (deep DOWN-fav edge)."""
    def run(restrict, yes_mid, yes_ask, no_ask, move):
        s = DeterminismState("btc-updown-15m-1",
                             DetParams(restrict_fav_side=restrict, dist_min_bps=0.0,
                                       min_ask=0.05, max_ask=0.95), 900)
        pf, rng = _pf(), np.random.default_rng(0)
        s.on_tick(_row(850, move_pct=move, yes_mid=yes_mid, yes_ask=yes_ask, no_ask=no_ask), pf, rng)
        return s.state
    assert run("no", 0.85, 0.85, 0.16, 0.10) == "FLAT"      # YES-fav, restrict NO -> skip
    assert run("no", 0.15, 0.16, 0.85, -0.10) == "HOLDING"  # NO-fav consistent -> enter (buy NO)
    assert run(None, 0.85, 0.85, 0.16, 0.10) == "HOLDING"   # no restriction -> YES-fav enters


# ── dual-oracle gate (2026-06-09): require Chainlink to agree with Coinbase at entry ──
# on_tick reads `cl_dist_bps` from the tick struct (ws_collector live / dual_oracle backtest).
# These tests build a cl-extended struct row; a bare _row (no cl field) reads as NaN = "missing".
_EXT_DTYPE = np.dtype(TICK_DTYPE.descr + [("cl_dist_bps", "f8")])


def _row_cl(sec, *, move_pct, yes_mid, yes_ask, no_ask, cl_dist_bps,
            ts=1_000_000, depth=1000.0):
    a = np.zeros(1, dtype=_EXT_DTYPE)
    a["timestamp_ms"][0] = ts
    a["seconds_into_window"][0] = sec
    a["move_pct"][0] = move_pct
    a["yes_mid"][0] = yes_mid
    a["yes_best_ask"][0] = yes_ask
    a["no_best_ask"][0] = no_ask
    a["yes_best_bid"][0] = max(0.01, yes_ask - 0.02)
    a["spread_yes"][0] = 0.02
    a["yes_ask_depth"][0] = depth
    a["no_ask_depth"][0] = depth
    a["cl_dist_bps"][0] = cl_dist_bps
    return a[0]


def _run_gate(params, row):
    decisions = []
    s = DeterminismState("btc-updown-15m-1", params, 900, observer=decisions.append)
    s.on_tick(row, _pf(), np.random.default_rng(0))
    return s.state, decisions[-1]["decision"]


def test_oracle_gate_none_is_noop_even_when_chainlink_disagrees():
    # gate OFF (default): a tick whose Chainlink disagrees (cl_dist<0 vs move>0) still enters.
    st, _ = _run_gate(DetParams(),
                      _row_cl(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16,
                              cl_dist_bps=-15.0))
    assert st == "HOLDING"


def test_oracle_gate_agree_blocks_disagreement():
    # buy YES (move>0) but Chainlink says DOWN (cl_dist<0) -> disagree -> skip.
    st, dec = _run_gate(DetParams(oracle_gate="agree"),
                        _row_cl(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16,
                                cl_dist_bps=-15.0))
    assert st == "FLAT" and dec == "skipped_oracle_disagree"


def test_oracle_gate_agree_enters_on_agreement():
    st, _ = _run_gate(DetParams(oracle_gate="agree"),
                      _row_cl(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16,
                              cl_dist_bps=15.0))
    assert st == "HOLDING"


def test_oracle_gate_dist_threshold():
    # "dist" mode: require |cl_dist| >= cl_dist_min_bps regardless of agreement.
    p = DetParams(oracle_gate="dist", cl_dist_min_bps=10.0)
    st_lo, dec = _run_gate(p, _row_cl(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85,
                                      no_ask=0.16, cl_dist_bps=5.0))
    assert st_lo == "FLAT" and dec == "skipped_cl_dist"
    st_hi, _ = _run_gate(p, _row_cl(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85,
                                    no_ask=0.16, cl_dist_bps=15.0))
    assert st_hi == "HOLDING"


def test_oracle_gate_missing_chainlink_fail_closed_vs_allow():
    # bare _row has no cl_dist_bps field -> read as NaN = Chainlink unavailable.
    miss = _row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16)
    st_skip, dec = _run_gate(DetParams(oracle_gate="agree", oracle_gate_on_missing="skip"), miss)
    assert st_skip == "FLAT" and dec == "skipped_oracle_missing"
    st_allow, _ = _run_gate(DetParams(oracle_gate="agree", oracle_gate_on_missing="allow"), miss)
    assert st_allow == "HOLDING"   # allow -> trade ungated when Chainlink absent


# ── dynamic (adaptive) max_ask: raise the ceiling only on a deep Chainlink lock ──

def test_dynamic_max_ask_raises_ceiling_on_deep_lock():
    # fav_ask 0.82 is above the base max_ask 0.78 but below max_ask_hi 0.85.
    p = lambda: DetParams(max_ask=0.78, max_ask_hi=0.85, cl_dist_hi_bps=20.0, dist_min_bps=5.0)
    def run(cld):
        return _run_gate(p(), _row_cl(850, move_pct=0.30, yes_mid=0.82, yes_ask=0.82,
                                      no_ask=0.19, cl_dist_bps=cld))[0]
    assert run(25.0) == "HOLDING"   # deep lock |cl_dist|>=20 -> ceiling 0.85 -> 0.82 enters
    assert run(10.0) == "FLAT"      # shallow lock -> ceiling stays 0.78 -> 0.82 rejected


def test_dynamic_max_ask_noop_when_unset():
    # without max_ask_hi, 0.82 is always rejected at the base 0.78 (even on a deep lock).
    st, _ = _run_gate(DetParams(max_ask=0.78, dist_min_bps=5.0),
                      _row_cl(850, move_pct=0.30, yes_mid=0.82, yes_ask=0.82,
                              no_ask=0.19, cl_dist_bps=25.0))
    assert st == "FLAT"


def test_dynamic_max_ask_missing_chainlink_uses_base_ceiling():
    # Chainlink missing (bare _row) -> conservative base ceiling 0.78 -> 0.82 rejected.
    st, _ = _run_gate(DetParams(max_ask=0.78, max_ask_hi=0.85, cl_dist_hi_bps=20.0, dist_min_bps=5.0),
                      _row(850, move_pct=0.30, yes_mid=0.82, yes_ask=0.82, no_ask=0.19))
    assert st == "FLAT"
