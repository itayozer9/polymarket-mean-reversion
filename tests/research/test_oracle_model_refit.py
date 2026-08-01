"""Unit tests for oracle_model_refit pure pieces — synthetic data only (no repo
artifacts), no mocking: real sklearn fits, real tmp-file IO, real engine loader."""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.analysis.oracle_model_refit import (
    FEATURES, build_artifact, backup_path, bump_version, crossings, derive_tau,
    fit_logistic, gate_decision, maintained_schedule, predict_params, refit_split,
    trailing_days, validate_artifact, weighted_abs_shift, write_with_backup)

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# version / windowing
# ---------------------------------------------------------------------------
def test_bump_version():
    assert bump_version("op-1") == "op-2"
    assert bump_version("op-9") == "op-10"
    assert bump_version("v2.3") == "v2.4"
    assert bump_version("weird") == "weird-r2"
    assert bump_version("") == "op-2"


def test_trailing_days():
    days = [f"2026-06-{d:02d}" for d in range(1, 10)]
    assert trailing_days(days, 3) == days[-3:]
    assert trailing_days(days, 99) == days          # shorter frame -> all of it
    with pytest.raises(ValueError):
        trailing_days(days, 0)


def test_refit_split_window_and_holdout():
    days = [f"2026-05-{d:02d}" for d in range(10, 30)]   # 20 days
    train, hold = refit_split(days, window=14, holdout=2)
    assert hold == days[-2:]
    assert train == days[-14:-2]                    # 12 train days inside the window
    assert len(train) == 12
    # short frame: window truncates to what exists
    train2, hold2 = refit_split(days[:6], window=14, holdout=2)
    assert train2 == days[:4] and hold2 == days[4:6]
    with pytest.raises(ValueError):
        refit_split(days[:4], window=14, holdout=2, min_train=3)


def test_maintained_schedule_mirrors_refit_pipeline():
    days = [f"2026-05-{d:02d}" for d in range(1, 21)]    # 20 days
    sched = maintained_schedule(days, window=14, holdout=2, cadence=7, min_days=5)
    # nothing in use before the first refit lands (refit end-of-day idx 4 ->
    # effect from idx 5)
    assert all(sched[d] is None for d in days[:5])
    # first refit trains on refit_split(days[:5]) = first 3 days
    assert sched[days[5]] == tuple(days[:3])
    # cadence: the SAME tuple object is in use until the next refit (idx 11)
    assert sched[days[11]] is sched[days[5]]
    # second refit (end of idx 11) trains on trailing 12 days minus 2 holdout
    assert sched[days[12]] == tuple(refit_split(days[:12], window=14, holdout=2)[0])
    # third refit end of idx 18 -> in use on idx 19, window now truncates to 14
    assert sched[days[19]] == tuple(refit_split(days[:19], window=14, holdout=2)[0])
    assert len(sched[days[19]]) == 12


# ---------------------------------------------------------------------------
# alarm math
# ---------------------------------------------------------------------------
def test_weighted_abs_shift_values_and_nan_exclusion():
    s, n = weighted_abs_shift([0.2, 0.5, 0.8], [0.2, 0.6, 0.7])
    assert s == pytest.approx((0.0 + 0.1 + 0.1) / 3)
    assert n == 3
    # NaN on either side drops the pair (fail-closed rows don't dilute the stat)
    s2, n2 = weighted_abs_shift([0.2, np.nan, 0.8], [0.2, 0.6, np.nan])
    assert (s2, n2) == (0.0, 1)
    s3, n3 = weighted_abs_shift([np.nan], [0.5])
    assert np.isnan(s3) and n3 == 0


def test_derive_tau_and_crossings():
    maint = {"d1": 0.010, "d2": 0.025, "d3": 0.018}
    tau = derive_tau(maint)
    assert tau == pytest.approx(0.025)
    # zero false alarms on the historical window: nothing strictly below tau fires
    assert crossings({d: v for d, v in maint.items() if v < tau}, tau) == []
    inc = {"d1": 0.012, "d2": 0.025, "d3": 0.061, "d4": float("nan")}
    assert crossings(inc, tau) == ["d2", "d3"]      # >= tau fires, NaN never
    assert derive_tau({"d": float("nan")}) != derive_tau({"d": 1.0})
    assert crossings(inc, float("nan")) == []


