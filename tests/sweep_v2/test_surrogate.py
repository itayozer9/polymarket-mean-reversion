"""Smoke-test LightGBM surrogate train + predict on synthetic rows."""
from __future__ import annotations

import numpy as np
import pytest

from scripts.sweep_v2 import param_space
from scripts.sweep_v2.run_surrogate import build_training_set, configs_to_matrix


def _row(cfg, sharpe, n_trades_min=50):
    return {"config": cfg, "result": {"cross_fold_sharpe": sharpe,
                                       "min_fold_n_trades": n_trades_min}}


def test_surrogate_trains_on_synthetic_rows():
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(80):
        cfg = param_space.random_sample(rng)
        sharpe = float(rng.normal(0, 1))
        rows.append(_row(cfg, sharpe))
    X, y = build_training_set(rows)
    assert X is not None and y is not None
    assert len(X) == 80
    assert len(y) == 80

    import lightgbm as lgb
    model = lgb.LGBMRegressor(n_estimators=20, num_leaves=7, verbose=-1)
    model.fit(X, y)
    preds = model.predict(X)
    assert preds.shape == y.shape


def test_configs_to_matrix_handles_categoricals():
    rng = np.random.default_rng(2)
    cfgs = [param_space.random_sample(rng) for _ in range(5)]
    X = configs_to_matrix(cfgs)
    assert len(X) == 5
    # No string columns left after encoding
    assert all(X.dtypes.apply(lambda d: d.kind in "ifb"))
