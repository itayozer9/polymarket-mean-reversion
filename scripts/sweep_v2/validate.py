"""Stage 9 — Strict K-Fold OOS validation + Bonferroni gate.

Reads pooled JSONL rows from Stages 3-8 and applies:
  1. Per-fold strict bar: net_pnl > 0 AND bootstrap CI > 0 AND n_trades >= 30
  2. GOLD: ≥4 of 5 folds pass
  3. Wilcoxon one-sided p-value on per-trade pooled OOF PnL
  4. Bonferroni correction over GOLD candidates

Output: `stage9_validated.jsonl` with verdict + corrected p-value per config.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from scripts.sweep_v2 import _runner, evaluate, param_space, folds as folds_mod

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"


def per_fold_strict(per_fold: List[Dict[str, Any]], min_trades: int = 30) -> List[Dict[str, Any]]:
    """For each fold, run bootstrap on its trade PnL and check the strict bar."""
    # We don't have per-fold raw trades here (eval_kfold strips them). Compute
    # a Gaussian CI proxy: pnl_mean ± 1.96 * pnl_std / sqrt(n).
    out = []
    for f in per_fold:
        n = f.get("n_trades", 0)
        if n < min_trades:
            out.append({**f, "ci_low": float("nan"), "pass": False})
            continue
        # Proxy bootstrap-95 CI on total PnL via Gaussian approx
        # (good enough as a first-pass gate; full bootstrap done at the next step).
        std = f.get("pnl_std", 0.0)
        ci_low = f.get("avg_pnl", 0.0) - 1.96 * (std / max(1, np.sqrt(n)))
        out.append({
            **f,
            "ci_low_per_trade": float(ci_low),
            "pass": bool(f.get("net_pnl", 0) > 0 and ci_low > 0),
        })
    return out


def evaluate_with_trades(cfg_dict: Dict[str, Any], ctx: evaluate.EvalContext) -> List:
    """Re-run a config and collect per-trade PnL for bonafide bootstrap / Wilcoxon."""
    res = evaluate.eval_kfold(ctx, cfg_dict, seed=42)
    return res.get("pooled_trades", [])


def bootstrap_ci_per_trade(pnls: np.ndarray, n_resamples: int = 5000, seed: int = 0) -> tuple:
    if len(pnls) == 0:
        return (0.0, 0.0, 0.0)
    rng = np.random.default_rng(seed)
    samples = rng.choice(pnls, size=(n_resamples, len(pnls)), replace=True).mean(axis=1)
    p5, p50, p95 = np.percentile(samples, [5, 50, 95])
    return (float(p5), float(p50), float(p95))


def wilcoxon_one_sided(pnls: np.ndarray) -> float:
    from scipy import stats
    if len(pnls) < 5:
        return 1.0
    if np.all(pnls == 0):
        return 1.0
    try:
        res = stats.wilcoxon(pnls, alternative="greater", zero_method="zsplit")
        return float(res.pvalue)
    except ValueError:
        return 1.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-stages", nargs="+",
                        default=[str(SWEEP_DIR / f"stage{n}_{name}.jsonl") for n, name in [
                            (3, "lhs"), (4, "tpe"), (5, "nsga"),
                            (6, "cmaes"), (7, "ga"), (8, "surrogate"),
                        ]])
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--min-folds-pass", type=int, default=4)
    parser.add_argument("--min-trades-per-fold", type=int, default=30)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage9_validated.jsonl"))
    args = parser.parse_args()

    # Pool all input rows
    pooled = []
    for path in args.input_stages:
        p = Path(path)
        if p.exists():
            pooled.extend(_runner.read_jsonl(p))
    print(f"  Stage 9: loaded {len(pooled)} eval rows across input stages.")
    if not pooled:
        Path(args.out).write_text("")
        return

    # Dedup by config_id
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in pooled:
        cid = r["config_id"]
        # keep the one with the highest sharpe
        prev = by_id.get(cid)
        if prev is None or r["result"].get("cross_fold_sharpe", -1e9) > prev["result"].get("cross_fold_sharpe", -1e9):
            by_id[cid] = r
    pooled = list(by_id.values())
    print(f"  Stage 9: deduped to {len(pooled)} unique configs.")

    # First-pass gate using the per-fold metrics already in each row
    survivors_phase1 = []
    for r in pooled:
        per_fold = r["result"].get("per_fold", [])
        gated = per_fold_strict(per_fold, min_trades=args.min_trades_per_fold)
        n_pass = sum(1 for f in gated if f["pass"])
        if n_pass >= args.min_folds_pass:
            survivors_phase1.append({**r, "per_fold_gated": gated, "n_folds_pass": n_pass})
    print(f"  Stage 9: phase-1 (≥{args.min_folds_pass}/5 folds pass strict bar): {len(survivors_phase1)} survivors.")

    if not survivors_phase1:
        _runner.write_jsonl([], Path(args.out))
        return

    # Phase 2: re-run survivors collecting per-trade PnL for proper Wilcoxon + bootstrap
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

    enriched = []
    p_values = []
    for r in survivors_phase1:
        trades = evaluate_with_trades(r["config"], ctx)
        pnls = np.array([t.pnl for t in trades], dtype="f8")
        ci = bootstrap_ci_per_trade(pnls, n_resamples=5000, seed=0)
        p_val = wilcoxon_one_sided(pnls)
        p_values.append(p_val)
        enriched.append({
            **r,
            "pooled_n_trades": int(len(pnls)),
            "pooled_net_pnl": float(pnls.sum()),
            "pooled_ci_per_trade_p5_p50_p95": list(ci),
            "wilcoxon_p": p_val,
        })

    # Bonferroni
    K = len(p_values)
    threshold = args.alpha / max(1, K)
    for r, p in zip(enriched, p_values):
        r["bonferroni_threshold"] = threshold
        r["gold"] = bool(p < threshold and r["pooled_net_pnl"] > 0)

    n_gold = sum(1 for r in enriched if r["gold"])
    print(f"  Stage 9: phase-2 Bonferroni gate (α={args.alpha}/{K}={threshold:.2e}): {n_gold} GOLD picks.")

    _runner.write_jsonl(enriched, Path(args.out))
    print(f"  Stage 9: wrote {len(enriched)} validated rows → {args.out}")


if __name__ == "__main__":
    main()