def test_gate_decision():
    ok, _ = gate_decision(0.130, 0.131)             # candidate better
    assert ok
    ok, _ = gate_decision(0.131, 0.131)             # equal
    assert ok
    ok, _ = gate_decision(0.1325, 0.131, tol=0.002)  # worse but within tol
    assert ok
    ok, why = gate_decision(0.134, 0.131, tol=0.002)  # worse beyond tol
    assert not ok and "0.13400" in why
    ok, why = gate_decision(float("nan"), 0.131)    # fail closed
    assert not ok and "fail closed" in why
    ok, why = gate_decision(0.120, 0.131, n=10, min_n=100)  # thin holdout
    assert not ok and "thin" in why


# ---------------------------------------------------------------------------
# fit / predict / artifact — parity with the research consumer path
# ---------------------------------------------------------------------------
def _synthetic_frame(n=20_000, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "cl_dist_bps": rng.normal(0, 20, n),
        "cb_dist_bps": rng.normal(0, 20, n),
        "cl_cb_basis_bps": rng.normal(0, 3, n),
        "cl_oracle_age_s": rng.uniform(0, 60, n),
        "time_left_sec": rng.uniform(60, 600, n),
        "realized_vol": np.abs(rng.normal(0.005, 0.003, n)) + 1e-4,
        "symbol": rng.choice(["btc", "eth", "sol", "xrp"], n),
    })


def _incumbent_stub():
    return {
        "version": "op-1", "fitted_at": "2026-06-10T18:27:20+00:00",
        "features": list(FEATURES),
        "mu": [0.0] * len(FEATURES), "sd": [1.0] * len(FEATURES),
        "coef": [0.1] * len(FEATURES), "intercept": 0.0,
        "isotonic": None, "iso_adopted": False,
        "metrics": {"dev": {"brier_raw": 0.1316}}, "n_dev": 1, "n_holdout": 1,
        "gate": {"theta": 0.575, "selected_on": "dev"},
    }


def test_fit_predict_parity_with_research_p_settle():
    """The artifact built from fit_logistic must evaluate IDENTICALLY through the
    research consumer (oracle_print_model.p_settle) — same standardization, same
    sigmoid, NaN fail-closed where features are invalid."""
    from research.analysis.oracle_print_model import design_matrix, p_settle

    df = _synthetic_frame()
    X, valid = design_matrix(df)
    assert valid.all()
    rng = np.random.default_rng(1)
    w_true = np.array([0.9, 0.2, 1.5, 0.1, -0.5, 0.05, -0.1, 0.0, -0.1])
    y = (rng.random(len(df)) < 1 / (1 + np.exp(-(X @ w_true)))).astype(float)

    fit = fit_logistic(X, y)
    art = build_artifact(fit, _incumbent_stub(), ["2026-06-01", "2026-06-12"],
                         ["2026-06-13", "2026-06-14"],
                         {"passed": True}, n_train=len(df))
    # poison a few rows -> p_settle must fail closed exactly there
    df2 = df.copy()
    df2.loc[df2.index[:7], "cl_oracle_age_s"] = np.nan
    p_res = p_settle(df2, model=art)
    assert np.isnan(p_res[:7]).all()
    X2, valid2 = design_matrix(df2)
    p_mine = predict_params(art, X2[valid2])
    assert np.allclose(p_res[valid2], p_mine, atol=1e-12)
    # the fit actually learned the synthetic signal (direction, not noise)
    p_true = 1 / (1 + np.exp(-(X @ w_true)))
    assert np.corrcoef(p_mine, p_true[valid2])[0, 1] > 0.95


def test_build_artifact_metadata_and_carry():
    fit = dict(mu=np.zeros(9), sd=np.ones(9), coef=np.full(9, 0.2), intercept=-0.1)
    inc = _incumbent_stub()
    art = build_artifact(fit, inc, ["2026-05-27", "2026-06-07"],
                         ["2026-06-08", "2026-06-09"],
                         {"passed": True, "brier_incumbent": 0.131},
                         n_train=123_456, now_iso="2026-06-12T10:00:00+00:00")
    assert art["version"] == "op-2"
    assert art["features"] == list(FEATURES)
    assert art["iso_adopted"] is False and art["isotonic"] is None
    assert art["n_train"] == 123_456
    r = art["refit"]
    assert r["refit_of"] == {"version": "op-1",
                             "fitted_at": "2026-06-10T18:27:20+00:00"}
    assert r["train_days"] == ["2026-05-27", "2026-06-07"]
    assert r["holdout_days"] == ["2026-06-08", "2026-06-09"]
    assert r["gate"]["passed"] is True
    # unknown keys carried verbatim; stale fit metadata is NOT carried
    assert art["gate"] == inc["gate"] and "gate" in r["carried_keys"]
    assert "metrics" not in art and "n_dev" not in art and "n_holdout" not in art
    assert "restart" in r["deployment_note"]


