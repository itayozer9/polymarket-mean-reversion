"""Stage 10 — Six-axis perturbation stress on GOLD picks.

For each GOLD config, run six independent perturbations. Survival = pass all six.
"""
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from scripts.sweep_v2 import _runner, evaluate, folds as folds_mod, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"


def perturb_numeric(cfg: Dict[str, Any], name: str, pct: float) -> Dict[str, Any]:
    out = dict(cfg)
    v = cfg.get(name)
    if v is None:
        return out
    # find the original (low, high) for clipping
    for n, kind, lo, hi, _ in param_space.PARAMS:
        if n == name and kind in ("float", "int"):
            new_v = v * (1 + pct)
            new_v = max(lo, min(hi, new_v))
            if kind == "int":
                new_v = int(round(new_v))
            out[name] = new_v
            break
    return out


def joint_perturbation(cfg: Dict[str, Any], rng: np.random.Generator, radius: float = 0.15) -> Dict[str, Any]:
    out = dict(cfg)
    for n, kind, lo, hi, _ in param_space.PARAMS:
        v = out.get(n)
        if v is None or kind not in ("float", "int"):
            continue
        delta = float(rng.uniform(-radius, radius)) * (hi - lo)
        new_v = max(lo, min(hi, v + delta))
        if kind == "int":
            new_v = int(round(new_v))
        out[n] = new_v
    return out


def adversarial_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Fee bumped to 0.08, reject_prob doubled — capped at 0.10 lower-end of the space."""
    out = dict(cfg)
    out["fill.fee_rate"] = 0.08  # outside the locked range but we override here
    out["fill.reject_prob"] = min(0.10, max(0.02, (cfg.get("fill.reject_prob") or 0.03) * 2.0))
    return out


def run_stress(
    cfg: Dict[str, Any], ctx: evaluate.EvalContext, seed_list=range(16), all_slugs=None,
) -> Dict[str, Any]:
    """Run all six stress axes on one config. Returns a dict of {axis: pass_bool, …}."""
    rng = np.random.default_rng(42)

    # 1. Seed stability
    seed_pnls = []
    for s in seed_list:
        res = evaluate.eval_kfold(ctx, cfg, seed=int(s))
        seed_pnls.append(res["pooled"]["net_pnl"])
    seed_positive = sum(1 for p in seed_pnls if p > 0)
    seed_pass = seed_positive >= 12  # 12/16 threshold per plan
    seed_result = {"n_positive": seed_positive, "n_total": len(seed_pnls), "pass": seed_pass}

    # 2. 1D parameter neighborhood (±10%, ±20% on each numeric param)
    nbrhd_results = {"n_runs": 0, "n_positive": 0}
    for n, kind, _, _, _ in param_space.PARAMS:
        if kind not in ("float", "int"):
            continue
        for pct in (-0.10, 0.10, -0.20, 0.20):
            perturbed = perturb_numeric(cfg, n, pct)
            res = evaluate.eval_kfold(ctx, perturbed, seed=42)
            nbrhd_results["n_runs"] += 1
            if res["pooled"]["net_pnl"] > 0:
                nbrhd_results["n_positive"] += 1
    nbrhd_pass_rate = (
        nbrhd_results["n_positive"] / nbrhd_results["n_runs"]
        if nbrhd_results["n_runs"] > 0 else 0.0
    )
    nbrhd_results["pass_rate"] = nbrhd_pass_rate
    nbrhd_results["pass"] = nbrhd_pass_rate >= 0.80

    # 3. Joint perturbation
    joint_pnls = []
    for _ in range(100):
        perturbed = joint_perturbation(cfg, rng, radius=0.15)
        res = evaluate.eval_kfold(ctx, perturbed, seed=42)
        joint_pnls.append(res["pooled"]["net_pnl"])
    joint_pass_rate = sum(1 for p in joint_pnls if p > 0) / max(1, len(joint_pnls))
    joint_result = {"n_runs": len(joint_pnls), "pass_rate": joint_pass_rate,
                    "pass": joint_pass_rate >= 0.75}

    # 4. Per-symbol breakdown
    per_sym_results = {}
    n_pos = 0
    for sym in ctx.symbols:
        # filter slugs to only this symbol's
        if all_slugs is None:
            sym_slugs = {slug for sym_, slug in ctx.all_slugs() if sym_ == sym}
        else:
            sym_slugs = {slug for sym_, slug in all_slugs if sym_ == sym}
        if not sym_slugs:
            continue
        # restrict per-symbol on the underlying eval
        # eval_on_slugs already filters by slug set; we just narrow ctx.symbols implicitly
        res = evaluate.eval_on_slugs(ctx, cfg, sym_slugs, seed=42)
        per_sym_results[sym] = res["per_symbol"].get(sym, {})
        if res["per_symbol"].get(sym, {}).get("net_pnl", 0) > 0:
            n_pos += 1
    symbol_result = {"per_symbol": per_sym_results, "n_positive": n_pos,
                     "n_total": len(per_sym_results),
                     "pass": n_pos >= 3}

    # 5. Adversarial costs
    adv_cfg = adversarial_cfg(cfg)
    res = evaluate.eval_kfold(ctx, adv_cfg, seed=42)
    adv_result = {"pooled_pnl": res["pooled"]["net_pnl"], "pass": res["pooled"]["net_pnl"] > 0}

    # 6. Liquidity shock — approximated by raising filter.min_book_depth_usd
    liq_cfg = dict(cfg)
    liq_cfg["filter.min_book_depth_usd"] = (cfg.get("filter.min_book_depth_usd") or 20.0) * 2.0
    res = evaluate.eval_kfold(ctx, liq_cfg, seed=42)
    liq_result = {"pooled_pnl": res["pooled"]["net_pnl"], "pass": res["pooled"]["net_pnl"] > 0}

    all_pass = all([
        seed_pass, nbrhd_results["pass"], joint_result["pass"],
        symbol_result["pass"], adv_result["pass"], liq_result["pass"],
    ])
    return {
        "seed_stability": seed_result,
        "param_1d_neighborhood": nbrhd_results,
        "joint_perturbation": joint_result,
        "per_symbol": symbol_result,
        "adversarial_costs": adv_result,
        "liquidity_shock": liq_result,
        "all_pass": all_pass,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(SWEEP_DIR / "stage9_validated.jsonl"))
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage10_stress.jsonl"))
    parser.add_argument("--n-seeds", type=int, default=16)
    args = parser.parse_args()

    rows = _runner.read_jsonl(Path(args.input)) if Path(args.input).exists() else []
    gold = [r for r in rows if r.get("gold")]
    print(f"  Stage 10: running stress on {len(gold)} GOLD picks.")
    if not gold:
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

    all_slugs = ctx.all_slugs()
    out_rows = []
    for i, r in enumerate(gold, 1):
        stress = run_stress(r["config"], ctx, seed_list=range(args.n_seeds), all_slugs=all_slugs)
        out_rows.append({**r, "stress": stress, "stress_pass": stress["all_pass"]})
        print(f"  Stage 10: {i}/{len(gold)} {r['config_id']} → pass={stress['all_pass']}")

    n_pass = sum(1 for r in out_rows if r["stress_pass"])
    _runner.write_jsonl(out_rows, Path(args.out))
    print(f"  Stage 10: {n_pass}/{len(out_rows)} GOLD picks survived all six perturbations.")


if __name__ == "__main__":
    main()
