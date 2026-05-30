"""Stage 7 — Evolutionary crossover GA over the top-200 distinct configs from
Stages 4-6 plus lifetime elites.

100 generations × 100 population = 10k evaluations (approximate; pruned for
duplicates).
"""
from __future__ import annotations

import argparse
import copy
import random
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from scripts.sweep_v2 import _runner, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


def _crossover(a: Dict[str, Any], b: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    child = {}
    for name, _, _, _, _ in param_space.PARAMS:
        child[name] = a[name] if rng.random() < 0.5 else b[name]
    return param_space._post_process(child)


def _mutate(
    cfg: Dict[str, Any], rng: random.Random, np_rng: np.random.Generator, p: float = 0.1
) -> Dict[str, Any]:
    out = dict(cfg)
    for name, kind, lo, hi, choices in param_space.PARAMS:
        if rng.random() >= p:
            continue
        if kind == "float":
            sigma = 0.1 * (hi - lo)
            out[name] = float(np.clip(out[name] + np_rng.normal(0, sigma), lo, hi))
        elif kind == "int":
            sigma = 0.1 * (hi - lo)
            out[name] = int(np.clip(round(out[name] + np_rng.normal(0, sigma)), lo, hi))
        elif kind == "bool":
            out[name] = not out[name]
        elif kind == "cat":
            out[name] = choices[int(np_rng.integers(0, len(choices)))]
    return param_space._post_process(out)


def _tournament(pop: List[Dict[str, Any]], k: int, rng: random.Random) -> Dict[str, Any]:
    """Select one from k random — winner has highest sharpe."""
    contestants = rng.sample(pop, min(k, len(pop)))
    return max(contestants, key=lambda r: r["fitness"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-generations", type=int, default=100)
    parser.add_argument("--population-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=45)
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage7_ga.jsonl"))
    parser.add_argument("--input-stages", nargs="+",
                        default=[str(SWEEP_DIR / "stage4_tpe.jsonl"),
                                 str(SWEEP_DIR / "stage5_nsga.jsonl"),
                                 str(SWEEP_DIR / "stage6_cmaes.jsonl")])
    parser.add_argument("--max-workers", type=int, default=None)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    np_rng = np.random.default_rng(args.seed)

    # Seed pool
    pooled_rows = []
    for path in args.input_stages:
        p = Path(path)
        if p.exists():
            pooled_rows.extend(_runner.read_jsonl(p))
    eligible = [r for r in pooled_rows if r["result"].get("min_fold_n_trades", 0) >= 30]
    eligible.sort(key=lambda r: r["result"].get("cross_fold_sharpe", -1e9), reverse=True)
    seed_configs = [r["config"] for r in eligible[:200]]
    for c in _runner.lifetime_top_configs(META_DIR, top_pct=0.05)[:50]:
        seed_configs.append(c)
    if len(seed_configs) < 10:
        print("  Stage 7: not enough seed configs from prior stages — falling back to random.")
        seed_configs = [param_space.random_sample(np_rng) for _ in range(args.population_size)]
    print(f"  Stage 7: GA seeded with {len(seed_configs)} configs.")

    # Initial population
    pop = []
    for cfg in seed_configs[: args.population_size]:
        pop.append({"config": cfg, "fitness": 0.0})
    while len(pop) < args.population_size:
        pop.append({"config": param_space.random_sample(np_rng), "fitness": 0.0})

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)

    all_rows = []
    seen_ids = set()
    for gen in range(args.n_generations):
        # Evaluate any not-yet-evaluated
        to_eval = [m["config"] for m in pop if m["fitness"] == 0.0]
        if to_eval:
            new_rows = _runner.evaluate_configs(
                to_eval, pool_args, max_workers=args.max_workers, label=f"GA-gen{gen}",
            )
            for member, row in zip([m for m in pop if m["fitness"] == 0.0], new_rows):
                cid = row["config_id"]
                member["fitness"] = (
                    -1e9 if row["result"].get("min_fold_n_trades", 0) < 30
                    else row["result"].get("cross_fold_sharpe", -1e9)
                )
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    all_rows.append(row)
        # Survive top-50%, breed for the rest
        pop.sort(key=lambda m: m["fitness"], reverse=True)
        n_keep = len(pop) // 2
        survivors = pop[:n_keep]
        # Produce children
        children = []
        while len(children) < (args.population_size - n_keep):
            a = _tournament(survivors, 3, rng)
            b = _tournament(survivors, 3, rng)
            child = _crossover(a["config"], b["config"], rng)
            child = _mutate(child, rng, np_rng, p=0.10)
            children.append({"config": child, "fitness": 0.0})
        pop = survivors + children
        if gen % 10 == 0:
            top = max(pop, key=lambda m: m["fitness"])
            print(f"  Stage 7: gen {gen} | best sharpe = {top['fitness']:.3f}")

    _runner.write_jsonl(all_rows, Path(args.out))
    print(f"  Stage 7: wrote {len(all_rows)} GA rows → {args.out}")


if __name__ == "__main__":
    main()
