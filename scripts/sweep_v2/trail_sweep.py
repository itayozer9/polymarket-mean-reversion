"""Phase 1 — fine-grid sweep of `exit.trailing_stop_pct` on top of the winner.

Holds all other params equal to the winner's; varies only trailing_stop_pct
across a denser grid than the random sampler used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from scripts.sweep_v2 import _runner, evaluate, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
PROPOSED_YAML = ROOT / "proposed_strategies_v3.yaml"


TRAIL_GRID = [None, 5.0, 8.0, 10.0, 12.0, 15.0, 18.0, 22.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0, 80.0]


def load_winner_config() -> Dict[str, Any]:
    """Read the winner from proposed_strategies_v3.yaml."""
    import yaml
    p = yaml.safe_load(PROPOSED_YAML.read_text())
    if not p.get("strategies"):
        raise SystemExit("No survivors in proposed_strategies_v3.yaml — run lenient_promote first.")
    return dict(p["strategies"][0]["config"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "trail_sweep.jsonl"))
    args = parser.parse_args()

    base = load_winner_config()
    print(f"  Base winner config_id: {param_space.hash_id(base)}")

    configs = []
    for v in TRAIL_GRID:
        c = dict(base)
        c["exit.trailing_stop_pct"] = v
        configs.append(c)
    print(f"  Trail-sweep: {len(configs)} configs (trailing_stop_pct ∈ {TRAIL_GRID})")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)
    rows = _runner.evaluate_configs(configs, pool_args, label="trail")
    _runner.write_jsonl(rows, Path(args.out))

    print("\n  Trail-sweep summary (sorted by cross-fold Sharpe):")
    print("  " + "─" * 80)
    print(f"  {'trail_pct':>10}  {'n_trades':>9}  {'pooled_pnl':>12}  {'sharpe':>8}  {'folds+':>7}")
    print("  " + "─" * 80)
    sorted_rows = sorted(
        rows, key=lambda r: r["result"].get("cross_fold_sharpe", -1e9), reverse=True,
    )
    for r in sorted_rows:
        cfg = r["config"]
        res = r["result"]
        trail = cfg.get("exit.trailing_stop_pct")
        trail_str = "None" if trail is None else f"{trail:.0f}%"
        per_fold = res.get("per_fold", [])
        folds_pos = sum(1 for f in per_fold if f.get("net_pnl", 0) > 0)
        print(
            f"  {trail_str:>10}  {res['pooled']['n_trades']:>9}  "
            f"${res['pooled']['net_pnl']:>10.2f}   "
            f"{res.get('cross_fold_sharpe', 0):>7.3f}   {folds_pos}/5"
        )
    print("  " + "─" * 80)


if __name__ == "__main__":
    main()
