"""Unit tests for sq_rescue pure helpers — synthetic frames only (no repo data)."""
import numpy as np
import pandas as pd
import pytest

from research.analysis.sq_rescue import (
    apply_concurrency_cap, campaign_split, chase_ceiling, deployed_curve_map,
    drift_metric, efficiency_ratio, interp_curve, rolling_curve_map,
    sq_decisions, static_curve_map, trailing_window_move, weighted_cal_error)


def test_campaign_split():
    assert campaign_split("2026-05-23") == "dev"
    assert campaign_split("2026-05-27") == "dev"
    assert campaign_split("2026-05-28") == "holdout"
    assert campaign_split("2026-06-04") == "holdout"
    assert campaign_split("2026-06-05") == "future"
    assert campaign_split("2026-06-09") == "future"


def test_chase_ceiling():
    assert chase_ceiling(0.20) == pytest.approx(0.27)
    assert chase_ceiling(0.90) == pytest.approx(0.95)   # capped at family max
    assert chase_ceiling(0.94) == pytest.approx(0.95)   # never below entry by cap


def test_efficiency_ratio_trend_vs_chop():
    ts = np.arange(0, 200, dtype="i8")
    trend = np.arange(200, dtype="f8")            # pure trend: ER == 1
    zig = np.array([0, 1] * 100, dtype="f8")      # perfect chop: ER ~ 0
    q = np.array([199], dtype="i8")
    er_t = efficiency_ratio(ts, trend, q, window_s=100)
    er_z = efficiency_ratio(ts, zig, q, window_s=100)
    assert er_t[0] == pytest.approx(1.0)
    assert er_z[0] < 0.05


def test_efficiency_ratio_coverage_nan():
    ts = np.array([0, 1, 2, 950, 951], dtype="i8")  # huge gap
    px = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    q = np.array([951], dtype="i8")
    # only 2 points in (951-100, 951] < 0.6*100 -> NaN (fail-open)
    assert np.isnan(efficiency_ratio(ts, px, q, window_s=100)[0])


def test_trailing_window_move():
    ep = np.array([100, 200, 300, 400, 500], dtype="i8")
    mv = np.array([10.0, -20.0, 30.0, -40.0, 50.0])
    q = np.array([500, 450, 300], dtype="i8")
    out = trailing_window_move(ep, mv, q, n_trail=4)
    assert out[0] == pytest.approx(np.mean([10, 20, 30, 40]))   # strictly before 500
    assert out[1] == pytest.approx(np.mean([10, 20, 30, 40]))   # 450 -> same 4 closed
    assert np.isnan(out[2])                                     # only 2 closed before 300


def test_concurrency_cap_priority_and_tiebreak():
    dec = pd.DataFrame({
        "window_start_ts": [1000] * 4 + [2000],
        "entry_sec": [120, 60, 60, 200, 30],
        "symbol": ["xrp", "eth", "btc", "sol", "btc"],
        "slug": list("abcde"),
    })
    out = apply_concurrency_cap(dec, 2)
    grp = out[out["window_start_ts"] == 1000]
    # earliest entry_sec first; tie at 60 -> btc before eth
    assert list(grp["symbol"]) == ["btc", "eth"]
    assert len(out[out["window_start_ts"] == 2000]) == 1
    # C=None passthrough
    assert len(apply_concurrency_cap(dec, None)) == 5


def test_rolling_curve_map_eligibility_cadence_and_fallback():
    rng = np.random.default_rng(0)
    days = [f"2026-05-{d:02d}" for d in range(23, 29)]
    rows = []
    for i, d in enumerate(days):
        n = 300 if d != "2026-05-25" else 10   # day 25 too thin to matter alone
        z = rng.normal(0, 1, n)
        up = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(float)
        rows.append(pd.DataFrame({"date": d, "z": z, "outcome_up_clean": up}))
    band = pd.concat(rows, ignore_index=True)
    m1 = rolling_curve_map(band, days, k=3, cadence=1)
    assert m1[days[0]] is None and m1[days[2]] is None     # no full 3d trail
    assert m1[days[3]] is not None
    # cadence 2: day idx 4 reuses idx-3 curve object
    m2 = rolling_curve_map(band, days, k=3, cadence=2)
    assert m2[days[4]] is m2[days[3]]
    assert m2[days[5]] is not m2[days[3]]
    # min_ticks fallback: absurd threshold -> curve carried as None forever
    m3 = rolling_curve_map(band, days, k=3, cadence=1, min_ticks=10**6)
    assert all(v is None for v in m3.values())


def test_static_and_deployed_curve_maps():
    days = ["2026-06-03", "2026-06-04", "2026-06-05", "2026-06-06"]
    zc_o, p_o = [-1, 0, 1], [0.2, 0.5, 0.8]
    zc_n, p_n = [-1, 0, 1], [0.3, 0.5, 0.7]
    s = static_curve_map(days, zc_o, p_o)
    assert all(np.allclose(s[d][1], p_o) for d in days)
    dm = deployed_curve_map(days, zc_o, p_o, zc_n, p_n, switch_date="2026-06-05")
    assert np.allclose(dm["2026-06-04"][1], p_o)
    assert np.allclose(dm["2026-06-05"][1], p_n)


