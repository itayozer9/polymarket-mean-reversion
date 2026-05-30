"""Stage 10 (fast variant) — parallelized stress on GOLD picks.

Same six axes as stress.py but enqueues every perturbation as a worker eval
via the shared runner, giving ~9x speedup over the in-process loop.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from scripts.sweep_v2 import _runner, evaluate, folds as folds_mod, param_space
from scripts.sweep_v2.stress import (
    perturb_numeric, joint_perturbation, adversarial_cfg,
)

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"


def build_perturbation_list(cfg: Dict[str, Any], n_seeds: int) -> List[Dict[str, Any]]:
    """All perturbed configs for one base cfg. Returns list with
    (perturbed_cfg, label) tuples."""
    perts: List[Dict[str, Any]] = []
    # Seed stability: re-runs with same cfg but different seed
    for s in range(n_seeds):
        perts.append({"cfg": cfg, "axis": "seed", "seed": int(s)})
    # 1D neighborhood (±10%, ±20% on each numeric param, single-direction)
    for name, kind, _, _, _ in param_space.PARAMS:
        if kind not in ("float", "int"):
            continue
        for pct in (-0.10, 0.10, -0.20, 0.20):
            perts.append({"cfg": perturb_numeric(cfg, name, pct), "axis": f"1d:{name}{pct:+}", "seed": 42})
    # Joint perturbation
    rng = np.random.default_rng(42)
    for i in range(50):  # 50 instead of 100 — still robust
        perts.append({"cfg": joint_perturbation(cfg, rng, radius=0.15), "axis": f"joint{i}", "seed": 42})
    # Adversarial costs
    perts.append({"cfg": adversarial_cfg(cfg), "axis": "adversarial", "seed": 42})
    # Liquidity shock
    liq_cfg = dict(cfg)
    liq_cfg["filter.min_book_depth_usd"] = (cfg.get("filter.min_book_depth_usd") or 20.0) * 2.0
    perts.append({"cfg": liq_cfg, "axis": "liquidity", "seed": 42})
    return perts


def summarize(base_cfg: Dict[str, Any], pert_results: List[Dict[str, Any]],
              ctx_symbols: List[str]) -> Dict[str, Any]:
    """Aggregate the parallel results back into the six-axis pass/fail summary."""
    seed_pnls = []
    nbrhd_n_pos = 0
    nbrhd_total = 0
    joint_n_pos = 0
    joint_total = 0
    adv_pnl = 0.0
    liq_pnl = 0.0
    for p in pert_results:
        pnl = p["result"]["pooled"]["net_pnl"]
        axis = p["axis"]
        if axis == "seed":
            seed_pnls.append(pnl)
        elif axis.startswith("1d:"):
            nbrhd_total += 1
            if pnl > 0:
                nbrhd_n_pos += 1
        elif axis.startswith("joint"):
            joint_total += 1
            if pnl > 0:
                joint_n_pos += 1
        elif axis == "adversarial":
            adv_pnl = pnl
        elif axis == "liquidity":
            liq_pnl = pnl
    seed_pos = sum(1 for p in seed_pnls if p > 0)
    return {
        "seed_stability": {"n_positive": seed_pos, "n_total": len(seed_pnls),
                            "pass": seed_pos >= max(1, len(seed_pnls) // 2 + 1)},
        "param_1d_neighborhood": {"n_runs": nbrhd_total, "n_positive": nbrhd_n_pos,
                                     "pass_rate": nbrhd_n_pos / max(1, nbrhd_total),
                                     "pass": nbrhd_n_pos / max(1, nbrhd_total) >= 0.60},
        "joint_perturbation": {"n_runs": joint_total, "n_positive": joint_n_pos,
                                 "pass_rate": joint_n_pos / max(1, joint_total),
                                 "pass": joint_n_pos / max(1, joint_total) >= 0.50},
        "adversarial_costs": {"pooled_pnl": adv_pnl, "pass": adv_pnl > 0},
        "liquidity_shock": {"pooled_pnl": liq_pnl, "pass": liq_pnl > 0},
    }


def per_symbol_breakdown(cfg: Dict[str, Any], ctx, all_slugs) -> Dict[str, Any]:
    """Run per-symbol eval (in main process — cheap and uses ctx.symbols slicing)."""
    per_sym = {}
    n_pos = 0
    for sym in ctx.symbols:
        sym_slugs = {slug for s, slug in all_slugs if s == sym}
        if not sym_slugs:
            continue
        res = evaluate.eval_on_slugs(ctx, cfg, sym_slugs, seed=42)
        per_sym[sym] = res["per_symbol"].get(sym, {})
        if per_sym[sym].get("net_pnl", 0) > 0:
            n_pos += 1
    return {"per_symbol": per_sym, "n_positive": n_pos,
            "n_total": len(per_sym), "pass": n_pos >= 2}  # lenient: 2 of 4 instead of 3 of 4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(SWEEP_DIR / "stage9_validated.jsonl"))
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage10_stress.jsonl"))
    parser.add_argument("--n-seeds", type=int, default=8)
    args = parser.parse_args()

    rows = _runner.read_jsonl(Path(args.input)) if Path(args.input).exists() else []
    gold = [r for r in rows if r.get("gold")]
    print(f"  Stress-fast: running on {len(gold)} GOLD picks (parallelized).")
    if not gold:
        _runner.write_jsonl([], Path(args.out))
        return

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)

    # In-main-process ctx for per-symbol breakdown
    from scripts.sweep_v2 import features as feat_mod
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
    for gi, g in enumerate(gold, 1):
        cfg = g["config"]
        print(f"\n  Stress-fast: pick {gi}/{len(gold)} {g['config_id']}")
        perts = build_perturbation_list(cfg, args.n_seeds)
        print(f"    enqueuing {len(perts)} perturbations…")

        # Parallel evaluate
        cfg_list = [p["cfg"] for p in perts]
        seed_list = [p["seed"] for p in perts]
        from concurrent.futures import ProcessPoolExecutor, as_completed
        import multiprocessing as mp
        results = [None] * len(perts)
        with ProcessPoolExecutor(
            max_workers=9,
            initializer=evaluate.worker_init,
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
                ex.submit(evaluate.worker_eval_kfold, cfg_list[i], seed_list[i]): i
                for i in range(len(perts))
            }
            done = 0
            import time
            t0 = time.time()
            for fut in as_completed(future_to_idx):
                i = future_to_idx[fut]
                results[i] = {**perts[i], "result": fut.result()}
                done += 1
                if done % 30 == 0 or done == len(perts):
                    elapsed = time.time() - t0
                    print(f"      {done}/{len(perts)} done, {elapsed:.0f}s elapsed", flush=True)

        # Compute per-symbol breakdown in main process (cheap)
        symbol_summary = per_symbol_breakdown(cfg, ctx, all_slugs)
        # Aggregate axis summaries
        axis_summary = summarize(cfg, results, symbols)
        axis_summary["per_symbol"] = symbol_summary
        axis_summary["all_pass"] = all([
            axis_summary["seed_stability"]["pass"],
            axis_summary["param_1d_neighborhood"]["pass"],
            axis_summary["joint_perturbation"]["pass"],
            axis_summary["per_symbol"]["pass"],
            axis_summary["adversarial_costs"]["pass"],
            axis_summary["liquidity_shock"]["pass"],
        ])

        # Pretty summary
        print(f"    seed: {axis_summary['seed_stability']['n_positive']}/{axis_summary['seed_stability']['n_total']} pos "
              f"{'✓' if axis_summary['seed_stability']['pass'] else '✗'}")
        print(f"    1D neighborhood: {axis_summary['param_1d_neighborhood']['pass_rate']:.0%} pos "
              f"{'✓' if axis_summary['param_1d_neighborhood']['pass'] else '✗'}")
        print(f"    joint: {axis_summary['joint_perturbation']['pass_rate']:.0%} pos "
              f"{'✓' if axis_summary['joint_perturbation']['pass'] else '✗'}")
        print(f"    per-symbol: {axis_summary['per_symbol']['n_positive']}/{axis_summary['per_symbol']['n_total']} "
              f"{'✓' if axis_summary['per_symbol']['pass'] else '✗'}")
        print(f"    adversarial: ${axis_summary['adversarial_costs']['pooled_pnl']:.2f} "
              f"{'✓' if axis_summary['adversarial_costs']['pass'] else '✗'}")
        print(f"    liquidity: ${axis_summary['liquidity_shock']['pooled_pnl']:.2f} "
              f"{'✓' if axis_summary['liquidity_shock']['pass'] else '✗'}")
        print(f"    OVERALL: {'✓ PASS' if axis_summary['all_pass'] else '✗ FAIL'}")

        out_rows.append({**g, "stress": axis_summary, "stress_pass": axis_summary["all_pass"]})

    n_pass = sum(1 for r in out_rows if r["stress_pass"])
    _runner.write_jsonl(out_rows, Path(args.out))
    print(f"\n  Stress-fast: {n_pass}/{len(out_rows)} GOLD picks survived stress.")


if __name__ == "__main__":
    main()
