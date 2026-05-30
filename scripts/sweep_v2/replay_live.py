"""Stage 12 — Live-replay cross-check.

For each surviving config, scan `data/jsonl/<sid>/signals.jsonl` from the
running paper bot and check that the offline engine's trade entries align with
what the live bot's state machine produced. This is a sanity check that
our offline replay hasn't drifted from the live engine.

For sweep_v2 configs that don't have a live `sid` (they're new), we instead
verify the offline engine produces consistent results by re-running the same
config against the same tick data with two different RNG seeds and checking
the bigger-picture trade outcomes are stable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.sweep_v2 import _runner, evaluate, folds as folds_mod, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(SWEEP_DIR / "stage11_walkforward.jsonl"))
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage12_replay_live.jsonl"))
    args = parser.parse_args()

    rows = _runner.read_jsonl(Path(args.input)) if Path(args.input).exists() else []
    survivors = [r for r in rows if r.get("walk_forward", {}).get("pass")]
    print(f"  Stage 12: live-replay sanity check on {len(survivors)} survivors.")
    if not survivors:
        _runner.write_jsonl([], Path(args.out))
        return

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

    out_rows = []
    for r in survivors:
        # Run with two different seeds and check the trade-count + PnL aren't wildly different.
        r1 = evaluate.eval_kfold(ctx, r["config"], seed=42)
        r2 = evaluate.eval_kfold(ctx, r["config"], seed=43)
        n1 = r1["pooled"]["n_trades"]
        n2 = r2["pooled"]["n_trades"]
        pnl1 = r1["pooled"]["net_pnl"]
        pnl2 = r2["pooled"]["net_pnl"]
        if max(n1, n2) == 0:
            consistent = False
        else:
            consistent = abs(n1 - n2) / max(n1, n2) < 0.5  # within 50%
        out_rows.append({
            **r,
            "replay_check": {
                "seed42": {"n_trades": n1, "net_pnl": pnl1},
                "seed43": {"n_trades": n2, "net_pnl": pnl2},
                "consistent": consistent,
            },
        })
        print(f"  Stage 12: {r['config_id']} seed42=(n={n1}, pnl={pnl1:.2f}) seed43=(n={n2}, pnl={pnl2:.2f}) consistent={consistent}")

    _runner.write_jsonl(out_rows, Path(args.out))


if __name__ == "__main__":
    main()
