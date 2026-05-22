import numpy as np
from research.features.core import (
    corrected_proximity_pct, realized_vol_per_sec, sigma_proximity,
)

def test_corrected_proximity_is_percent():
    # move_pct already in percent; proximity must equal |move_pct|
    mp = np.array([0.0, 0.5, -1.2, 3.0], dtype="f8")
    out = corrected_proximity_pct(mp)
    assert np.allclose(out, [0.0, 0.5, 1.2, 3.0])

def test_realized_vol_per_sec_zero_for_flat():
    mp = np.zeros(120, dtype="f8")
    out = realized_vol_per_sec(mp, window=60)
    assert np.allclose(out, 0.0)

def test_realized_vol_per_sec_positive_for_moving():
    rng = np.random.default_rng(0)
    mp = np.cumsum(rng.normal(0, 0.05, 300))
    out = realized_vol_per_sec(mp, window=60)
    assert out[-1] > 0.0

def test_sigma_proximity_small_when_far_in_time():
    # 0.5% from strike, but lots of time and vol -> few sigmas away
    mp = np.full(10, 0.5, dtype="f8")
    vol_per_sec = np.full(10, 0.05, dtype="f8")   # 0.05%/s
    time_left = np.full(10, 400, dtype="f8")       # 400s left
    out = sigma_proximity(mp, vol_per_sec, time_left)
    # sigma_remaining = 0.05*sqrt(400) = 1.0%  -> 0.5/1.0 = 0.5 sigma
    assert np.allclose(out, 0.5, atol=1e-6)

def test_sigma_proximity_large_when_little_time():
    mp = np.full(10, 0.5, dtype="f8")
    vol_per_sec = np.full(10, 0.05, dtype="f8")
    time_left = np.full(10, 4, dtype="f8")         # 4s left
    out = sigma_proximity(mp, vol_per_sec, time_left)
    # sigma_remaining = 0.05*2 = 0.1% -> 0.5/0.1 = 5 sigma (decided)
    assert np.allclose(out, 5.0, atol=1e-6)

from research.features.core import (
    rolling_drop_pct, odds_velocity, book_imbalance, spot_move_pct,
)

def test_rolling_drop_pct_detects_drop():
    # mid rises to 0.40 then falls to 0.20 -> 50% drop from window peak
    mid = np.array([0.30, 0.40, 0.35, 0.20], dtype="f8")
    out = rolling_drop_pct(mid, window_sec=10)
    assert np.isclose(out[-1], 50.0, atol=1e-6)
    assert out[0] == 0.0

def test_odds_velocity_sign():
    mid = np.array([0.30, 0.28, 0.25, 0.25], dtype="f8")
    out = odds_velocity(mid, window_sec=2)
    assert out[2] < 0.0   # falling
    assert out[0] == 0.0

def test_book_imbalance_bounds():
    bid = np.array([100.0, 0.0, 50.0], dtype="f8")
    ask = np.array([100.0, 0.0, 0.0], dtype="f8")
    out = book_imbalance(bid, ask)
    assert np.isclose(out[0], 0.5)
    assert np.isclose(out[1], 0.5)   # both zero -> neutral
    assert np.isclose(out[2], 1.0)

def test_spot_move_pct_signed_change():
    # move_pct is the spot's signed distance from strike, in percent.
    # spot_move_pct is the change in that over the trailing window.
    mp = np.array([0.00, 0.10, 0.30, 0.25], dtype="f8")
    out = spot_move_pct(mp, window_sec=2)
    assert out[0] == 0.0
    assert np.isclose(out[2], 0.30)   # spot moved +0.30% over 2 ticks
    assert np.isclose(out[3], 0.15)   # 0.25 - 0.10
