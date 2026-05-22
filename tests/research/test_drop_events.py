"""Unit tests for the Phase 3 drop-event analysis (`research.analysis.drop_events`).

These exercise the load-bearing logic on synthetic ticks: drop detection +
dedupe, the strictly-later forward path (no look-ahead), and the trade
simulation's exit precedence and fee handling.
"""
import numpy as np
import pandas as pd

from research.analysis import drop_events as de


def _window(slug, yes_mid, *, outcome_up=1, sec0=0):
    """Build a one-window tick frame from a YES-mid path. NO mid mirrors it;
    a tight 1c half-spread book is attached so book guards pass."""
    n = len(yes_mid)
    yes_mid = np.asarray(yes_mid, dtype="f8")
    no_mid = 1.0 - yes_mid
    sec = np.arange(sec0, sec0 + n)
    return pd.DataFrame({
        "slug": slug, "symbol": "btc",
        "timestamp_ms": 1_747_000_000_000 + sec * 1000,
        "window_start_ts": 1_747_000_000.0,
        "seconds_into_window": sec.astype(int),
        "time_left_sec": (900 - sec).astype(int),
        "yes_mid": yes_mid, "no_mid": no_mid,
        "yes_best_bid": np.clip(yes_mid - 0.01, 0.001, 0.999),
        "yes_best_ask": np.clip(yes_mid + 0.01, 0.001, 0.999),
        "no_best_bid": np.clip(no_mid - 0.01, 0.001, 0.999),
        "no_best_ask": np.clip(no_mid + 0.01, 0.001, 0.999),
        "spot_move_30s": 0.0, "move_pct": 0.0,
        "outcome_up": float(outcome_up),
        "date": "2026-05-15",
    })


def test_detect_single_drop_event():
    """A flat 0.60 mid that falls to 0.45 is a 25% drop -- one event fires."""
    path = [0.60] * 60 + [0.45] * 60        # 25% fall from the trailing peak
    g = _window("w1", path)
    events = de.detect_drops_one_window(g, "yes", threshold=0.20)
    assert len(events) == 1
    assert events[0]["side"] == "yes"
    # event mid is the dropped level, drop_mag >= threshold
    assert abs(events[0]["event_mid"] - 0.45) < 1e-9
    assert events[0]["drop_mag"] >= 0.20


def test_dedupe_requires_recovery():
    """Two drops in one window only count once unless the side recovers
    (drop falls back below threshold/2) between them."""
    # drop, stay low (no recovery), drop further -- still ONE event.
    nojump = [0.60] * 40 + [0.45] * 40 + [0.40] * 40
    g = _window("w2", nojump)
    assert len(de.detect_drops_one_window(g, "yes", 0.20)) == 1

    # drop, fully recover to peak, drop again -- TWO events.
    recov = [0.60] * 40 + [0.45] * 40 + [0.60] * 40 + [0.45] * 40
    g2 = _window("w3", recov)
    assert len(de.detect_drops_one_window(g2, "yes", 0.20)) == 2


def test_threshold_monotonic_event_counts():
    """A bigger drop threshold can only detect fewer-or-equal events."""
    path = [0.70] * 30 + [0.60] * 30 + [0.50] * 30 + [0.40] * 30
    g = _window("w4", path)
    n10 = len(de.detect_drops_one_window(g, "yes", 0.10))
    n35 = len(de.detect_drops_one_window(g, "yes", 0.35))
    assert n10 >= n35


def test_forward_path_is_strictly_later_no_lookahead():
    """The +Hs forward look must read a tick strictly later than the event."""
    path = [0.60] * 60 + [0.45] + [0.55] * 120   # drop at sec 60, then recover
    dev = _window("w5", path)
    ev = de.detect_all_drops(dev, threshold=0.20)
    assert len(ev) == 1
    fp = de.forward_paths(dev, ev)
    # +15s after the sec-60 event reads the recovered 0.55 level, never 0.45.
    assert abs(fp["mid_15"].iloc[0] - 0.55) < 1e-9
    # window close is the last tick.
    assert abs(fp["mid_close"].iloc[0] - 0.55) < 1e-9


def test_trade_resolution_loss_and_fee_sign():
    """A side that drops and never recovers, resolving worthless, must be a
    near -100% taker trade (lose the stake, pay the entry fee)."""
    # YES drops to 0.30 and the window resolves DOWN (outcome_up=0).
    path = [0.60] * 60 + [0.30] * 200
    dev = _window("w6", path, outcome_up=0)
    ev = de.detect_all_drops(dev, threshold=0.20)
    ev = ev[ev["side"] == "yes"].reset_index(drop=True)
    assert len(ev) == 1
    tr = de.simulate_drop_trade(dev, ev, profit_target=1.00,
                                max_hold=None, execution="taker")
    assert len(tr) == 1
    assert tr["exit_reason"].iloc[0] == "resolution"
    # lost the full stake plus the entry fee -> strictly worse than -$STAKE.
    assert tr["pnl"].iloc[0] <= -de.STAKE


def test_trade_profit_target_exit():
    """If the side rebounds past the target the trade exits at 'target' with
    a positive PnL, and the exit tick is strictly later than entry."""
    # YES drops to 0.40 then climbs to 0.80 (a +100% move on the entry).
    path = [0.60] * 60 + [0.40] * 5 + [0.80] * 120
    dev = _window("w7", path, outcome_up=1)
    ev = de.detect_all_drops(dev, threshold=0.20)
    ev = ev[ev["side"] == "yes"].reset_index(drop=True)
    tr = de.simulate_drop_trade(dev, ev, profit_target=0.25,
                                max_hold=None, execution="maker")
    assert len(tr) == 1
    assert tr["exit_reason"].iloc[0] == "target"
    assert tr["pnl"].iloc[0] > 0
    assert tr["exit_sec"].iloc[0] > tr["sec"].iloc[0]


def test_summarize_empty_is_safe():
    s = de.summarize(pd.DataFrame(), n_days=6)
    assert s["n"] == 0
    assert np.isnan(s["mean_pnl"])