def test_validate_artifact_rejects_bad_artifacts():
    fit = dict(mu=np.zeros(9), sd=np.ones(9), coef=np.full(9, 0.2), intercept=-0.1)
    good = build_artifact(fit, _incumbent_stub(), ["a", "b"], ["c"], {}, 10)
    validate_artifact(good)                          # passes
    bad = dict(good, features=good["features"][:-1] + ["coin_doge"])
    with pytest.raises(ValueError, match="drifted"):
        validate_artifact(bad)
    with pytest.raises(ValueError, match="wrong length"):
        validate_artifact(dict(good, mu=[0.0] * 3))
    with pytest.raises(ValueError, match="non-finite"):
        validate_artifact(dict(good, coef=[np.nan] * 9))
    with pytest.raises(ValueError, match="positive"):
        validate_artifact(dict(good, sd=[0.0] * 9))
    with pytest.raises(ValueError, match="isotonic"):
        validate_artifact(dict(good, iso_adopted=True))


# ---------------------------------------------------------------------------
# backup + write (real files in tmp_path)
# ---------------------------------------------------------------------------
def test_write_with_backup_preserves_incumbent_and_never_overwrites(tmp_path):
    path = tmp_path / "oracle_print_model.json"
    inc = _incumbent_stub()
    path.write_text(json.dumps(inc, indent=2))
    fit = dict(mu=np.zeros(9), sd=np.ones(9), coef=np.full(9, 0.2), intercept=-0.1)
    art = build_artifact(fit, inc, ["a", "b"], ["c"], {"passed": True}, 10)

    bp1 = write_with_backup(str(path), art)
    assert json.loads(path.read_text())["version"] == "op-2"
    assert json.loads(Path(bp1).read_text()) == inc          # incumbent preserved
    assert "op-1" in os.path.basename(bp1) and "20260610T182720Z" in bp1

    # second refit: distinct backup name, first backup untouched
    art2 = build_artifact(fit, json.loads(path.read_text()), ["a", "b"], ["c"],
                          {"passed": True}, 10)
    bp2 = write_with_backup(str(path), art2)
    assert bp2 != bp1 and Path(bp1).exists() and Path(bp2).exists()
    assert json.loads(path.read_text())["version"] == "op-3"
    assert json.loads(Path(bp2).read_text())["version"] == "op-2"


def test_backup_path_uniquifies(tmp_path):
    p = tmp_path / "m.json"
    b1 = backup_path(str(p), "op-1", "2026-06-10T18:27:20+00:00")
    Path(b1).write_text("x")
    b2 = backup_path(str(p), "op-1", "2026-06-10T18:27:20+00:00")
    assert b2 != b1 and b2.startswith(b1)
    # garbage fitted_at still yields a usable path
    assert "unknown" in backup_path(str(p), "op-1", None)


# ---------------------------------------------------------------------------
# the registered safety net: the REAL engine loader accepts our artifacts and
# fail-fasts on feature drift (read-only import of src/)
# ---------------------------------------------------------------------------
def test_engine_loader_roundtrip_and_feature_drift_failfast(tmp_path):
    sys.path.insert(0, str(REPO / "src"))
    from mean_reversion_live.engine.print_model import load_print_model

    fit = dict(mu=np.zeros(9), sd=np.ones(9), coef=np.full(9, 0.2), intercept=-0.1)
    art = build_artifact(fit, _incumbent_stub(), ["a", "b"], ["c"],
                         {"passed": True}, 10)
    p = tmp_path / "art.json"
    p.write_text(json.dumps(art))
    m = load_print_model(p)                          # must not raise
    assert m.version == "op-2" and m.iso_adopted is False

    drifted = dict(art, features=art["features"][:-1] + ["coin_doge"])
    p2 = tmp_path / "drift.json"
    p2.write_text(json.dumps(drifted))
    with pytest.raises(ValueError, match="drifted"):
        load_print_model(p2)
