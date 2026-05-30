"""Run each pick 8 times with different RNG seeds on live data.
A robust pick should be positive in ≥6 of 8 seeds with low PnL variance.
This guards against lucky-seed artifacts in the LHS sweep.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mean_reversion_live.adapters import arb_imports  # noqa: F401,E402
from scripts.mean_reversion import loaders as _arb_loaders  # noqa: E402

LIVE_DIR = ROOT / "data" / "analysis_live_only"
_arb_loaders.DATA_DIR = str(LIVE_DIR)
_arb_loaders.OUTCOMES_FILE = str(LIVE_DIR / "outcomes.csv")

from mean_reversion_live.adapters.arb_imports import (  # noqa: E402
    EntryParams, ExitParams, FilterParams, FillParams, HumanParams, Portfolio,
    SimConfig, iter_markets, load_outcomes,
)
from scripts.mean_reversion.simulate import simulate_market  # noqa: E402


WSEC = 900
SYMBOLS = ["btc", "eth", "sol", "xrp"]


def run_market_with_reentry(slug, arr, cfg, pf, rng, outcome, max_iter=12):
    start = 0
    while start < len(arr) - 30:
        sl = arr[start:]
        if len(sl) < 30: break
        before = len(pf.trades)
        simulate_market(slug, sl, cfg, WSEC, pf, rng, outcome)
        after = len(pf.trades)
        if after == before: break
        last = pf.trades[-1]
        offs = np.where(arr["timestamp_ms"][start:] > last.exit_ts_ms)[0]
        if len(offs) == 0: break
        new_start = start + int(offs[0])
        if new_start <= start: break
        start = new_start
        if max_iter <= 0: break
        max_iter -= 1


def build_cfg(d):
    s = d["sim_config"]
    return SimConfig(
        entry=EntryParams(**s["entry"]),
        exit=ExitParams(**s["exit"]),
        filter=FilterParams(**s["filter"]),
        human=HumanParams(**s["human"]),
        fill=FillParams(**s["fill"]),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--picks", required=True)
    ap.add_argument("--date-start", default="2026-05-15")
    ap.add_argument("--date-end", default="2026-05-17")
    ap.add_argument("--seeds", type=int, default=8)
    args = ap.parse_args()

    picks = yaml.safe_load(open(args.picks))
    print(f"Loaded {len(picks)} picks. Running each with {args.seeds} seeds…")

    outcomes = load_outcomes()
    markets = {}
    for sym in SYMBOLS:
        markets[sym] = list(iter_markets("15m", sym, args.date_start, args.date_end))

    seeds = list(range(args.seeds))
    print(f"\n{'id':40} {'mean$':>7} {'std$':>6} {'min$':>7} {'max$':>7} {'pos':>5} {'n_avg':>6}  per-seed")
    print("-" * 130)
    results = []
    for pick in picks:
        cfg = build_cfg(pick)
        seed_pnls = []
        seed_ns = []
        for seed in seeds:
            total_pnl = 0.0
            total_n = 0
            for sym in SYMBOLS:
                rng = np.random.default_rng(seed)
                pf = Portfolio(human=cfg.human, bankroll=1000.0)
                for slug, arr in markets[sym]:
                    run_market_with_reentry(slug, arr, cfg, pf, rng, outcomes.get(slug))
                total_pnl += pf.total_pnl
                total_n += pf.n_trades
            seed_pnls.append(total_pnl)
            seed_ns.append(total_n)
        mn = sum(seed_pnls) / len(seed_pnls)
        sd = statistics.stdev(seed_pnls) if len(seed_pnls) > 1 else 0.0
        positive_seeds = sum(1 for p in seed_pnls if p > 0)
        n_avg = sum(seed_ns) / len(seed_ns)
        seed_str = " ".join(f"{p:+.0f}" for p in seed_pnls)
        print(f"{pick['id']:40} {mn:>7.1f} {sd:>6.1f} {min(seed_pnls):>7.1f} {max(seed_pnls):>7.1f} "
              f"{positive_seeds}/{len(seeds):<3} {n_avg:>6.0f}  [{seed_str}]")
        results.append({
            "id": pick["id"],
            "mean_pnl": mn, "std_pnl": sd,
            "min_pnl": min(seed_pnls), "max_pnl": max(seed_pnls),
            "positive_seeds": positive_seeds, "n_seeds": len(seeds),
            "avg_trades": n_avg,
            "seed_pnls": seed_pnls, "seed_ns": seed_ns,
        })

    Path("runs").mkdir(exist_ok=True)
    with open("runs/seed_stability.json", "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nSaved → runs/seed_stability.json")

    # Summary verdict
    print("\n=== Stability verdict ===")
    print("Pick is STABLE if: ≥6/8 positive seeds AND mean > $20 AND mean > 1.5×std")
    print()
    stable = []
    for r in results:
        is_stable = (r["positive_seeds"] >= 6 and r["mean_pnl"] > 20 and
                     r["mean_pnl"] > 1.5 * r["std_pnl"])
        marker = "STABLE ✓" if is_stable else "weak"
        print(f"  {r['id']:40} mean=${r['mean_pnl']:>+7.1f}  std=${r['std_pnl']:>5.1f}  "
              f"+seeds={r['positive_seeds']}/{r['n_seeds']}  → {marker}")
        if is_stable:
            stable.append(r["id"])
    print(f"\nSTABLE picks: {len(stable)} of {len(results)}")
    for s in stable:
        print(f"  - {s}")


if __name__ == "__main__":
    main()
