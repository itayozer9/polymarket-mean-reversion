"""Backtest the exact strategies in strategies.yaml on a given date range.

Unlike backtest_sweep.py (which scans a parameter grid), this script runs the
EXACT configs we'd run live, with the same fills (`realistic_fill_model=True`,
fee 0.07, etc.).

Usage:
    uv run python scripts/backtest_yaml.py \
        --strategies cfg_max_pnl_v1,cfg_max_pnl_v2,cfg_max_pnl_v3,cfg_balanced_v1,cfg_high_vol_wr,cfg_late_panic_v1 \
        --date-start 2026-05-15 --date-end 2026-05-16
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mean_reversion_live.adapters import arb_imports  # noqa: F401,E402
from scripts.mean_reversion import loaders as _arb_loaders  # noqa: E402

COMBINED_DIR = ROOT / "data" / "backtest_combined"
_arb_loaders.DATA_DIR = str(COMBINED_DIR)
_arb_loaders.OUTCOMES_FILE = str(COMBINED_DIR / "outcomes.csv")

from mean_reversion_live.adapters.arb_imports import (  # noqa: E402
    EntryParams, ExitParams, FilterParams, FillParams, HumanParams, Portfolio,
    SimConfig, iter_markets, load_outcomes,
)
from scripts.mean_reversion.simulate import simulate_market  # noqa: E402

WINDOW_SEC_15M = 900


def run_market_with_reentry(slug, arr, cfg, wsec, portfolio, rng, outcome, max_iter=20):
    """Re-call simulate_market on remaining-tick slices until the window is exhausted."""
    start_idx = 0
    n_before = len(portfolio.trades)
    iters = 0
    while start_idx < len(arr) - 30 and iters < max_iter:
        sl = arr[start_idx:]
        if len(sl) < 30:
            break
        before = len(portfolio.trades)
        simulate_market(slug, sl, cfg, wsec, portfolio, rng, outcome)
        after = len(portfolio.trades)
        if after == before:
            break
        last = portfolio.trades[-1]
        offsets = np.where(arr["timestamp_ms"][start_idx:] > last.exit_ts_ms)[0]
        if len(offsets) == 0:
            break
        new_start = start_idx + int(offsets[0])
        if new_start <= start_idx:
            break
        start_idx = new_start
        iters += 1
    return len(portfolio.trades) - n_before


def build_cfg(entry: dict) -> SimConfig:
    s = entry["sim_config"]
    return SimConfig(
        entry=EntryParams(**s["entry"]),
        exit=ExitParams(**s["exit"]),
        filter=FilterParams(**s["filter"]),
        human=HumanParams(**s["human"]),
        fill=FillParams(**s["fill"]),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", required=True, help="comma-separated yaml ids")
    ap.add_argument("--symbols", default="btc,eth,sol,xrp")
    ap.add_argument("--date-start", required=True)
    ap.add_argument("--date-end", required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    yaml_path = ROOT / "strategies.yaml"
    strats_yaml = yaml.safe_load(open(yaml_path))
    by_id = {s["id"]: s for s in strats_yaml}
    requested = [s.strip() for s in args.strategies.split(",")]
    symbols = [s.strip() for s in args.symbols.split(",")]

    missing = [s for s in requested if s not in by_id]
    if missing:
        raise SystemExit(f"Unknown strategy ids: {missing}")

    outcomes = load_outcomes()
    print(f"Backtest: {args.date_start} → {args.date_end}, symbols={symbols}")
    print(f"Strategies: {requested}")

    # Cache market data once across all configs
    print("Loading market data...")
    markets_cache = {}
    for sym in symbols:
        ms = list(iter_markets("15m", sym, args.date_start, args.date_end))
        markets_cache[sym] = ms
        print(f"  {sym}: {len(ms)} markets, {sum(len(a) for _,a in ms):,} ticks")

    print(f"\n{'='*90}")
    print(f"{'strategy':24} | {'n':>4} | {'WR':>6} | {'PnL$':>9} | {'fees$':>7} | {'gross$':>9} | {'tpw':>4} | {'max_tpw':>7}")
    print("-" * 90)

    grand_total = {"trades": 0, "pnl": 0.0, "wins": 0}
    per_sym_totals = {sym: {"trades": 0, "pnl": 0.0, "wins": 0} for sym in symbols}

    for sid in requested:
        entry = by_id[sid]
        cfg = build_cfg(entry)
        all_trades = []
        tpw_all = []
        for sym in symbols:
            rng = np.random.default_rng(args.seed)
            pf = Portfolio(human=cfg.human, bankroll=float(entry.get("starting_capital_usd", 1000.0)))
            for slug, arr in markets_cache[sym]:
                out = outcomes.get(slug)
                n_added = run_market_with_reentry(
                    slug, arr, cfg, WINDOW_SEC_15M, pf, rng, out
                )
                if n_added > 0:
                    tpw_all.append(n_added)
            all_trades.extend(pf.trades)
            per_sym_totals[sym]["trades"] += pf.n_trades
            per_sym_totals[sym]["pnl"] += pf.total_pnl
            per_sym_totals[sym]["wins"] += pf.wins
        n = len(all_trades)
        wins = sum(1 for t in all_trades if t.pnl > 0)
        pnl = sum(t.pnl for t in all_trades)
        fees = sum(t.fee_total for t in all_trades)
        gross = pnl + fees
        tpw_mean = (sum(tpw_all) / len(tpw_all)) if tpw_all else 0.0
        tpw_max = max(tpw_all) if tpw_all else 0
        wr = wins / n if n else 0.0
        print(f"{sid:24} | {n:>4} | {wr*100:>5.1f}% | {pnl:>9.2f} | {fees:>7.2f} | {gross:>9.2f} | {tpw_mean:>4.2f} | {tpw_max:>7d}")
        grand_total["trades"] += n
        grand_total["pnl"] += pnl
        grand_total["wins"] += wins

    print("-" * 90)
    n = grand_total["trades"]
    wr = grand_total["wins"] / n if n else 0.0
    print(f"{'COMBINED':24} | {n:>4} | {wr*100:>5.1f}% | {grand_total['pnl']:>9.2f}")

    print(f"\nPer-symbol totals across all 6 strategies:")
    for sym in symbols:
        d = per_sym_totals[sym]
        wr = d["wins"] / d["trades"] if d["trades"] else 0.0
        print(f"  {sym}: {d['trades']} trades | {wr*100:.1f}% WR | ${d['pnl']:.2f}")


if __name__ == "__main__":
    main()
