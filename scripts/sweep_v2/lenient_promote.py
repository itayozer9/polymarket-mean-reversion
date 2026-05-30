"""Lower the strict bar. Promote candidates from lifetime store that:
  - pooled_net_pnl > 0
  - min_fold_n_trades >= MIN_TRADES (default 5; strict was 30)
  - folds_positive >= MIN_FOLDS_POS (default 3 of 5)

Then re-evaluate each through the full eval_kfold to capture per-trade PnL,
run a quick Wilcoxon (no Bonferroni — we're looking for plausibility, not proof),
and produce a stage9-format JSONL that downstream stages 10-15 can consume.

Usage:
    uv run python scripts/sweep_v2/lenient_promote.py
    uv run python scripts/sweep_v2/orchestrate.py --skip stage3_lhs stage4_tpe \\
      stage5_nsga stage6_cmaes stage7_ga stage8_surrogate stage9_validate \\
      --from-stage stage10_stress --medium
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.sweep_v2 import _runner, evaluate, folds as folds_mod, param_space
from scripts.sweep_v2 import features as feat_mod
from scripts.sweep_v2.validate import bootstrap_ci_per_trade, wilcoxon_one_sided

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-trades", type=int, default=5,
                        help="Min per-fold trade count (default 5; strict was 30).")
    parser.add_argument("--min-folds-positive", type=int, default=3,
                        help="Min folds with positive PnL (default 3 of 5).")
    parser.add_argument("--top-k", type=int, default=20,
                        help="Take top-K by pooled_net_pnl from the eligible set.")
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage9_validated.jsonl"))
    args = parser.parse_args()

    lifetime_path = META_DIR / "all_evals_lifetime.parquet"
    if not lifetime_path.exists():
        raise SystemExit(f"No lifetime store at {lifetime_path}")
    df = pd.read_parquet(lifetime_path)
    print(f"  Lenient: lifetime store has {len(df):,} rows.")

    eligible = df[
        (df["pooled_net_pnl"] > 0)
        & (df["min_fold_n_trades"] >= args.min_trades)
    ].copy()
    eligible = eligible.drop_duplicates("config_id")
    eligible = eligible.sort_values("pooled_net_pnl", ascending=False).head(args.top_k)
    print(f"  Lenient: {len(eligible)} unique configs with pooled_pnl > 0 "
          f"AND min_fold_trades >= {args.min_trades}.")

    if eligible.empty:
        Path(args.out).write_text("")
        print("  Lenient: nothing to promote.")
        return

    # Build context for re-evaluation
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    fl_path = SWEEP_DIR / "features.parquet"
    fl = feat_mod.FeatureLookup.from_parquet(fl_path) if fl_path.exists() else None
    ctx = evaluate.EvalContext.build(symbols, args.date_start, args.date_end, feature_lookup=fl)
    fold_data = folds_mod.load_folds()
    ctx.fold_mask = evaluate.FoldMask(
        n_folds=fold_data["n_folds"],
        slug_to_fold={s: int(f) for s, f in fold_data["slug_to_fold"].items()},
    )

    rows = []
    for _, row in eligible.iterrows():
        cfg = param_space.unflatten_from_dataframe(row.to_dict())
        res = evaluate.eval_kfold(ctx, cfg, seed=42)
        trades = res.get("pooled_trades", [])
        pnls = np.array([t.pnl for t in trades], dtype="f8")
        ci = bootstrap_ci_per_trade(pnls, n_resamples=2000, seed=0)
        p_val = wilcoxon_one_sided(pnls)
        per_fold = res.get("per_fold", [])
        n_folds_pos = sum(1 for f in per_fold if f.get("net_pnl", 0) > 0)
        passed = n_folds_pos >= args.min_folds_positive and float(pnls.sum()) > 0

        rows.append({
            "config_id": param_space.hash_id(cfg),
            "config": cfg,
            "result": res,
            "per_fold_gated": per_fold,
            "n_folds_pass": n_folds_pos,
            "pooled_n_trades": int(len(pnls)),
            "pooled_net_pnl": float(pnls.sum()),
            "pooled_ci_per_trade_p5_p50_p95": list(ci),
            "wilcoxon_p": p_val,
            "bonferroni_threshold": None,  # not applied in lenient mode
            "gold": bool(passed),
            "promotion_mode": "lenient",
        })
        print(f"  Lenient: {param_space.hash_id(cfg)} "
              f"folds_pos={n_folds_pos}/5 n_trades={len(pnls)} "
              f"pnl=${pnls.sum():.2f} p={p_val:.3f} → gold={passed}")

    _runner.write_jsonl(rows, Path(args.out))
    n_gold = sum(1 for r in rows if r["gold"])
    print(f"  Lenient: wrote {len(rows)} rows ({n_gold} GOLD) → {args.out}")


if __name__ == "__main__":
    main()
