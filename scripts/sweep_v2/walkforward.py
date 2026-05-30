"""Stage 11 — Walk-forward validation.

Chronological 5-day train → 1-day test → roll forward over the May 15-23 span.
Stress survivors only.

NOTE: train/test split here is on TIME, not used for fitting (we already have
the configs). We just compute per-day PnL on each "test" day and require
positive median.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from scripts.sweep_v2 import _runner, evaluate, folds as folds_mod, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(SWEEP_DIR / "stage10_stress.jsonl"))
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage11_walkforward.jsonl"))
    args = parser.parse_args()

    rows = _runner.read_jsonl(Path(args.input)) if Path(args.input).exists() else []
    survivors = [r for r in rows if r.get("stress_pass")]
    print(f"  Stage 11: running walk-forward on {len(survivors)} stress-survivor configs.")
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

    d0 = datetime.strptime(args.date_start, "%Y-%m-%d")
    d1 = datetime.strptime(args.date_end, "%Y-%m-%d")
    days = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)]
    outcomes_path = ROOT / "data" / "outcomes.csv"

    # Need at least 5 days of train + 1 day of test = 6 days available
    walk_windows = [days[i + 5] for i in range(len(days) - 5)]
    print(f"  Stage 11: testing on {len(walk_windows)} forward days: {walk_windows}")

    out_rows = []
    for r in survivors:
        per_day = []
        for test_day in walk_windows:
            test_slugs = _slugs_for_date(outcomes_path, test_day, symbols)
            res = evaluate.eval_on_slugs(ctx, r["config"], test_slugs, seed=42)
            per_day.append({"date": test_day, "pnl": res["aggregate"]["net_pnl"],
                            "n_trades": res["aggregate"]["n_trades"]})
        pnls = [d["pnl"] for d in per_day]
        median = float(sorted(pnls)[len(pnls) // 2]) if pnls else 0.0
        passed = median > 0
        out_rows.append({**r, "walk_forward": {"per_day": per_day,
                                                 "median_pnl": median,
                                                 "pass": passed}})
        print(f"  Stage 11: {r['config_id']} median={median:.2f} pass={passed}")

    n_pass = sum(1 for r in out_rows if r["walk_forward"]["pass"])
    _runner.write_jsonl(out_rows, Path(args.out))
    print(f"  Stage 11: {n_pass}/{len(out_rows)} survivors passed walk-forward.")


if __name__ == "__main__":
    main()
