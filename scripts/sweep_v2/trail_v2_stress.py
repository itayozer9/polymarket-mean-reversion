"""Stress-test the top trail-v2 staircases and pick the most robust.

Runs the same 6-axis stress as stress_fast.py but on each staircase variant
of the base winner config. Picks survivors that pass all axes.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from scripts.sweep_v2 import _runner, evaluate, folds as folds_mod, param_space
from scripts.sweep_v2 import features as feat_mod
from scripts.sweep_v2 import trail_v2
from scripts.sweep_v2.stress import perturb_numeric, joint_perturbation, adversarial_cfg
from scripts.sweep_v2.stress_fast import summarize, per_symbol_breakdown
from scripts.sweep_v2.trail_v2_sweep import load_winner_config, worker_init_v2, worker_eval_v2

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"


# Top-5 staircases by Sharpe from trail_v2_sweep
TOP_STAIRCASES: List[Tuple[str, List[Tuple[float, float]]]] = [
    ("act30_lock30", [(30, 30)]),
    ("act100_lock30", [(100, 30)]),
    ("act150_lock30", [(150, 30)]),
    ("act50_30__act150_15", [(50, 30), (150, 15)]),
    ("act100_25__act200_15", [(100, 25), (200, 15)]),
    ("act50_40__act100_25__act200_15", [(50, 40), (100, 25), (200, 15)]),
]


def build_perturbation_list(cfg: Dict[str, Any], steps, n_seeds: int) -> List[Dict[str, Any]]:
    perts: List[Dict[str, Any]] = []
    for s in range(n_seeds):
        perts.append({"cfg": cfg, "steps": steps, "axis": "seed", "seed": int(s)})
    for name, kind, _, _, _ in param_space.PARAMS:
        if kind not in ("float", "int"):
            continue
        for pct in (-0.10, 0.10, -0.20, 0.20):
            perts.append({"cfg": perturb_numeric(cfg, name, pct), "steps": steps,
                          "axis": f"1d:{name}{pct:+}", "seed": 42})
    rng = np.random.default_rng(42)
    for i in range(50):
        perts.append({"cfg": joint_perturbation(cfg, rng, radius=0.15), "steps": steps,
                      "axis": f"joint{i}", "seed": 42})
    perts.append({"cfg": adversarial_cfg(cfg), "steps": steps, "axis": "adversarial", "seed": 42})
    liq_cfg = dict(cfg)
    liq_cfg["filter.min_book_depth_usd"] = (cfg.get("filter.min_book_depth_usd") or 20.0) * 2.0
    perts.append({"cfg": liq_cfg, "steps": steps, "axis": "liquidity", "seed": 42})
    return perts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-seeds", type=int, default=8)
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "trail_v2_stress.jsonl"))
    args = parser.parse_args()

    base = load_winner_config()
    base["exit.trailing_stop_pct"] = None
    print(f"  Base: {param_space.hash_id(base)}, testing {len(TOP_STAIRCASES)} top staircases.")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)
    fl_path = SWEEP_DIR / "features.parquet"
    fl = feat_mod.FeatureLookup.from_parquet(fl_path) if fl_path.exists() else None
    ctx = evaluate.EvalContext.build(symbols, args.date_start, args.date_end, feature_lookup=fl)
    fold_data = folds_mod.load_folds()
    ctx.fold_mask = evaluate.FoldMask(
        n_folds=fold_data["n_folds"],
        slug_to_fold={s: int(f) for s, f in fold_data["slug_to_fold"].items()},
    )
    all_slugs = ctx.all_slugs()

    out_rows = []
    for gi, (label, steps) in enumerate(TOP_STAIRCASES, 1):
        print(f"\n  Stress: {gi}/{len(TOP_STAIRCASES)} {label}")
        perts = build_perturbation_list(base, steps, args.n_seeds)
        print(f"    enqueuing {len(perts)} perturbations…")
        results = [None] * len(perts)
        with ProcessPoolExecutor(
            max_workers=9, initializer=worker_init_v2,
            initargs=(pool_args["symbols"], pool_args["date_start"], pool_args["date_end"],
                      pool_args["fold_mask_dict"], pool_args["feature_cache_path"]),
            mp_context=mp.get_context("spawn"),
        ) as ex:
            future_to_idx = {
                ex.submit(worker_eval_v2, perts[i]["cfg"], perts[i]["steps"], perts[i]["seed"]): i
                for i in range(len(perts))
            }
            done = 0
            t0 = time.time()
            for fut in as_completed(future_to_idx):
                i = future_to_idx[fut]
                results[i] = {**perts[i], "result": fut.result()}
                done += 1
                if done % 30 == 0 or done == len(perts):
                    el = time.time() - t0
                    print(f"      {done}/{len(perts)} done, {el:.0f}s", flush=True)

        # Per-symbol breakdown (use trail_v2 for this too)
        trail_v2.install_patch()
        trail_v2.set_trail_v2(steps)
        per_sym = per_symbol_breakdown(base, ctx, all_slugs)
        trail_v2.set_trail_v2(None)

        axis_summary = summarize(base, results, symbols)
        axis_summary["per_symbol"] = per_sym
        axis_summary["all_pass"] = all([
            axis_summary["seed_stability"]["pass"],
            axis_summary["param_1d_neighborhood"]["pass"],
            axis_summary["joint_perturbation"]["pass"],
            axis_summary["per_symbol"]["pass"],
            axis_summary["adversarial_costs"]["pass"],
            axis_summary["liquidity_shock"]["pass"],
        ])

        print(f"    seed: {axis_summary['seed_stability']['n_positive']}/{axis_summary['seed_stability']['n_total']} pos {'✓' if axis_summary['seed_stability']['pass'] else '✗'}")
        print(f"    1D:     {axis_summary['param_1d_neighborhood']['pass_rate']:.0%} pos {'✓' if axis_summary['param_1d_neighborhood']['pass'] else '✗'}")
        print(f"    joint:  {axis_summary['joint_perturbation']['pass_rate']:.0%} pos {'✓' if axis_summary['joint_perturbation']['pass'] else '✗'}")
        print(f"    per-sym:{axis_summary['per_symbol']['n_positive']}/{axis_summary['per_symbol']['n_total']} {'✓' if axis_summary['per_symbol']['pass'] else '✗'}")
        print(f"    adv:    ${axis_summary['adversarial_costs']['pooled_pnl']:.2f} {'✓' if axis_summary['adversarial_costs']['pass'] else '✗'}")
        print(f"    liq:    ${axis_summary['liquidity_shock']['pooled_pnl']:.2f} {'✓' if axis_summary['liquidity_shock']['pass'] else '✗'}")
        print(f"    OVERALL: {'✓ PASS' if axis_summary['all_pass'] else '✗ FAIL'}")

        out_rows.append({
            "label": label, "staircase": steps,
            "stress": axis_summary, "stress_pass": axis_summary["all_pass"],
        })

    n_pass = sum(1 for r in out_rows if r["stress_pass"])
    _runner.write_jsonl(out_rows, Path(args.out))
    print(f"\n  Stress: {n_pass}/{len(out_rows)} staircases passed all six axes.")


if __name__ == "__main__":
    main()
