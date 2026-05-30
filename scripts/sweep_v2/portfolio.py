"""Stage 14 — Portfolio diversification + sensitivity (SHAP) analysis.

For final survivors:
1. Compute pairwise daily-PnL correlations; cluster at ρ≥0.7; pick one
   representative per cluster.
2. Train a LightGBM surrogate on all eval rows; compute SHAP-derived feature
   importance to identify what drives cross-fold Sharpe (informs next iteration).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.sweep_v2 import _runner, evaluate, folds as folds_mod, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"


def daily_pnl_vector(cfg: Dict[str, Any], ctx, dates: List[str]) -> np.ndarray:
    """Compute one PnL per date for a config (used as the correlation feature)."""
    out = []
    for d in dates:
        slugs = _slugs_for_date(ROOT / "data" / "outcomes.csv", d, ctx.symbols)
        if not slugs:
            out.append(0.0)
            continue
        res = evaluate.eval_on_slugs(ctx, cfg, slugs, seed=42)
        out.append(res["aggregate"]["net_pnl"])
    return np.array(out, dtype="f8")


def _slugs_for_date(outcomes_path: Path, date: str, symbols: List[str]) -> set:
    df = pd.read_csv(outcomes_path)
    df = df.drop_duplicates("market_slug")
    df = df[df["market_slug"].str.contains("-updown-15m-", na=False)]
    df = df[df["symbol"].isin(symbols)]
    df["dt"] = pd.to_datetime(df["window_start_ts"], unit="s", utc=True)
    d0 = pd.Timestamp(date, tz="UTC")
    d1 = d0 + pd.Timedelta(days=1)
    df = df[(df["dt"] >= d0) & (df["dt"] < d1)]
    return set(df["market_slug"].astype(str))


def cluster_by_correlation(vectors: List[np.ndarray], threshold: float = 0.7) -> List[int]:
    """Return cluster assignments (greedy by row order)."""
    n = len(vectors)
    if n == 0:
        return []
    labels = [-1] * n
    next_id = 0
    for i in range(n):
        if labels[i] != -1:
            continue
        labels[i] = next_id
        for j in range(i + 1, n):
            if labels[j] != -1:
                continue
            v1, v2 = vectors[i], vectors[j]
            if np.std(v1) == 0 or np.std(v2) == 0:
                continue
            corr = float(np.corrcoef(v1, v2)[0, 1])
            if corr >= threshold:
                labels[j] = next_id
        next_id += 1
    return labels


def shap_feature_importance(all_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Train LightGBM on all rows, compute mean |SHAP| per feature."""
    import shap

    eligible = [r for r in all_rows if r["result"].get("min_fold_n_trades", 0) >= 30]
    if len(eligible) < 50:
        print("  Stage 14: too few eligible rows for SHAP; returning empty.")
        return {}
    from scripts.sweep_v2.run_surrogate import configs_to_matrix

    X = configs_to_matrix([r["config"] for r in eligible])
    y = np.array([r["result"].get("cross_fold_sharpe", 0.0) for r in eligible], dtype="f4")
    model = lgb.LGBMRegressor(
        n_estimators=200, learning_rate=0.05, num_leaves=63,
        min_child_samples=5, n_jobs=-1, verbose=-1, random_state=46,
    )
    model.fit(X, y)
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)
    mean_abs = np.abs(shap_values).mean(axis=0)
    return {col: float(v) for col, v in zip(X.columns, mean_abs)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(SWEEP_DIR / "stage13_replay_march.jsonl"))
    parser.add_argument(
        "--all-evals",
        nargs="+",
        default=[str(SWEEP_DIR / f"stage{n}_{name}.jsonl") for n, name in [
            (3, "lhs"), (4, "tpe"), (5, "nsga"),
            (6, "cmaes"), (7, "ga"), (8, "surrogate"),
        ]],
    )
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage14_portfolio.json"))
    args = parser.parse_args()

    survivors = _runner.read_jsonl(Path(args.input)) if Path(args.input).exists() else []
    survivors = [r for r in survivors if r.get("march_replay", {}).get("pass")]
    print(f"  Stage 14: portfolio + sensitivity on {len(survivors)} final survivors.")

    # SHAP can be computed regardless of survivor count
    all_rows = []
    for p in args.all_evals:
        if Path(p).exists():
            all_rows.extend(_runner.read_jsonl(Path(p)))
    importances = shap_feature_importance(all_rows)
    top_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:15]
    print("  Stage 14: top SHAP drivers:")
    for name, val in top_features:
        print(f"    {name}: {val:.4f}")

    # Cluster survivors
    clusters_out = []
    if survivors:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        fl_path = SWEEP_DIR / "features.parquet"
        from scripts.sweep_v2 import features as feat_mod
        fl = feat_mod.FeatureLookup.from_parquet(fl_path) if fl_path.exists() else None
        ctx = evaluate.EvalContext.build(symbols, args.date_start, args.date_end, feature_lookup=fl)
        fold_data = folds_mod.load_folds()
        ctx.fold_mask = evaluate.FoldMask(
            n_folds=fold_data["n_folds"],
            slug_to_fold={s: int(f) for s, f in fold_data["slug_to_fold"].items()},
        )
        from datetime import datetime, timedelta
        d0 = datetime.strptime(args.date_start, "%Y-%m-%d")
        d1 = datetime.strptime(args.date_end, "%Y-%m-%d")
        dates = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d")
                 for i in range((d1 - d0).days + 1)]
        vectors = [daily_pnl_vector(r["config"], ctx, dates) for r in survivors]
        clusters = cluster_by_correlation(vectors, threshold=0.7)
        for r, c, v in zip(survivors, clusters, vectors):
            clusters_out.append({"config_id": r["config_id"], "cluster": int(c),
                                  "daily_pnl_vector": v.tolist()})
        n_clusters = len(set(clusters))
        print(f"  Stage 14: {len(survivors)} survivors → {n_clusters} correlation clusters (ρ≥0.7).")

    payload = {
        "n_survivors": len(survivors),
        "clusters": clusters_out,
        "shap_importance": importances,
        "top_features": [{"name": n, "importance": v} for n, v in top_features],
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str))
    print(f"  Stage 14: wrote {args.out}")


if __name__ == "__main__":
    main()