def test_drift_metric_weighting():
    p_a = np.array([0.2, 0.5, 0.8])
    p_b = np.array([0.2, 0.6, 0.8])
    w_mid = np.array([0.0, 1.0, 0.0])
    assert drift_metric(p_a, p_a, np.ones(3)) == 0.0
    assert drift_metric(p_a, p_b, w_mid) == pytest.approx(0.1)
    assert np.isnan(drift_metric(p_a, p_b, np.zeros(3)))


def test_weighted_cal_error():
    pred = np.array([0.05] * 100 + [0.95] * 100)
    out = np.array([0] * 100 + [1] * 100, dtype=float)   # perfectly calibrated tails
    assert weighted_cal_error(pred, out) == pytest.approx(0.05, abs=1e-9)
    out_bad = np.array([1] * 100 + [0] * 100, dtype=float)
    assert weighted_cal_error(pred, out_bad) == pytest.approx(0.95, abs=1e-9)


def _mini_band():
    """Synthetic 2-slug band exercising every StaleQuoteState gate."""
    cols = dict(symbol="btc", date="2026-05-26", window_start_ts=1000,
                outcome_up_clean=1, realized_vol=0.01, cb_spot=100.0,
                start_price=100.0)
    rows = [
        # slug A tick 1: |mis| big enough BUT jump too small -> skipped
        dict(slug="a", seconds_into_window=100, time_left_sec=800, yes_mid=0.40,
             yes_best_ask=0.45, yes_best_bid=0.38, no_best_ask=0.62,
             yes_ask_depth=100.0, no_ask_depth=100.0, spot_vel_10s_bps=2.0,
             dist_strike_bps=50.0, abs_dist_bps=50.0, z=2.0, **cols),
        # slug A tick 2: qualifies (mis>0 buys YES at 0.45, jump ok, depth ok)
        dict(slug="a", seconds_into_window=120, time_left_sec=780, yes_mid=0.40,
             yes_best_ask=0.45, yes_best_bid=0.38, no_best_ask=0.62,
             yes_ask_depth=100.0, no_ask_depth=100.0, spot_vel_10s_bps=9.0,
             dist_strike_bps=50.0, abs_dist_bps=50.0, z=2.0, **cols),
        # slug A tick 3: also qualifies but later -> ignored (first-tick rule)
        dict(slug="a", seconds_into_window=150, time_left_sec=750, yes_mid=0.40,
             yes_best_ask=0.45, yes_best_bid=0.38, no_best_ask=0.62,
             yes_ask_depth=100.0, no_ask_depth=100.0, spot_vel_10s_bps=9.0,
             dist_strike_bps=50.0, abs_dist_bps=50.0, z=2.0, **cols),
        # slug B: mis<0 (buys NO) but NO-side depth too thin -> skipped
        dict(slug="b", seconds_into_window=130, time_left_sec=770, yes_mid=0.60,
             yes_best_ask=0.65, yes_best_bid=0.55, no_best_ask=0.45,
             yes_ask_depth=100.0, no_ask_depth=1.0, spot_vel_10s_bps=-9.0,
             dist_strike_bps=-50.0, abs_dist_bps=50.0, z=-2.0, **cols),
    ]
    return pd.DataFrame(rows)


def test_sq_decisions_gates_first_tick_and_sides():
    band = _mini_band()
    # curve: z=2 -> p_up=0.9 (mis=+0.5 vs mid 0.40... capped by max_mis=0.6 here)
    cmap = static_curve_map(["2026-05-26"], [-3, 0, 3], [0.05, 0.5, 0.95])
    dec = sq_decisions(band, cmap, margin=0.08, max_mis=0.60, jump_bps=8.0,
                       min_ask=0.05, max_ask=0.95, stake=10.0)
    assert list(dec["slug"]) == ["a"]                  # b fails depth, a fires once
    r = dec.iloc[0]
    assert r["entry_sec"] == 120                       # first QUALIFYING tick, not 100
    assert bool(r["buy_yes"]) is True
    assert r["entry_ask"] == pytest.approx(0.45)
    assert r["csplit"] == "dev"
    # max_dist gate kills everything (dist 50 > 19)
    dec2 = sq_decisions(band, cmap, margin=0.08, max_mis=0.60, jump_bps=8.0,
                        min_ask=0.05, max_ask=0.95, max_dist_bps=19.0, stake=10.0)
    assert dec2.empty
    # ineligible day (curve None) -> no decisions
    dec3 = sq_decisions(band, {"2026-05-26": None}, margin=0.08, max_mis=0.60,
                        jump_bps=8.0, min_ask=0.05, max_ask=0.95, stake=10.0)
    assert dec3.empty


def test_sq_decisions_max_mis_cap():
    band = _mini_band()
    # tighten max_mis below the |mis| of slug a's ticks -> no trades
    cmap = static_curve_map(["2026-05-26"], [-3, 0, 3], [0.05, 0.5, 0.95])
    dec = sq_decisions(band, cmap, margin=0.08, max_mis=0.20, jump_bps=8.0,
                       min_ask=0.05, max_ask=0.95, stake=10.0)
    assert dec.empty


def test_interp_curve_clipping():
    zc = np.array([-1.0, 0.0, 1.0])
    p = np.array([0.2, 0.5, 0.8])
    out = interp_curve(np.array([-9.0, 0.0, 9.0]), zc, p)
    assert out[0] == 0.2 and out[2] == 0.8 and out[1] == 0.5
