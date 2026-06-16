# tests/research/test_ta_features.py
import numpy as np
import pandas as pd
import pytest
from research.dataset.ta_features import build_ta_features


def _window(slug, prices):
    return pd.DataFrame({
        "slug": slug,
        "seconds_into_window": np.arange(len(prices)),
        "cb_spot": np.asarray(prices, dtype="f8"),
    })


def test_rising_series_is_trend_up():
    # strictly rising spot -> positive EMA slope, RSI high, regime trend
    df = _window("btc-1", 100.0 + np.arange(120) * 0.5)
    out = build_ta_features(df).set_index("seconds_into_window")
    last = out.loc[119]
    assert last["ta_ema_slope"] > 0
    assert last["ta_ma_cross"] == 1
    assert last["ta_rsi"] > 70
    assert last["ta_regime"] == "trend"


def test_flat_series_is_range():
    df = _window("btc-2", np.full(120, 100.0))
    out = build_ta_features(df).set_index("seconds_into_window")
    last = out.loc[119]
    assert abs(last["ta_ema_slope"]) < 1e-6
    assert abs(last["ta_z_vwap"]) < 1e-6
    assert last["ta_boll_width"] < 1.0
    assert last["ta_ma_cross"] == 0   # equal EMAs -> sign 0 (documented tie/flat)
    assert last["ta_regime"] == "range"


def test_highvol_regime_is_reachable():
    # Large alternating jumps keep abs(ta_z_vwap) < 1.5 (mean-reverting around a
    # level, no sustained drift) but push ta_boll_width >= 8.0 bps -> highvol.
    n = 120
    base = 100.0
    amp = 0.6  # ~60 bps swing; tuned so std/mean*1e4 clears the 8.0 bps gate
    prices = base + amp * (np.arange(n) % 2) - amp / 2.0
    out = build_ta_features(_window("hv-1", prices))
    assert (out["ta_regime"] == "highvol").any()


def test_features_are_causal():
    # a feature value at sec t must NOT change when later rows are appended
    full = _window("btc-3", 100.0 + np.sin(np.arange(120) / 5.0))
    truncated = full.iloc[:60].copy()
    a = build_ta_features(full).set_index("seconds_into_window").loc[59]
    b = build_ta_features(truncated).set_index("seconds_into_window").loc[59]
    for col in ["ta_ema_slope", "ta_rsi", "ta_macd_hist", "ta_z_vwap", "ta_atr"]:
        assert a[col] == pytest.approx(b[col], rel=1e-9, nan_ok=True), col


def test_per_slug_isolation():
    # two slugs in one frame must not bleed across the groupby boundary
    df = pd.concat([
        _window("a", 100.0 + np.arange(80) * 0.5),     # rising
        _window("b", 100.0 - np.arange(80) * 0.5),     # falling
    ], ignore_index=True)
    out = build_ta_features(df)
    a = out[out["slug"] == "a"].set_index("seconds_into_window").loc[79]
    b = out[out["slug"] == "b"].set_index("seconds_into_window").loc[79]
    assert a["ta_ma_cross"] == 1 and b["ta_ma_cross"] == -1
