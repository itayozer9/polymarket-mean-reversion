"""Unit tests for the pure helpers in research/analysis/oracle_print_model.py.

Repo style: no mocks — real small frames/arrays, exact hand-computed expectations.
"""
import numpy as np
import pandas as pd
import pytest

from research.analysis.oracle_print_model import (
    SIGMA_FLOOR_BPS, Z_CLIP, agree_leaf_fit, agree_leaf_predict, asof_price_age,
    brier, clustered_bootstrap_fast, dedupe_rounds, design_matrix, diffusion_z,
    iso_apply, jaccard, logloss, sigma_bps_per_sec, split_round_id)
from research.lib.stats import window_clustered_bootstrap


def test_split_round_id_real_value():
    # observed btc round: phase 3, aggregator round 3585859
    phase, agg = split_round_id(55340232221132240707)
    assert phase == 3
    assert agg == 3585859


def test_dedupe_rounds_first_observation_and_phase_split():
    big = (3 << 64) | 100  # > uint64 — must survive as string
    polls = pd.DataFrame({
        "symbol": ["btc", "btc", "btc", "eth"],
        "timestamp_ms": [2000, 1000, 3000, 1500],
        "round_id": [str(big), str(big), str(big + 1), str((3 << 64) | 7)],
        "updated_at": [10.0, 10.0, 25.0, 12.0],
        "price": [100.0, 100.0, 101.0, 5.0],
    })
    r = dedupe_rounds(polls)
    assert len(r) == 3
    btc = r[r["symbol"] == "btc"].reset_index(drop=True)
    # first observation (min poll ts) is kept
    assert btc.loc[0, "first_seen_ms"] == 1000
    assert list(btc["agg_round"]) == [100, 101]
    assert (btc["phase"] == 3).all()
    # sorted by updated_at within symbol
    assert list(btc["updated_at"]) == [10.0, 25.0]


def test_sigma_floor_and_units():
    # realized_vol is in percent units -> 0.01% = 1 bps/s
    out = sigma_bps_per_sec([0.01, 0.0, np.nan])
    assert out[0] == pytest.approx(1.0)
    assert out[1] == SIGMA_FLOOR_BPS          # floored at the pre-registered floor
    assert np.isnan(out[2])                   # NaN propagates (fail-closed)


def test_diffusion_z_scaling_and_clip():
    # 10 bps, sigma 1 bps/s, T=100s -> z = 10 / (1*10) = 1
    assert diffusion_z(10.0, 1.0, 100.0) == pytest.approx(1.0)
    # huge distance clips at +-Z_CLIP
    assert diffusion_z(1e9, 1.0, 100.0) == Z_CLIP
    assert diffusion_z(-1e9, 1.0, 100.0) == -Z_CLIP
    # T floored at 1s
    assert diffusion_z(2.0, 1.0, 0.0) == pytest.approx(2.0)


def test_design_matrix_hand_row():
    df = pd.DataFrame({
        "cl_dist_bps": [10.0, 5.0],
        "cb_dist_bps": [20.0, np.nan],     # second row invalid (NaN feature)
        "cl_cb_basis_bps": [100.0, 0.0],   # clips at 5 (in 10-bps units)
        "cl_oracle_age_s": [240.0, 10.0],  # caps at 120 -> age factor 2.0
        "time_left_sec": [100.0, 100.0],
        "realized_vol": [0.01, 0.01],      # sigma = 1 bps/s
        "symbol": ["sol", "btc"],
    })
    X, valid = design_matrix(df)
    assert valid.tolist() == [True, False]
    z_cl, z_cb = 1.0, 2.0
    row = X[0]
    assert row[0] == pytest.approx(z_cl)
    assert row[1] == pytest.approx(z_cb)
    assert row[2] == pytest.approx(1.0)        # d_cl = 10/10
    assert row[3] == pytest.approx(2.0)        # d_cb = 20/10
    assert row[4] == pytest.approx(5.0)        # basis clipped
    assert row[5] == pytest.approx(2.0 * (z_cb - z_cl))  # age_gap
    assert row[6:9].tolist() == [0.0, 1.0, 0.0]          # sol one-hot


def test_brier_and_logloss_known_values():
    assert brier([1.0, 0.0], [1, 0]) == 0.0
    assert brier([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    assert logloss([0.5, 0.5], [1, 0]) == pytest.approx(np.log(2.0))


def test_fast_bootstrap_matches_canonical():
    rng = np.random.default_rng(7)
    vals = rng.normal(size=200)
    groups = np.array([f"w{i % 17}" for i in range(200)])
    fast = clustered_bootstrap_fast(vals, groups, n=300, seed=3)
    slow = window_clustered_bootstrap(vals, groups, n=300, seed=3)
    assert np.allclose(fast, slow, atol=1e-9)


def test_agree_leaf_fit_predict():
    # 4 ticks: 2 agree (cb side correct twice -> p_agree=1), 2 disagree (cb side
    # correct once -> p_disagree=0.5)
    cb = [5.0, -5.0, 5.0, -5.0]
    cl = [5.0, -5.0, -5.0, 5.0]
    y = [1, 0, 1, 1]
    leaves = agree_leaf_fit(cb, cl, y)
    assert leaves["p_agree"] == pytest.approx(1.0)
    assert leaves["p_disagree"] == pytest.approx(0.5)
    p = agree_leaf_predict(cb, cl, leaves)
    assert p[0] == pytest.approx(1.0)    # agree, cb up -> P(up)=1
    assert p[1] == pytest.approx(0.0)    # agree, cb down -> P(up)=0
    assert p[2] == pytest.approx(0.5)
    assert p[3] == pytest.approx(0.5)


def test_jaccard():
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(set(), set()) == 0.0
    assert jaccard(["a"], ["a"]) == 1.0


def test_iso_apply_matches_sklearn():
    from sklearn.isotonic import IsotonicRegression
    rng = np.random.default_rng(0)
    p = rng.uniform(size=300)
    y = (rng.uniform(size=300) < p ** 1.3).astype(float)  # mis-calibrated
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p, y)
    q = rng.uniform(-0.1, 1.1, size=50)  # includes out-of-range -> clip behaviour
    got = iso_apply(q, iso.X_thresholds_, iso.y_thresholds_)
    want = iso.predict(np.clip(q, 0, 1))
    assert np.allclose(got, want, atol=1e-9)


def test_asof_price_age():
    upd = np.array([10.0, 20.0, 30.0])
    px = np.array([1.0, 2.0, 3.0])
    price, age = asof_price_age(upd, px, np.array([5.0, 10.0, 25.0, 99.0]))
    assert np.isnan(price[0]) and np.isnan(age[0])     # before first update
    assert price[1] == 1.0 and age[1] == 0.0
    assert price[2] == 2.0 and age[2] == 5.0
    assert price[3] == 3.0 and age[3] == 69.0
