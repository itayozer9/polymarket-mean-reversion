"""Unit tests for the pure helpers of the external-inputs study (XI1-XI5)."""
import numpy as np
import pandas as pd

from research.analysis.external_inputs import (
    asof_state, joint_cascade_ts, minute_bars, monotonic_signal, relabel_splits,
    robust_z, tag_between, tag_events, tag_recent, jaccard)
from research.dataset.binance_deriv_fetch import funding_to_df, oi_to_df


# ---------------------------------------------------------------- fetchers
def test_funding_to_df_parses_sorts_dedupes():
    rows = [
        {"fundingTime": 2000, "fundingRate": "0.0001", "markPrice": "100.5"},
        {"fundingTime": 1000, "fundingRate": "-0.0002", "markPrice": "99.0"},
        {"fundingTime": 2000, "fundingRate": "0.0001", "markPrice": "100.5"},
    ]
    df = funding_to_df(rows)
    assert list(df["funding_time_ms"]) == [1000, 2000]
    assert df["funding_rate"].iloc[0] == -0.0002
    assert funding_to_df([]).empty


def test_oi_to_df_parses():
    rows = [
        {"timestamp": 300000, "sumOpenInterest": "10.5", "sumOpenInterestValue": "1000"},
        {"timestamp": 0, "sumOpenInterest": "10.0", "sumOpenInterestValue": "990"},
    ]
    df = oi_to_df(rows)
    assert list(df["ts_ms"]) == [0, 300000]
    assert df["sum_open_interest"].iloc[1] == 10.5


# ---------------------------------------------------------------- bars / z
def test_minute_bars_aggregates_ohlcv():
    df = pd.DataFrame({
        "ts_ms": [0, 1000, 60_000, 61_000],
        "high": [10.0, 12.0, 9.0, 11.0],
        "low": [9.0, 10.0, 8.0, 9.5],
        "close": [10.0, 11.0, 8.5, 10.0],
        "volume": [1.0, 2.0, 3.0, 4.0],
    })
    m = minute_bars(df)
    assert list(m["minute_ts"]) == [0, 60]
    assert m["vol"].tolist() == [3.0, 7.0]
    # minute 0: high 12, low 9, close 11 -> rng_bps = 3/11*1e4
    assert np.isclose(m["rng_bps"].iloc[0], 3.0 / 11.0 * 1e4)


def test_robust_z_is_causal_and_flags_spike():
    x = pd.Series([1.0] * 130 + [50.0] + [1.0] * 5)
    z = robust_z(x, window=120, min_periods=60)
    assert z[:60].isna().all()          # warmup
    assert z.iloc[130] > 5               # spike flagged
    # causality: changing the LAST value must not change earlier z
    x2 = x.copy()
    x2.iloc[-1] = 1e9
    z2 = robust_z(x2, window=120, min_periods=60)
    assert np.allclose(z.iloc[60:-1].fillna(0), z2.iloc[60:-1].fillna(0))


def test_robust_z_constant_series_is_nan_not_inf():
    z = robust_z(pd.Series([2.0] * 200))
    assert not np.isinf(z.fillna(0)).any()


# ---------------------------------------------------------------- taggers
def test_tag_recent_window_edges():
    ev = np.array([100.0, 500.0])
    entries = np.array([99.0, 100.0, 280.0, 281.0, 500.0, 700.0])
    out = tag_recent(entries, ev, lookback_s=180)
    assert out.tolist() == [False, True, True, False, True, False]
    assert tag_recent(entries, np.array([]), 180).tolist() == [False] * 6


def test_tag_between_inclusive():
    ev = np.array([100.0, 200.0])
    entries = np.array([150.0, 250.0, 99.0])
    lows = np.array([100.0, 201.0, 0.0])
    assert tag_between(entries, lows, ev).tolist() == [True, False, False]


def test_asof_state_staleness():
    ts = np.array([100.0, 200.0])
    val = np.array([1.0, 2.0])
    out = asof_state(np.array([50.0, 150.0, 200.0, 900.0]), ts, val, max_stale_s=600)
    assert np.isnan(out[0])
    assert out[1] == 1.0 and out[2] == 2.0
    assert np.isnan(out[3])              # 700s stale > 600


