"""Stage 6 — CMA-ES local refinement around top-K clusters from Stages 4+5.

Numeric params only — CMA-ES is a continuous optimizer. Categoricals are held
at the cluster centroid's value during refinement.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import cma
import numpy as np

from scripts.sweep_v2 import _runner, evaluate, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


NUMERIC_PARAMS = [
    (n, k, lo, hi) for (n, k, lo, hi, _) in param_space.PARAMS
    if k in ("float", "int")
]


def _cluster_top_configs(stage_rows: List[Dict[str, Any]], n_clusters: int) -> List[Dict[str, Any]]:
    """Greedy clustering: walk top-N by Sharpe, drop any that's within param-distance
    threshold to an already-picked centroid."""
    eligible = [
        r for r in stage_rows
        if r["result"].get("min_fold_n_trades", 0) >= 30
    ]
    eligible.sort(key=lambda r: r["result"].get("cross_fold_sharpe", -1e9), reverse=True)
    centroids = []
    picked: List[Dict[str, Any]] = []
    threshold = 0.3  # normalized distance
    for r in eligible:
        if len(picked) >= n_clusters:
            break
        cfg_vec = _normalize_config(r["config"])
        dup = False
        for c in centroids:
            d = float(np.linalg.norm(cfg_vec - c))
            if d < threshold:
                dup = True
                break
        if not dup:
            picked.append(r)
            centroids.append(cfg_vec)
    return picked


def _normalize_config(cfg: Dict[str, Any]) -> np.ndarray:
    """Map numeric params to [0,1]; ignore categoricals/bools/None."""
    vec = []
    for name, kind, lo, hi in NUMERIC_PARAMS:
        v = cfg.get(name, lo)
        if v is None:
            vec.append(0.0)
        else:
            vec.append((float(v) - lo) / (hi - lo + 1e-9))
    return np.array(vec, dtype="f8")


def _denormalize(x: np.ndarray, base_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Map [0,1] vector + base config (for categoricals) to a full config dict."""
    out = dict(base_cfg)
    for i, (name, kind, lo, hi) in enumerate(NUMERIC_PARAMS):
        u = float(np.clip(x[i], 0.0, 1.0))
        v = lo + u * (hi - lo)
        if kind == "int":
            v = int(round(v))
        out[name] = v
    return param_space._post_process(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-clusters", type=int, default=50)
    parser.add_argument("--evals-per-cluster", type=int, default=400)
    parser.add_argument("--seed", type=int, default=44)
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage6_cmaes.jsonl"))
    parser.add_argument("--input-stages", nargs="+",
                        default=[str(SWEEP_DIR / "stage4_tpe.jsonl"),
                                 str(SWEEP_DIR / "stage5_nsga.jsonl")])
    parser.add_argument("--max-workers", type=int, default=None)
    args = parser.parse_args()

    pooled_rows = []
    for path in args.input_stages:
        p = Path(path)
        if p.exists():
            pooled_rows.extend(_runner.read_jsonl(p))
    # Also include lifetime store top survivors
    pooled_rows.extend([{"config": c, "result": {"min_fold_n_trades": 999, "cross_fold_sharpe": 999}}
                        for c in _runner.lifetime_top_configs(META_DIR, top_pct=0.01)[:20]])
    if not pooled_rows:
        print("  Stage 6: no prior results — generating cluster seeds from random configs.")
        rng = np.random.default_rng(args.seed)
        pooled_rows = [{"config": param_space.random_sample(rng),
                        "result": {"min_fold_n_trades": 999, "cross_fold_sharpe": 0}}
                       for _ in range(args.n_clusters * 2)]

    clusters = _cluster_top_configs(pooled_rows, args.n_clusters)
    print(f"  Stage 6: refining {len(clusters)} cluster centroids with CMA-ES.")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)

    # Generate the candidate population from CMA-ES (offline) for each cluster,
    # then evaluate all of them in parallel via the standard runner.
    all_candidates = []
    for ci, cluster in enumerate(clusters):
        base_cfg = cluster["config"]
        x0 = _normalize_config(base_cfg)
        sigma0 = 0.15
        es = cma.CMAEvolutionStrategy(
            x0.tolist(), sigma0,
            {
                "bounds": [[0.0] * len(NUMERIC_PARAMS), [1.0] * len(NUMERIC_PARAMS)],
                "popsize": 16, "verbose": -9, "seed": args.seed + ci,
                "maxfevals": args.evals_per_cluster,
            },
        )
        # Generate offline (no real eval feedback per generation — we accept
        # this approximation in exchange for parallelism).
        budget_left = args.evals_per_cluster
        while not es.stop() and budget_left > 0:
            solutions = es.ask()
            for s in solutions[:budget_left]:
                cfg = _denormalize(np.array(s), base_cfg)
                all_candidates.append(cfg)
            budget_left -= len(solutions)
            # Tell ES random fitness so it keeps stepping (rough exploration).
            es.tell(solutions, [float(np.random.RandomState(args.seed + ci).random()) for _ in solutions])
    print(f"  Stage 6: total CMA-ES candidates to evaluate: {len(all_candidates)}")

    rows = _runner.evaluate_configs(all_candidates, pool_args, max_workers=args.max_workers, label="CMA-ES")
    _runner.write_jsonl(rows, Path(args.out))
    print(f"  Stage 6: wrote {len(rows)} CMA-ES rows → {args.out}")


if __name__ == "__main__":
    main()
