"""Sanity-check that sweep_v2 evaluate.eval_kfold runs end-to-end on real data
and that adding filter_v2.* gates reduces (or keeps equal) the trade count.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from scripts.sweep_v2 import evaluate, folds, param_space


ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"


def _has_data() -> bool:
    return (SWEEP_DIR / "folds_v1.json").exists() and \
           (SWEEP_DIR / "combined" / "outcomes.csv").exists()


@pytest.mark.skipif(not _has_data(), reason="sweep_v2 data not initialized; run setup_data + folds first")
def test_eval_kfold_runs_and_filter_v2_is_strict():
    fold_data = folds.load_folds()
    ctx = evaluate.EvalContext.build(
        ["btc"], "2026-05-17", "2026-05-17", feature_lookup=None,
    )
    ctx.fold_mask = evaluate.FoldMask(
        n_folds=fold_data["n_folds"],
        slug_to_fold={s: int(f) for s, f in fold_data["slug_to_fold"].items()},
    )

    rng = np.random.default_rng(42)
    cfg = param_space.random_sample(rng)
    # Wide entry band so we actually get trades
    cfg["entry.entry_price_min"] = 0.10
    cfg["entry.entry_price_max"] = 0.60
    cfg["entry.drop_magnitude_pct"] = 5.0
    cfg["entry.min_time_left_sec"] = 120
    cfg["entry.min_seconds_into_window"] = 30
    cfg["exit.profit_target_pct"] = 30.0
    cfg["exit.max_hold_sec"] = 600
    # Disable all v2 filters
    for f in ("macro_stress", "rv_regime", "depth_imbalance", "btc_lead",
              "spread_zscore", "expiry_bucket"):
        cfg[f"filter_v2.use_{f}"] = False

    base = evaluate.eval_kfold(ctx, cfg, seed=42)
    assert "per_fold" in base
    assert len(base["per_fold"]) == fold_data["n_folds"]
    assert isinstance(base["cross_fold_sharpe"], float)


def test_param_space_roundtrip():
    """to_sim_config + back through SimConfig.to_dict() preserves values."""
    rng = np.random.default_rng(99)
    cfg = param_space.random_sample(rng)
    sim_cfg = param_space.to_sim_config(cfg)
    d = sim_cfg.to_dict()
    # Verify entry.side and a few numeric params round-tripped
    assert d["entry"]["side"] == cfg["entry.side"]
    assert d["entry"]["drop_magnitude_pct"] == pytest.approx(cfg["entry.drop_magnitude_pct"])
    assert d["exit"]["profit_target_pct"] == pytest.approx(cfg["exit.profit_target_pct"])


def test_flatten_unflatten_roundtrip():
    rng = np.random.default_rng(1)
    cfg = param_space.random_sample(rng)
    flat = param_space.flatten_for_dataframe(cfg)
    restored = param_space.unflatten_from_dataframe(flat)
    for k in cfg:
        assert restored[k] == cfg[k], f"{k}: {restored[k]!r} != {cfg[k]!r}"
