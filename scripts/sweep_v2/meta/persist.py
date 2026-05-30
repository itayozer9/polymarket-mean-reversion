"""Stage 16 — Meta-learning persistence (the learning curve).

Reads outputs from all 15 prior stages and writes/updates the persistent
meta-store under data/sweep_v2/meta/. Subsequent iterations consume these
artifacts to bias search distributions, prune dead features, and warm-start
samplers.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from scripts.sweep_v2 import _runner, param_space

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


def classify_failure(row: Dict[str, Any], gold_rows: List[Dict[str, Any]]) -> str:
    res = row.get("result", {})
    pooled = res.get("pooled", {})
    per_fold = res.get("per_fold", [])
    if res.get("min_fold_n_trades", 0) < 30:
        return "low_trade_count"
    if pooled.get("net_pnl", 0) <= 0:
        return "train_negative"
    # If it had positive train PnL but no GOLD designation: likely overfit / CI failed
    cid = row["config_id"]
    is_gold = any(g.get("config_id") == cid and g.get("gold") for g in gold_rows)
    if not is_gold:
        return "ci_or_bonferroni_failed"
    return "passed"


def write_lifetime_store(all_eval_paths: List[Path], gold_rows: List[Dict[str, Any]]):
    """Append all evaluations from this iteration to all_evals_lifetime.parquet."""
    new_rows = []
    iteration_id = int(time.time())
    for p in all_eval_paths:
        if not p.exists():
            continue
        for r in _runner.read_jsonl(p):
            cfg = r["config"]
            res = r["result"]
            flat = param_space.flatten_for_dataframe(cfg)
            flat.update({
                "config_id": r["config_id"],
                "stage": p.stem,
                "iteration_id": iteration_id,
                "cross_fold_sharpe": res.get("cross_fold_sharpe", float("nan")),
                "cross_fold_pnl_mean": res.get("cross_fold_pnl_mean", float("nan")),
                "min_fold_n_trades": res.get("min_fold_n_trades", 0),
                "pooled_net_pnl": res.get("pooled", {}).get("net_pnl", float("nan")),
                "failure_class": classify_failure(r, gold_rows),
            })
            new_rows.append(flat)
    if not new_rows:
        return None
    new_df = pd.DataFrame(new_rows)
    out_path = META_DIR / "all_evals_lifetime.parquet"
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        full = pd.concat([existing, new_df], ignore_index=True)
    else:
        full = new_df
    out_path.parent.mkdir(parents=True, exist_ok=True)
    full.to_parquet(out_path, index=False)
    print(f"  Stage 16: lifetime store updated → {out_path} ({len(full):,} total rows; +{len(new_df):,} this iteration)")
    return out_path


def update_viable_region_priors():
    out_path = META_DIR / "all_evals_lifetime.parquet"
    if not out_path.exists():
        return
    df = pd.read_parquet(out_path)
    viable = df[(df["pooled_net_pnl"].fillna(-1) > 0) & (df["min_fold_n_trades"] >= 30)]
    if len(viable) < 20:
        print(f"  Stage 16: only {len(viable)} viable lifetime rows — too few to derive priors yet.")
        return
    priors = {}
    for name, kind, lo, hi, _ in param_space.PARAMS:
        if kind not in ("float", "int"):
            continue
        if name not in viable.columns:
            continue
        col = viable[name].dropna()
        col = col[col != "__NONE__"]
        if len(col) < 10:
            continue
        try:
            arr = pd.to_numeric(col, errors="coerce").dropna().to_numpy(dtype="f8")
            if len(arr) < 10:
                continue
            priors[name] = {
                "p5": float(np.percentile(arr, 5)),
                "p95": float(np.percentile(arr, 95)),
                "median": float(np.median(arr)),
                "n_support": int(len(arr)),
            }
        except (ValueError, TypeError):
            continue
    out_priors = META_DIR / "viable_region_priors.json"
    out_priors.write_text(json.dumps(priors, indent=2))
    print(f"  Stage 16: viable region priors → {out_priors} ({len(priors)} params).")


def update_feature_usefulness(portfolio_path: Path):
    """Read SHAP importances from the stage14 portfolio.json and merge into a
    running history in feature_useful.json. Features ranking in the bottom
    quartile across ≥3 iterations get dropped via feature_graveyard.md."""
    if not portfolio_path.exists():
        return
    payload = json.loads(portfolio_path.read_text())
    shap = payload.get("shap_importance", {})
    history_path = META_DIR / "feature_useful.json"
    if history_path.exists():
        history = json.loads(history_path.read_text())
    else:
        history = {"iterations": [], "per_feature": {}}
    iter_id = int(time.time())
    history["iterations"].append(iter_id)
    for name, imp in shap.items():
        history["per_feature"].setdefault(name, []).append({"iter": iter_id, "importance": imp})
    history_path.write_text(json.dumps(history, indent=2))
    print(f"  Stage 16: feature usefulness history updated → {history_path}")

    # Feature graveyard: bottom-quartile across ≥3 iterations
    grave_path = META_DIR / "feature_graveyard.md"
    n_iters = len(history["iterations"])
    if n_iters < 3:
        return
    dead = []
    for name, hist in history["per_feature"].items():
        if len(hist) < 3:
            continue
        recent_imps = [h["importance"] for h in hist[-3:]]
        # Compare to the global median over the last iteration
        last_iter = history["iterations"][-1]
        global_imps = [imp for h in history["per_feature"].values() for imp in [h[-1]["importance"]] if h and h[-1]["iter"] == last_iter]
        if not global_imps:
            continue
        q1 = float(np.percentile(global_imps, 25))
        if all(r <= q1 for r in recent_imps):
            dead.append((name, recent_imps[-1]))
    if dead:
        lines = ["# Feature Graveyard", ""]
        lines.append("Features that ranked in the bottom quartile of SHAP importance "
                      "for ≥3 consecutive iterations. Dropped from the search space.")
        lines.append("")
        for name, imp in dead:
            lines.append(f"- `{name}` — last importance: {imp:.4f}")
        grave_path.write_text("\n".join(lines))
        print(f"  Stage 16: feature graveyard updated → {grave_path} ({len(dead)} dropped features).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all-eval-stages",
        nargs="+",
        default=[str(SWEEP_DIR / f"stage{n}_{name}.jsonl") for n, name in [
            (3, "lhs"), (4, "tpe"), (5, "nsga"),
            (6, "cmaes"), (7, "ga"), (8, "surrogate"),
        ]],
    )
    parser.add_argument("--validated", default=str(SWEEP_DIR / "stage9_validated.jsonl"))
    parser.add_argument("--portfolio", default=str(SWEEP_DIR / "stage14_portfolio.json"))
    args = parser.parse_args()

    META_DIR.mkdir(parents=True, exist_ok=True)
    eval_paths = [Path(p) for p in args.all_eval_stages]
    validated = _runner.read_jsonl(Path(args.validated)) if Path(args.validated).exists() else []
    gold_rows = [r for r in validated if r.get("gold")]

    write_lifetime_store(eval_paths, gold_rows)
    update_viable_region_priors()
    update_feature_usefulness(Path(args.portfolio))
    print("  Stage 16: meta-store fully updated.")


if __name__ == "__main__":
    main()
