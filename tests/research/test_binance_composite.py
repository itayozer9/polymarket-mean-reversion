"""Pure-helper tests for the Binance composite study (no mocking, no network)."""
import numpy as np
import pandas as pd

from research.analysis.binance_composite import (
    asof_at, fit_basis, fit_weight, sign_disagree)
from research.dataset.binance_fetch import (
    aggtrades_to_1s, date_range, day_bounds_ms, klines_to_df)


# --------------------------------------------------------------------------
# asof_at
# --------------------------------------------------------------------------

def test_asof_at_backward_and_tolerance():
    ts = np.array([1000, 2000, 5000], dtype="int64")
    px = np.array([1.0, 2.0, 5.0])
    q = np.array([999, 1000, 1500, 2000, 4999, 5000, 9000], dtype="int64")
    out = asof_at(ts, px, q, tol_ms=2000)
    assert np.isnan(out[0])              # before first obs
    assert out[1] == 1.0                 # exact hit
    assert out[2] == 1.0                 # backward
    assert out[3] == 2.0
    assert np.isnan(out[4])              # 2999ms stale > 2000 tol
    assert out[5] == 5.0
    assert np.isnan(out[6])              # 4000ms stale > tol


def test_asof_at_empty_feed():
    out = asof_at(np.array([], dtype="int64"), np.array([]),
                  np.array([1000], dtype="int64"))
    assert len(out) == 1 and np.isnan(out[0])


# --------------------------------------------------------------------------
# fit_basis / fit_weight
# --------------------------------------------------------------------------

def test_fit_basis_median_ratio_ignores_nan():
    cl = np.array([101.0, 202.0, 303.0, np.nan])
    proxy = np.array([100.0, 200.0, 300.0, 400.0])
    assert abs(fit_basis(cl, proxy) - 1.01) < 1e-12


def test_fit_basis_degenerate_returns_one():
    assert fit_basis(np.array([np.nan]), np.array([1.0])) == 1.0


def test_fit_weight_recovers_planted_weight():
    rng = np.random.default_rng(0)
    n = 4000
    cb = 100.0 + rng.normal(0, 0.05, n)
    bn = cb + rng.normal(0, 0.03, n)             # bn differs from cb
    w_true = 0.7
    cl = w_true * bn + (1 - w_true) * cb + rng.normal(0, 0.001, n)
    scale = np.full(n, 100.0)
    w_hat = fit_weight(cl, bn, cb, scale)
    assert abs(w_hat - w_true) < 0.05


def test_fit_weight_clips_to_unit_interval():
    cb = np.array([100.0, 100.0, 100.0, 100.0])
    bn = np.array([101.0, 99.0, 101.0, 99.0])
    cl = 3.0 * bn - 2.0 * cb                      # implied w=3 -> clip to 1
    w_hat = fit_weight(cl, bn, cb, np.full(4, 100.0))
    assert w_hat == 1.0


def test_fit_weight_degenerate_x_returns_half():
    same = np.array([100.0, 100.0])
    assert fit_weight(np.array([100.0, 100.0]), same, same,
                      np.full(2, 100.0)) == 0.5


# --------------------------------------------------------------------------
# sign_disagree (>= strike == Up, the pipeline's cl_up convention)
# --------------------------------------------------------------------------

def test_sign_disagree_convention():
    close = np.array([101.0, 99.0, 100.0, 99.0])
    strike = np.array([100.0, 100.0, 100.0, 100.0])
    cl_up = np.array([1, 0, 1, 1])
    out = sign_disagree(close, strike, cl_up)
    # 101>=100 up==up ok; 99<100 down==down ok; ==strike counts UP, ok; 99 down vs up -> flip
    assert out.tolist() == [0, 0, 0, 1]


# --------------------------------------------------------------------------
# fetcher pure helpers
# --------------------------------------------------------------------------

def test_klines_to_df_orders_dedupes_and_types():
    raw = [
        [2000, "2.0", "2.1", "1.9", "2.05", "10.0", 2999, "x", 1, "y", "z", "0"],
        [1000, "1.0", "1.1", "0.9", "1.05", "5.0", 1999, "x", 1, "y", "z", "0"],
        [2000, "2.0", "2.1", "1.9", "2.05", "10.0", 2999, "x", 1, "y", "z", "0"],
    ]
    df = klines_to_df(raw)
    assert df["ts_ms"].tolist() == [1000, 2000]
    assert df["close"].tolist() == [1.05, 2.05]
    assert df["ts_ms"].dtype == "int64"
    assert df["volume"].dtype == "float64"


def test_klines_to_df_empty():
    df = klines_to_df([])
    assert list(df.columns) == ["ts_ms", "open", "high", "low", "close", "volume"]
    assert df.empty


def test_aggtrades_to_1s_ohlcv():
    trades = pd.DataFrame({
        "T": [1000, 1400, 1900, 2500],     # two in second 1, one each elsewhere
        "p": ["10.0", "11.0", "9.5", "12.0"],
        "q": ["1.0", "2.0", "1.0", "0.5"],
    })
    out = aggtrades_to_1s(trades)
    assert out["ts_ms"].tolist() == [1000, 2000]
    s1 = out.iloc[0]
    assert (s1["open"], s1["high"], s1["low"], s1["close"]) == (10.0, 11.0, 9.5, 9.5)
    assert s1["volume"] == 4.0
    assert out.iloc[1]["close"] == 12.0


def test_day_bounds_and_date_range():
    t0, t1 = day_bounds_ms("2026-05-22")
    assert t0 == 1779408000000 and t1 - t0 == 86_400_000
    days = date_range("2026-05-30", "2026-06-02")
    assert days == ["2026-05-30", "2026-05-31", "2026-06-01", "2026-06-02"]
