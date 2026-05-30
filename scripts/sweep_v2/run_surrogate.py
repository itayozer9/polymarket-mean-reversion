"""Stage 8 — Surrogate-guided exploration.

1. Train a LightGBM regressor on all eval rows from Stages 3-7.
2. Sample N synthetic candidates from the random space.
3. Predict cross-fold Sharpe for each; evaluate the top K predictions with the
   real engine.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.sweep_v2 import _runner, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


def configs_to_matrix(configs: List[Dict[str, Any]]) -> pd.DataFrame:
    flat = [param_space.flatten_for_dataframe(c) for c in configs]
    df = pd.DataFrame(flat)
    # All columns arrive as strings after flatten_for_dataframe. Convert.
    for name, kind, _, _, _ in param_space.PARAMS:
        if kind in ("cat", "bool"):
            df[name] = df[name].astype("category").cat.codes
        elif kind in ("float", "int"):
            df[name] = pd.to_numeric(df[name], errors="coerce")
    return df


def build_training_set(rows: List[Dict[str, Any]]) -> tuple:
    eligible = [r for r in rows if r["result"].get("min_fold_n_trades", 0) >= 30]
    if not eligible:
        return None, None
    X = configs_to_matrix([r["config"] for r in eligible])
    y = np.array([r["result"].get("cross_fold_sharpe", 0.0) for r in eligible], dtype="f4")
    return X, y


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-candidates", type=int, default=1000000, help="Synthetic candidates to score.")
    parser.add_argument("--top-k-to-eval", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=46)
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage8_surrogate.jsonl"))
    parser.add_argument("--input-stages", nargs="+",
                        default=[str(SWEEP_DIR / "stage3_lhs.jsonl"),
                                 str(SWEEP_DIR / "stage4_tpe.jsonl"),
                                 str(SWEEP_DIR / "stage5_nsga.jsonl"),
                                 str(SWEEP_DIR / "stage6_cmaes.jsonl"),
                                 str(SWEEP_DIR / "stage7_ga.jsonl")])
    parser.add_argument("--max-workers", type=int, default=None)
    args = parser.parse_args()

    pooled_rows = []
    for path in args.input_stages:
        p = Path(path)
        if p.exists():
            pooled_rows.extend(_runner.read_jsonl(p))
    # Include lifetime store
    lifetime_path = META_DIR / "all_evals_lifetime.parquet"
    if lifetime_path.exists():
        df_lt = pd.read_parquet(lifetime_path)
        for _, row in df_lt.iterrows():
            cfg = param_space.unflatten_from_dataframe(row.to_dict())
            pooled_rows.append({
                "config": cfg,
                "result": {
                    "cross_fold_sharpe": row.get("cross_fold_sharpe", 0.0),
                    "min_fold_n_trades": row.get("min_fold_n_trades", 0),
                },
            })

    if len(pooled_rows) < 50:
        print(f"  Stage 8: only {len(pooled_rows)} prior rows — too few to train surrogate; skipping.")
        _runner.write_jsonl([], Path(args.out))
        return

    X, y = build_training_set(pooled_rows)
    if X is None:
        print("  Stage 8: no eligible prior rows; skipping.")
        _runner.write_jsonl([], Path(args.out))
        return
    print(f"  Stage 8: training LightGBM on {len(X)} rows.")
    model = lgb.LGBMRegressor(
        n_estimators=200, learning_rate=0.05, num_leaves=63,
        min_child_samples=5, n_jobs=-1, verbose=-1, random_state=args.seed,
    )
    model.fit(X, y)

    # Sample candidates and score them
    rng = np.random.default_rng(args.seed)
    print(f"  Stage 8: sampling {args.n_candidates:,} synthetic candidates and scoring.")
    candidates = [param_space.random_sample(rng) for _ in range(args.n_candidates)]
    X_cand = configs_to_matrix(candidates)
    preds = model.predict(X_cand)
    order = np.argsort(preds)[::-1]
    top_idx = order[: args.top_k_to_eval]
    top_cfgs = [candidates[i] for i in top_idx]
    print(f"  Stage 8: top predicted sharpe range: {preds[top_idx].min():.3f} … {preds[top_idx].max():.3f}")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)
    rows = _runner.evaluate_configs(top_cfgs, pool_args, max_workers=args.max_workers, label="Surrogate")
    _runner.write_jsonl(rows, Path(args.out))
    print(f"  Stage 8: wrote {len(rows)} surrogate rows → {args.out}")


if __name__ == "__main__":
    main()
