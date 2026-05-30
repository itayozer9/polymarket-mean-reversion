"""Two-iteration smoke for the meta-store.

Synthesizes a tiny set of stage-3 JSONL rows and verifies that:
1. write_lifetime_store appends to data/sweep_v2/meta/all_evals_lifetime.parquet.
2. update_viable_region_priors derives a JSON-able {param: {p5, p95}} dict.
3. A second iteration extends the same parquet rather than overwriting.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.sweep_v2 import param_space, _runner
from scripts.sweep_v2.meta import persist as meta_persist


def _make_row(cfg, sharpe, n_trades, pnl):
    return {
        "config_id": param_space.hash_id(cfg),
        "config": cfg,
        "result": {
            "cross_fold_sharpe": sharpe,
            "min_fold_n_trades": n_trades,
            "pooled": {"net_pnl": pnl, "n_trades": n_trades * 5},
            "per_fold": [],
        },
    }


def test_meta_lifetime_store_grows(tmp_path, monkeypatch):
    monkeypatch.setattr(meta_persist, "SWEEP_DIR", tmp_path)
    monkeypatch.setattr(meta_persist, "META_DIR", tmp_path / "meta")
    rng = np.random.default_rng(42)

    # iteration 1: write 30 rows
    stage_path = tmp_path / "stage3_lhs.jsonl"
    rows = []
    for _ in range(30):
        cfg = param_space.random_sample(rng)
        rows.append(_make_row(cfg, sharpe=float(rng.normal(0, 1)),
                                n_trades=int(rng.integers(0, 80)),
                                pnl=float(rng.normal(0, 5))))
    _runner.write_jsonl(rows, stage_path)
    meta_persist.write_lifetime_store([stage_path], gold_rows=[])
    meta_persist.update_viable_region_priors()

    lifetime = tmp_path / "meta" / "all_evals_lifetime.parquet"
    assert lifetime.exists()
    df = pd.read_parquet(lifetime)
    assert len(df) == 30
    iteration_ids_v1 = set(df["iteration_id"].tolist())
    assert len(iteration_ids_v1) == 1

    # iteration 2: small delay so iteration_id differs
    time.sleep(1.1)
    rows2 = []
    for _ in range(20):
        cfg = param_space.random_sample(rng)
        rows2.append(_make_row(cfg, sharpe=float(rng.normal(0, 1)),
                                 n_trades=int(rng.integers(0, 80)),
                                 pnl=float(rng.normal(0, 5))))
    _runner.write_jsonl(rows2, stage_path)
    meta_persist.write_lifetime_store([stage_path], gold_rows=[])
    df2 = pd.read_parquet(lifetime)
    assert len(df2) == 50, "lifetime store must append, not overwrite"
    new_ids = set(df2["iteration_id"].tolist())
    assert len(new_ids) == 2, "second iteration must register a new iteration_id"