def test_tag_events_and_tiers():
    cal = pd.DataFrame({
        "tier": [1, 2],
        "window_start_epoch_s": [1000, 5000],
        "window_end_epoch_s": [1900, 5900],
    })
    e = np.array([999.0, 1000.0, 1900.0, 1901.0, 5500.0])
    assert tag_events(e, cal).tolist() == [False, True, True, False, True]
    assert tag_events(e, cal, tier_max=1).tolist() == [False, True, True, False, False]


# ---------------------------------------------------------------- splits
def test_relabel_splits_override():
    df = pd.DataFrame({
        "date": ["2026-06-01", "2026-06-05", "2026-05-25"],
        "split": ["future", "future", "dev"],
    })
    out = relabel_splits(df, "2026-06-05")
    assert out["split"].tolist() == ["holdout", "future", "dev"]
    assert df["split"].tolist() == ["future", "future", "dev"]  # original intact


# ---------------------------------------------------------------- XI4 rule
def _xi4_frame(**over):
    base = dict(gap_bps=[10.0], yes_best_ask=[0.50], yes_best_bid=[0.48],
                yes5_bid=[0.60], yes5_ask=[0.62], yes5_bid_usd=[5.0],
                yes5_ask_usd=[5.0])
    base.update({k: [v] for k, v in over.items()})
    return pd.DataFrame(base)


def test_monotonic_signal_up_violation():
    sig = monotonic_signal(_xi4_frame(), margin=0.05, gap_bps=2.0)
    assert len(sig) == 1 and bool(sig["buy_yes"].iloc[0])
    assert sig["entry_ask"].iloc[0] == 0.50


def test_monotonic_signal_down_violation():
    # K5 below K15; 5m says DOWN strongly (yes5_ask low) vs 15m bid still high
    sig = monotonic_signal(
        _xi4_frame(gap_bps=-10.0, yes5_ask=0.30, yes5_bid=0.28,
                   yes_best_bid=0.40, yes_best_ask=0.42),
        margin=0.05, gap_bps=2.0)
    assert len(sig) == 1 and not bool(sig["buy_yes"].iloc[0])
    assert np.isclose(sig["entry_ask"].iloc[0], 0.60)   # 1 - yes15_bid


def test_monotonic_signal_respects_margin_gap_and_depth():
    assert monotonic_signal(_xi4_frame(), margin=0.15, gap_bps=2.0).empty
    assert monotonic_signal(_xi4_frame(gap_bps=1.0), margin=0.05, gap_bps=2.0).empty
    assert monotonic_signal(_xi4_frame(yes5_bid_usd=0.5), margin=0.05, gap_bps=2.0).empty


def test_jaccard():
    assert jaccard({"a", "b"}, {"b", "c"}) == round(1 / 3, 3)
    assert jaccard(set(), {"a"}) == 0.0


def test_joint_cascade_ts_requires_k_simultaneous_hot_coins():
    # 4 coins x 3 minutes; baseline z=0; minute 120: 3 coins spike; minute 180: 1.
    rows = []
    for sym in ("btc", "eth", "sol", "xrp"):
        for mt, (zv, zr) in {60: (-1.0, -1.0),
                             120: (9.0, 9.0) if sym != "btc" else (-1.0, -1.0),
                             180: (9.0, 9.0) if sym == "btc" else (-1.0, -1.0)}.items():
            rows.append(dict(symbol=sym, minute_ts=mt, vol_z=zv, rng_z=zr,
                             date="2026-05-25"))
    casc = pd.DataFrame(rows)
    out = joint_cascade_ts(casc, k=3, qv=0.9, qr=0.9,
                           dh_lo="2026-05-23", dh_hi="2026-06-04")
    # q0.9 thresholds sit between baseline (-1) and spike (9); only minute 120
    # has >=3 simultaneously-hot coins (eth/sol/xrp)
    assert out.tolist() == [120.0]
