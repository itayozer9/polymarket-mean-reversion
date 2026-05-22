"""Tests for the patient-trader policy simulator (Task 16).

The simulator is a clean re-implementation of the user's manual policy:
buy the cheap side after a visible odds drop, hold patiently, take profit
on a bounce, exit near breakeven if it recovers, otherwise hold to
resolution. No stop-loss. One trade per window. $10 fixed stake.
"""
import pandas as pd

from research.analysis.patient_policy import PatientPolicy, simulate_window


def _win(prices, outcome_up, drop=50.0, sigma=0.4):
    """One window, cheap side = YES, mids given by `prices`."""
    n = len(prices)
    return pd.DataFrame({
        "slug": ["w"] * n,
        "seconds_into_window": list(range(n)),
        "time_left_sec": [900 - i for i in range(n)],
        "cheap_side": ["YES"] * n,
        "cheap_mid": prices,
        "cheap_ask": [p + 0.01 for p in prices],
        "cheap_bid": [p - 0.01 for p in prices],
        "cheap_drop_30s": [drop] * n,
        "sigma_proximity": [sigma] * n,
        "proximity_pct": [0.1] * n,
        "outcome_up": [float(outcome_up)] * n,
    })


def test_profit_target_exit_maker():
    # enters ~0.20, rises to 0.40 -> +100% -> profit target 50% fires
    w = _win([0.20, 0.25, 0.30, 0.40], outcome_up=1)
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=50.0, breakeven_exit=True, execution="maker")
    tr = simulate_window(w, pol)
    assert tr is not None and tr["exit_reason"] == "profit_target"
    assert tr["pnl_usd"] > 0


def test_resolution_loss_taker():
    # enters ~0.20, never rises, window resolves against -> -100%-ish
    w = _win([0.20, 0.18, 0.15, 0.10], outcome_up=0)
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=50.0, breakeven_exit=True, execution="taker")
    tr = simulate_window(w, pol)
    assert tr is not None and tr["exit_reason"] == "resolution"
    assert tr["pnl_usd"] < 0


def test_no_entry_when_band_excludes():
    w = _win([0.50, 0.52, 0.55, 0.60], outcome_up=1)  # mid above band
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=50.0, breakeven_exit=True, execution="maker")
    assert simulate_window(w, pol) is None


def test_no_entry_when_drop_too_small():
    # mid in band but no visible drop -> no entry
    w = _win([0.20, 0.21, 0.22, 0.23], outcome_up=1, drop=0.0)
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=50.0, breakeven_exit=True, execution="maker")
    assert simulate_window(w, pol) is None


def test_no_entry_when_too_little_time_left():
    # mid in band, visible drop, but every tick is too late in the window
    n = 5
    w = pd.DataFrame({
        "slug": ["w"] * n,
        "seconds_into_window": list(range(880, 880 + n)),
        "time_left_sec": [20 - i for i in range(n)],
        "cheap_side": ["YES"] * n,
        "cheap_mid": [0.20] * n,
        "cheap_ask": [0.21] * n,
        "cheap_bid": [0.19] * n,
        "cheap_drop_30s": [50.0] * n,
        "sigma_proximity": [0.4] * n,
        "proximity_pct": [0.1] * n,
        "outcome_up": [1.0] * n,
    })
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=420,
                        profit_target_pct=50.0, breakeven_exit=True, execution="maker")
    assert simulate_window(w, pol) is None


def test_breakeven_exit_after_underwater():
    # enters ~0.20, dips to 0.12 (underwater), recovers to ~0.20 -> breakeven exit
    w = _win([0.20, 0.12, 0.14, 0.20, 0.21], outcome_up=0)
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=200.0, breakeven_exit=True, execution="maker")
    tr = simulate_window(w, pol)
    assert tr is not None and tr["exit_reason"] == "breakeven"
    # maker breakeven exit at ~entry price -> pnl near zero
    assert abs(tr["pnl_usd"]) < 1.0


def test_breakeven_off_holds_to_resolution():
    # same path as above but breakeven_exit off -> holds to resolution (loses)
    w = _win([0.20, 0.12, 0.14, 0.20, 0.21], outcome_up=0)
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=200.0, breakeven_exit=False, execution="maker")
    tr = simulate_window(w, pol)
    assert tr is not None and tr["exit_reason"] == "resolution"
    assert tr["pnl_usd"] < 0


def test_resolution_win_when_held_side_wins():
    # enters ~0.20, never hits target, but window resolves in favour -> win
    w = _win([0.20, 0.22, 0.24, 0.26], outcome_up=1)
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=200.0, breakeven_exit=False, execution="maker")
    tr = simulate_window(w, pol)
    assert tr is not None and tr["exit_reason"] == "resolution"
    assert tr["won"] is True
    assert tr["pnl_usd"] > 0


def test_taker_pnl_le_maker_pnl_same_path():
    # cost is non-negative: taker net <= maker net for an identical winning path
    w = _win([0.20, 0.25, 0.30, 0.40], outcome_up=1)
    base = dict(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                max_sigma_proximity=1.0, min_time_left_sec=60,
                profit_target_pct=50.0, breakeven_exit=True)
    tr_t = simulate_window(w, PatientPolicy(execution="taker", **base))
    tr_m = simulate_window(w, PatientPolicy(execution="maker", **base))
    assert tr_t["pnl_usd"] <= tr_m["pnl_usd"]


def test_one_trade_per_window():
    # a long oscillating path still yields exactly one trade dict (or None)
    w = _win([0.20, 0.40, 0.20, 0.40, 0.20, 0.40], outcome_up=1)
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=50.0, breakeven_exit=True, execution="maker")
    tr = simulate_window(w, pol)
    assert isinstance(tr, dict)
    # exits at the first profit-target crossing, not the last
    assert tr["exit_reason"] == "profit_target"
    assert tr["seconds_held"] == 1
