"""Chain all 16 sweep_v2 stages with checkpointing.

Usage:
    uv run python scripts/sweep_v2/orchestrate.py --smoke
    uv run python scripts/sweep_v2/orchestrate.py --full
    uv run python scripts/sweep_v2/orchestrate.py --reset-meta --full
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


# Smoke budgets are tiny *intentionally* — the goal is end-to-end pipeline
# verification, not finding real strategies. Smoke also narrows the data scope
# to 2 symbols × 2 days so each config eval is fast.
SMOKE_DATA = ["--symbols", "btc,eth", "--date-start", "2026-05-17", "--date-end", "2026-05-19"]
SMOKE_BUDGETS = {
    "lhs": ["--n-configs", "20"] + SMOKE_DATA,
    "tpe": ["--n-trials", "20"] + SMOKE_DATA,
    "nsga": ["--n-trials", "20"] + SMOKE_DATA,
    "cmaes": ["--n-clusters", "3", "--evals-per-cluster", "10"] + SMOKE_DATA,
    "ga": ["--n-generations", "3", "--population-size", "12"] + SMOKE_DATA,
    "surrogate": ["--n-candidates", "1000", "--top-k-to-eval", "10"] + SMOKE_DATA,
    "validate": SMOKE_DATA,
    "stress": ["--n-seeds", "3"] + SMOKE_DATA,
    "walkforward": SMOKE_DATA,
    "replay_live": SMOKE_DATA,
    "portfolio": SMOKE_DATA,
}


FULL_DATA = ["--symbols", "btc,eth,sol,xrp", "--date-start", "2026-05-15", "--date-end", "2026-05-23"]

# Medium budgets — realistic ~2-3 hr target on a modern laptop. Each per-eval
# pays ~25-40s of real-engine simulator work on full data, so the budgets above
# the "smoke" tier are dominated by the sequential in-process Optuna stages.
MEDIUM_BUDGETS = {
    "lhs": ["--n-configs", "1500"] + FULL_DATA,
    "tpe": ["--n-trials", "100"] + FULL_DATA,
    "nsga": ["--n-trials", "100"] + FULL_DATA,
    "cmaes": ["--n-clusters", "10", "--evals-per-cluster", "50"] + FULL_DATA,
    "ga": ["--n-generations", "8", "--population-size", "50"] + FULL_DATA,
    "surrogate": ["--n-candidates", "100000", "--top-k-to-eval", "500"] + FULL_DATA,
    "validate": FULL_DATA,
    "stress": ["--n-seeds", "8"] + FULL_DATA,
    "walkforward": FULL_DATA,
    "replay_live": FULL_DATA,
    "portfolio": FULL_DATA,
}

# Full budgets — aspirational. At ~30s per eval, this is multi-day compute. The
# in-process TPE stage (30k trials × 30s = 250 hr) is the bottleneck; use --medium
# or break the run into chunks if you actually want it to finish.
FULL_BUDGETS = {
    "lhs": ["--n-configs", "15000"] + FULL_DATA,
    "tpe": ["--n-trials", "30000"] + FULL_DATA,
    "nsga": ["--n-trials", "10000"] + FULL_DATA,
    "cmaes": ["--n-clusters", "50", "--evals-per-cluster", "400"] + FULL_DATA,
    "ga": ["--n-generations", "100", "--population-size", "100"] + FULL_DATA,
    "surrogate": ["--n-candidates", "1000000", "--top-k-to-eval", "10000"] + FULL_DATA,
    "validate": FULL_DATA,
    "stress": ["--n-seeds", "16"] + FULL_DATA,
    "walkforward": FULL_DATA,
    "replay_live": FULL_DATA,
    "portfolio": FULL_DATA,
}


STAGES = [
    ("setup_data", "scripts.sweep_v2.setup_data", []),
    ("folds", "scripts.sweep_v2.folds", []),
    ("features", "scripts.sweep_v2.features", []),
    ("stage3_lhs", "scripts.sweep_v2.run_lhs", "lhs"),
    ("stage4_tpe", "scripts.sweep_v2.run_tpe", "tpe"),
    ("stage5_nsga", "scripts.sweep_v2.run_nsga", "nsga"),
    ("stage6_cmaes", "scripts.sweep_v2.run_cmaes", "cmaes"),
    ("stage7_ga", "scripts.sweep_v2.run_ga", "ga"),
    ("stage8_surrogate", "scripts.sweep_v2.run_surrogate", "surrogate"),
    ("stage9_validate", "scripts.sweep_v2.validate", "validate"),
    ("stage10_stress", "scripts.sweep_v2.stress", "stress"),
    ("stage11_walkforward", "scripts.sweep_v2.walkforward", "walkforward"),
    ("stage12_replay_live", "scripts.sweep_v2.replay_live", "replay_live"),
    ("stage13_replay_march", "scripts.sweep_v2.replay_march", []),
    ("stage14_portfolio", "scripts.sweep_v2.portfolio", "portfolio"),
    ("stage15_report", "scripts.sweep_v2.report", []),
    ("stage16_persist", "scripts.sweep_v2.meta.persist", []),
    ("stage16_distill", "scripts.sweep_v2.meta.distill", []),
]


def run_stage(name: str, module: str, extra_args, env_override=None):
    cmd = ["uv", "run", "python", "-m", module] + list(extra_args)
    print(f"\n{'='*70}\n→ {name}: {' '.join(cmd)}\n{'='*70}", flush=True)
    t0 = time.time()
    res = subprocess.run(cmd, env=env_override)
    elapsed = time.time() - t0
    if res.returncode != 0:
        print(f"  {name} FAILED (exit {res.returncode}) after {elapsed:.0f}s")
        sys.exit(res.returncode)
    print(f"  {name} done in {elapsed:.0f}s", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="Tiny budgets for end-to-end smoke test.")
    parser.add_argument("--medium", action="store_true", help="Realistic 2-3 hr budget on full data (~3,000 evals).")
    parser.add_argument("--full", action="store_true", help="Full ~95k-evaluation budget (multi-day compute).")
    parser.add_argument("--reset-meta", action="store_true",
                        help="Delete data/sweep_v2/meta/ before running (forces cold start).")
    parser.add_argument("--skip", nargs="*", default=[],
                        help="Stage names to skip (e.g. stage5_nsga).")
    parser.add_argument(
        "--from-stage", default=None,
        help="Start from this stage name (resume after a crash).",
    )
    args = parser.parse_args()

    if not args.smoke and not args.full and not args.medium:
        print("Pass --smoke, --medium, or --full.")
        sys.exit(2)

    if args.reset_meta and META_DIR.exists():
        print(f"Removing {META_DIR}")
        shutil.rmtree(META_DIR)

    if args.smoke:
        budgets = SMOKE_BUDGETS
    elif args.medium:
        budgets = MEDIUM_BUDGETS
    else:
        budgets = FULL_BUDGETS

    started = args.from_stage is None
    for name, module, extra_key in STAGES:
        if not started:
            if name == args.from_stage:
                started = True
            else:
                print(f"  (skipping {name} via --from-stage)")
                continue
        if name in args.skip:
            print(f"  (skipping {name} via --skip)")
            continue
        if isinstance(extra_key, str):
            extra_args = budgets.get(extra_key, [])
        else:
            extra_args = extra_key
        run_stage(name, module, extra_args)

    print(f"\n{'='*70}\nAll stages complete.\n{'='*70}")


if __name__ == "__main__":
    main()
