"""Stage 3 — Broad Latin Hypercube sweep.

Reads `data/sweep_v2/meta/viable_region_priors.json` (if present) to narrow
per-param ranges. First iteration uses full bounds.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.sweep_v2 import _runner, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-configs", type=int, default=15000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage3_lhs.jsonl"))
    parser.add_argument("--max-workers", type=int, default=None)
    args = parser.parse_args()

    priors = _runner.viable_priors_from_meta(META_DIR)
    if priors:
        print(f"  Stage 3: applying viable-region priors over {len(priors)} params from meta-store.")
    else:
        print("  Stage 3: no priors — using full param bounds (first iteration).")

    configs = param_space.lhs_samples(args.n_configs, seed=args.seed, priors=priors)
    print(f"  Stage 3: sampled {len(configs)} configs via LHS.")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)

    rows = _runner.evaluate_configs(configs, pool_args, max_workers=args.max_workers, label="LHS")
    _runner.write_jsonl(rows, Path(args.out))
    print(f"  Stage 3: wrote {len(rows)} rows → {args.out}")


if __name__ == "__main__":
    main()
