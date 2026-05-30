"""Phase 2 sweep — two-step trail with activation threshold around the winner.

Each config replaces the winner's exit.trailing_stop_pct=None with a staircase
of (activation_pct, lock_pct) tuples. The trail-v2 logic only kicks in once
the trade's peak PnL reaches the activation threshold.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from scripts.sweep_v2 import _runner, evaluate, param_space, folds as folds_mod
from scripts.sweep_v2 import features as feat_mod
from scripts.sweep_v2 import trail_v2

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
PROPOSED_YAML = ROOT / "proposed_strategies_v3.yaml"


# Staircases to test. Each is a list of (activation_pct, lock_pct).
# - "single" stairs activate at one threshold.
# - "double/triple" tighten as the trade goes deeper into profit.
STAIRCASES: List[Tuple[str, List[Tuple[float, float]]]] = [
    # single-step
    ("act30_lock30", [(30, 30)]),
    ("act30_lock20", [(30, 20)]),
    ("act50_lock30", [(50, 30)]),
    ("act50_lock20", [(50, 20)]),
    ("act75_lock30", [(75, 30)]),
    ("act100_lock30", [(100, 30)]),
    ("act100_lock20", [(100, 20)]),
    ("act150_lock30", [(150, 30)]),
    ("act150_lock20", [(150, 20)]),
    ("act200_lock30", [(200, 30)]),
    ("act200_lock20", [(200, 20)]),
    # two-step (tighten as up)
    ("act50_30__act150_15", [(50, 30), (150, 15)]),
    ("act50_40__act150_20", [(50, 40), (150, 20)]),
    ("act100_30__act200_15", [(100, 30), (200, 15)]),
    ("act100_25__act200_15", [(100, 25), (200, 15)]),
    # three-step
    ("act50_40__act100_25__act200_15", [(50, 40), (100, 25), (200, 15)]),
    ("act75_35__act150_20__act250_10", [(75, 35), (150, 20), (250, 10)]),
    # very loose activation (almost like legacy trail)
    ("act10_lock40", [(10, 40)]),
    ("act10_lock50", [(10, 50)]),
]


def load_winner_config() -> Dict[str, Any]:
    import yaml
    p = yaml.safe_load(PROPOSED_YAML.read_text())
    if not p.get("strategies"):
        raise SystemExit("No survivors in proposed_strategies_v3.yaml.")
    return dict(p["strategies"][0]["config"])


def worker_init_v2(symbols, date_start, date_end, fold_mask_dict, feature_cache_path):
    """Like evaluate.worker_init but also installs the trail-v2 patch."""
    evaluate.worker_init(symbols, date_start, date_end, fold_mask_dict, feature_cache_path)
    trail_v2.install_patch()


def worker_eval_v2(cfg_dict, steps, seed=42):
    """Set the thread-local steps for this trial, then eval."""
    trail_v2.set_trail_v2(steps)
    try:
        return evaluate.worker_eval_kfold(cfg_dict, seed=seed)
    finally:
        trail_v2.set_trail_v2(None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "trail_v2_sweep.jsonl"))
    args = parser.parse_args()

    base = load_winner_config()
    base["exit.trailing_stop_pct"] = None  # disable legacy trail — v2 owns trailing now
    print(f"  Base winner config_id: {param_space.hash_id(base)}")
    print(f"  Testing {len(STAIRCASES)} v2-trail staircases + 1 baseline (None).")

    # Add baseline (no trail at all) for comparison
    work: List[Tuple[str, Optional[List[Tuple[float, float]]]]] = [("baseline_no_trail", None)]
    work += STAIRCASES

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)

    rows = [None] * len(work)
    with ProcessPoolExecutor(
        max_workers=9,
        initializer=worker_init_v2,
        initargs=(
            pool_args["symbols"],
            pool_args["date_start"],
            pool_args["date_end"],
            pool_args["fold_mask_dict"],
            pool_args["feature_cache_path"],
        ),
        mp_context=mp.get_context("spawn"),
    ) as ex:
        future_to_idx = {
            ex.submit(worker_eval_v2, base, steps, 42): i
            for i, (_, steps) in enumerate(work)
        }
        done = 0
        import time
        t0 = time.time()
        for fut in as_completed(future_to_idx):
            i = future_to_idx[fut]
            label, steps = work[i]
            res = fut.result()
            rows[i] = {
                "label": label,
                "staircase": steps,
                "result": res,
            }
            done += 1
            if done % 5 == 0 or done == len(work):
                el = time.time() - t0
                print(f"    {done}/{len(work)} done, {el:.0f}s")

    # Sort by cross-fold Sharpe
    rows = [r for r in rows if r is not None]
    sorted_rows = sorted(
        rows, key=lambda r: r["result"].get("cross_fold_sharpe", -1e9), reverse=True,
    )
    print("\n  Trail-v2 summary (sorted by cross-fold Sharpe):")
    print("  " + "─" * 100)
    print(f"  {'label':<35}  {'n_trades':>9}  {'pooled_pnl':>12}  {'sharpe':>8}  {'folds+':>7}")
    print("  " + "─" * 100)
    for r in sorted_rows:
        res = r["result"]
        per_fold = res.get("per_fold", [])
        folds_pos = sum(1 for f in per_fold if f.get("net_pnl", 0) > 0)
        print(
            f"  {r['label']:<35}  {res['pooled']['n_trades']:>9}  "
            f"${res['pooled']['net_pnl']:>10.2f}   "
            f"{res.get('cross_fold_sharpe', 0):>7.3f}   {folds_pos}/5"
        )
    print("  " + "─" * 100)

    _runner.write_jsonl([{**r, "staircase": r["staircase"]} for r in rows], Path(args.out))


if __name__ == "__main__":
    main()
