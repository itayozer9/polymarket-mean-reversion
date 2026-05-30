"""Run a list of picked configs over JUST the live tick data and report.

Used as a final sanity check on the strategies from the broad sweep —
confirms they perform well on the data the running bot has been seeing.

Usage:
    uv run python scripts/analysis/validate_picks_live.py --picks runs/preview2_strategies.yaml \
        --date-start 2026-05-15 --date-end 2026-05-17
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import stdev

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mean_reversion_live.adapters import arb_imports  # noqa: F401,E402
from scripts.mean_reversion import loaders as _arb_loaders  # noqa: E402

# Use live-only data dir.
LIVE_DIR = ROOT / "data" / "live"
# Build a tmp dir with only live files
LIVE_ANALYSIS_DIR = ROOT / "data" / "analysis_live_only"
if not LIVE_ANALYSIS_DIR.exists():
    LIVE_ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    # symlink all live files
    for f in LIVE_DIR.glob("*.csv.gz"):
        target = LIVE_ANALYSIS_DIR / f.name
        if not target.exists():
            target.symlink_to(f)
    # outcomes
    target = LIVE_ANALYSIS_DIR / "outcomes.csv"
    if not target.exists():
        target.symlink_to(ROOT / "data" / "analysis_2026-05-17" / "ticks" / "outcomes.csv")

_arb_loaders.DATA_DIR = str(LIVE_ANALYSIS_DIR)
_arb_loaders.OUTCOMES_FILE = str(LIVE_ANALYSIS_DIR / "outcomes.csv")

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


def build_cfg(d: dict) -> SimConfig:
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
    ap.add_argument("--picks", required=True, help="YAML file with picks (output of portfolio builder)")
    ap.add_argument("--date-start", default="2026-05-15")
    ap.add_argument("--date-end", default="2026-05-17")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    picks = yaml.safe_load(open(args.picks))
    print(f"Loaded {len(picks)} picks.")

    outcomes = load_outcomes()
    markets = {}
    print("Loading live market data...")
    for sym in SYMBOLS:
        markets[sym] = list(iter_markets("15m", sym, args.date_start, args.date_end))
        print(f"  {sym}: {len(markets[sym])} markets")

    print(f"\n{'id':40} {'n':>4} {'WR':>5} {'pnl':>8} {'sharpe':>6}  per-coin")
    print("-" * 100)
    results = []
    for pick in picks:
        cfg = build_cfg(pick)
        all_trades = []
        per_coin = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
        per_day = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
        for sym in SYMBOLS:
            rng = np.random.default_rng(args.seed)
            pf = Portfolio(human=cfg.human, bankroll=1000.0)
            for slug, arr in markets[sym]:
                run_market_with_reentry(slug, arr, cfg, pf, rng, outcomes.get(slug))
            for t in pf.trades:
                all_trades.append(t)
                per_coin[sym]["trades"] += 1
                per_coin[sym]["pnl"] += t.pnl
                d = dt.datetime.utcfromtimestamp(t.entry_ts_ms/1000).strftime("%Y-%m-%d")
                per_day[d]["trades"] += 1
                per_day[d]["pnl"] += t.pnl
                if t.pnl > 0: per_day[d]["wins"] += 1
        n = len(all_trades)
        if n == 0:
            print(f"{pick['id']:40} (no trades)")
            continue
        pnl = sum(t.pnl for t in all_trades)
        wins = sum(1 for t in all_trades if t.pnl > 0)
        pnls = [t.pnl for t in all_trades]
        std = stdev(pnls) if n >= 2 else 1.0
        sh = (pnl / n) / std if std > 1e-9 else 0.0
        coin_str = " ".join(f"{c}:{per_coin[c]['trades']}/${per_coin[c]['pnl']:.0f}" for c in SYMBOLS)
        print(f"{pick['id']:40} {n:>4} {wins/n*100:>4.0f}% {pnl:>8.1f} {sh:>6.2f}  {coin_str}")
        day_str = " ".join(f"{d}:{v['trades']}t/${v['pnl']:.0f}" for d,v in sorted(per_day.items()))
        print(f"  └─ {day_str}")
        results.append({"id": pick["id"], "n": n, "pnl": pnl, "wr": wins/n, "sharpe": sh,
                        "per_coin": dict(per_coin), "per_day": dict(per_day)})

    Path("runs").mkdir(exist_ok=True)
    with open("runs/live_validation.json", "w") as fh:
        json.dump(results, fh, indent=2, default=lambda o: dict(o) if isinstance(o, defaultdict) else o)


if __name__ == "__main__":
    main()
