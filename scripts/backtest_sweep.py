"""Tunable parameter sweep for mean-reversion strategy hypotheses.

Usage:
    uv run python scripts/backtest_sweep.py --hypothesis H1
    uv run python scripts/backtest_sweep.py --hypothesis ALL --top-k 30

Reads historical (data/historical/) + live (data/live/) ticks from
data/backtest_combined/ (a symlink directory built by data/setup_backtest_dir.sh
or auto-built on first run). Uses polymarket-arb's simulate_market via the
existing arb_imports adapter — NO duplication of state-machine logic.

Multiple entries per 15m window are allowed via a local re-entry loop that
calls simulate_market repeatedly on remaining-tick slices after each exit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, replace
from itertools import product
from pathlib import Path
from typing import Iterable

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Import the adapter first — it patches sys.path so we can reach polymarket-arb.
from mean_reversion_live.adapters import arb_imports  # noqa: E402,F401

# Now redirect the polymarket-arb loaders to our combined dir.
from scripts.mean_reversion import loaders as _arb_loaders  # noqa: E402

COMBINED_DIR = ROOT / "data" / "backtest_combined"
if not COMBINED_DIR.exists():
    raise SystemExit(
        f"Combined data dir not found: {COMBINED_DIR}\n"
        f"Run scripts/setup_backtest_dir.sh to build it."
    )

_arb_loaders.DATA_DIR = str(COMBINED_DIR)
_arb_loaders.OUTCOMES_FILE = str(COMBINED_DIR / "outcomes.csv")

from mean_reversion_live.adapters.arb_imports import (  # noqa: E402
    EntryParams,
    ExitParams,
    FillParams,
    FilterParams,
    HumanParams,
    Portfolio,
    SimConfig,
    iter_markets,
    load_outcomes,
    window_duration_sec,
)
from scripts.mean_reversion.simulate import simulate_market  # noqa: E402

WINDOW_SEC_15M = 900


# ---------------------------------------------------------------------------
# Re-entry loop
# ---------------------------------------------------------------------------

def run_market_with_reentry(
    slug: str,
    arr: np.ndarray,
    cfg: SimConfig,
    wsec: int,
    portfolio: Portfolio,
    rng: np.random.Generator,
    outcome,
    max_iterations: int = 20,
) -> int:
    """Call simulate_market repeatedly on remaining slices.

    simulate_market enforces a per-market lockout after the first trade closes.
    To allow multiple trades inside the same window, after each call we look at
    the last trade's exit timestamp and slice the array forward, then call
    simulate_market again. The Portfolio is reused so daily caps / cooldowns
    still apply across re-entries within the same simulated trader.

    Returns the number of trades added to `portfolio.trades` by this market.
    """
    start_idx = 0
    n_before = len(portfolio.trades)
    iterations = 0
    while start_idx < len(arr) - 30 and iterations < max_iterations:
        slice_arr = arr[start_idx:]
        if len(slice_arr) < 30:
            break
        before = len(portfolio.trades)
        simulate_market(slug, slice_arr, cfg, wsec, portfolio, rng, outcome)
        after = len(portfolio.trades)
        if after == before:
            break
        last_trade = portfolio.trades[-1]
        offsets = np.where(arr["timestamp_ms"][start_idx:] > last_trade.exit_ts_ms)[0]
        if len(offsets) == 0:
            break
        new_start = start_idx + int(offsets[0])
        if new_start <= start_idx:
            break
        start_idx = new_start
        iterations += 1
    return len(portfolio.trades) - n_before


# ---------------------------------------------------------------------------
# Base configs per hypothesis
# ---------------------------------------------------------------------------

def _base_human() -> HumanParams:
    return HumanParams(
        reaction_delay_min_sec=0.5,
        reaction_delay_max_sec=2.0,
        signal_skip_prob=0.10,
        daily_trade_cap=None,
        post_loss_cooldown_sec=0,
        concurrent_position_cap=4,
        fixed_bet_usd=10.0,
    )

def _base_fill() -> FillParams:
    return FillParams(fee_rate=0.072, reject_prob=0.03, use_next_tick_for_fill=True)


def base_h1() -> SimConfig:
    return SimConfig(
        entry=EntryParams(
            side="BOTH", entry_price_min=0.25, entry_price_max=0.40,
            drop_magnitude_pct=15.0, drop_window_sec=90,
            min_time_left_sec=480, proximity_max_pct=100.0,
            min_seconds_into_window=60,
        ),
        exit=ExitParams(
            profit_target_pct=25.0, stop_loss_pct=None,
            max_hold_sec=900, trailing_stop_pct=None,
        ),
        filter=FilterParams(
            min_book_depth_usd=20.0, max_spread=0.05,
            book_imbalance_min=None, vol_regime="ALL", time_of_day="ALL",
            multi_tier_entry=1, correlated_signal_filter=False,
        ),
        human=_base_human(),
        fill=_base_fill(),
    )


def base_h2() -> SimConfig:
    """Deep asymmetric — 200%+ hunt."""
    b = base_h1()
    return replace(
        b,
        entry=replace(
            b.entry,
            entry_price_min=0.08, entry_price_max=0.20,
            drop_magnitude_pct=25.0, drop_window_sec=120,
        ),
        exit=replace(b.exit, profit_target_pct=150.0),
    )


def base_h3() -> SimConfig:
    """Time-of-day liquidity edge."""
    b = base_h1()
    return replace(b, filter=replace(b.filter, time_of_day="ASIA"))


def base_h4() -> SimConfig:
    """Vol-regime split."""
    b = base_h1()
    return replace(b, filter=replace(b.filter, vol_regime="LOW"))


def base_h5() -> SimConfig:
    """Book-imbalance reversal."""
    b = base_h1()
    return replace(b, filter=replace(b.filter, book_imbalance_min=2.0))


def base_h6() -> SimConfig:
    """Tight-spread-only."""
    b = base_h1()
    return replace(b, filter=replace(b.filter, max_spread=0.01))


def base_h7() -> SimConfig:
    """Late-window panic — enter only after 600s into the 15m window."""
    b = base_h1()
    return replace(
        b,
        entry=replace(
            b.entry,
            entry_price_min=0.10, entry_price_max=0.30,
            drop_magnitude_pct=20.0, drop_window_sec=60,
            min_seconds_into_window=600, min_time_left_sec=60,
        ),
        exit=replace(b.exit, profit_target_pct=25.0, max_hold_sec=240),
    )


def base_h1r() -> SimConfig:
    """H1 refined — entry_max=0.45, drop=10, target=25, time_left=300 (top winner)."""
    b = base_h1()
    return replace(
        b,
        entry=replace(
            b.entry,
            entry_price_min=0.25, entry_price_max=0.45,
            drop_magnitude_pct=10.0, drop_window_sec=90,
            min_time_left_sec=300,
        ),
        exit=replace(b.exit, profit_target_pct=25.0),
    )


def base_h7r() -> SimConfig:
    """H7 refined — late-window with the top winner's params."""
    b = base_h7()
    return replace(
        b,
        entry=replace(
            b.entry,
            entry_price_min=0.10, entry_price_max=0.25,
            min_seconds_into_window=600,
        ),
        exit=replace(b.exit, profit_target_pct=40.0, max_hold_sec=240),
    )


def base_hybrid() -> SimConfig:
    """Hybrid — H1 base + HIGH vol regime + tight spread (combine top WR filters)."""
    b = base_h1r()
    return replace(
        b,
        filter=replace(
            b.filter, vol_regime="HIGH", max_spread=0.005,
        ),
    )


# ---------------------------------------------------------------------------
# Sweep grids per hypothesis (small enough to be fast; expand as needed)
# ---------------------------------------------------------------------------

H_GRIDS = {
    "H1": {
        "base": base_h1,
        "sweep": {
            "entry.entry_price_max": [0.35, 0.40, 0.45],
            "entry.drop_magnitude_pct": [10.0, 15.0, 20.0],
            "exit.profit_target_pct": [15.0, 25.0, 40.0],
            "entry.min_time_left_sec": [300, 480],
        },
    },
    "H2": {
        "base": base_h2,
        "sweep": {
            "entry.entry_price_min": [0.05, 0.10],
            "entry.entry_price_max": [0.15, 0.20, 0.25],
            "entry.drop_magnitude_pct": [20.0, 30.0],
            "exit.profit_target_pct": [100.0, 150.0, 300.0],
        },
    },
    "H3": {
        "base": base_h3,
        "sweep": {
            "filter.time_of_day": ["ASIA", "EU", "US", "OVERNIGHT"],
            "entry.drop_magnitude_pct": [10.0, 20.0],
            "exit.profit_target_pct": [25.0, 40.0],
        },
    },
    "H4": {
        "base": base_h4,
        "sweep": {
            "filter.vol_regime": ["LOW", "MED", "HIGH"],
            "entry.drop_magnitude_pct": [10.0, 18.0, 25.0],
            "exit.profit_target_pct": [25.0, 50.0],
        },
    },
    "H5": {
        "base": base_h5,
        "sweep": {
            "filter.book_imbalance_min": [1.5, 2.0, 3.0],
            "entry.entry_price_max": [0.30, 0.40],
            "exit.profit_target_pct": [25.0, 40.0],
        },
    },
    "H6": {
        "base": base_h6,
        "sweep": {
            "filter.max_spread": [0.005, 0.01, 0.02],
            "entry.entry_price_max": [0.30, 0.40],
            "exit.profit_target_pct": [25.0, 40.0],
        },
    },
    "H7": {
        "base": base_h7,
        "sweep": {
            "entry.min_seconds_into_window": [540, 600, 660],
            "entry.entry_price_max": [0.25, 0.35],
            "exit.profit_target_pct": [20.0, 40.0],
            "exit.max_hold_sec": [180, 240],
        },
    },
    # ── Refinement hypotheses (finer grids around top performers) ──
    "H1R": {
        "base": base_h1r,
        "sweep": {
            "entry.entry_price_min": [0.20, 0.25, 0.30],
            "entry.entry_price_max": [0.40, 0.45, 0.50],
            "entry.drop_magnitude_pct": [5.0, 8.0, 10.0, 12.0],
            "exit.profit_target_pct": [15.0, 20.0, 25.0, 30.0],
        },
    },
    "H7R": {
        "base": base_h7r,
        "sweep": {
            "entry.min_seconds_into_window": [540, 600, 660, 720],
            "entry.entry_price_max": [0.20, 0.25, 0.30],
            "exit.profit_target_pct": [25.0, 35.0, 45.0],
            "exit.max_hold_sec": [120, 180, 240],
        },
    },
    "HYB": {
        "base": base_hybrid,
        "sweep": {
            "filter.max_spread": [0.005, 0.01],
            "filter.vol_regime": ["HIGH", "MED", "ALL"],
            "entry.drop_magnitude_pct": [8.0, 10.0, 12.0],
            "exit.profit_target_pct": [20.0, 25.0, 30.0],
        },
    },
}


def _apply_override(cfg: SimConfig, path: str, value):
    """Apply a dotted override like `entry.entry_price_max` to a frozen SimConfig."""
    section, field = path.split(".", 1)
    sub = getattr(cfg, section)
    new_sub = replace(sub, **{field: value})
    return replace(cfg, **{section: new_sub})


def expand_grid(base_fn, sweep: dict):
    """Yield (params_dict, SimConfig) for the cartesian product of the sweep."""
    keys = list(sweep.keys())
    for values in product(*[sweep[k] for k in keys]):
        cfg = base_fn()
        params = {}
        for k, v in zip(keys, values):
            cfg = _apply_override(cfg, k, v)
            params[k] = v
        yield params, cfg


# ---------------------------------------------------------------------------
# Per-config evaluation
# ---------------------------------------------------------------------------

def load_cached_markets(
    symbols: list, date_start: str, date_end: str
) -> dict:
    """Load all (slug, arr) tuples per symbol once. Reused across all configs."""
    cache = {}
    for sym in symbols:
        markets = list(iter_markets("15m", sym, date_start, date_end))
        cache[sym] = markets
        n_ticks = sum(len(arr) for _, arr in markets)
        print(f"  cached {sym}: {len(markets)} markets, {n_ticks:,} ticks")
    return cache


def evaluate_config(
    cfg: SimConfig,
    symbols: list,
    outcomes: dict,
    markets_cache: dict,
    seed: int,
) -> dict:
    """Run one config across all requested symbols. Return per-symbol + aggregate stats."""
    per_symbol = {}
    all_trades = []
    for sym in symbols:
        rng = np.random.default_rng(seed)
        portfolio = Portfolio(human=cfg.human, bankroll=1000.0)
        trades_per_window_counts = []
        for slug, arr in markets_cache[sym]:
            outcome = outcomes.get(slug)
            n_trades_this_market = run_market_with_reentry(
                slug, arr, cfg, WINDOW_SEC_15M, portfolio, rng, outcome,
            )
            if n_trades_this_market > 0:
                trades_per_window_counts.append(n_trades_this_market)
        sym_trades = list(portfolio.trades)
        all_trades.extend(sym_trades)
        per_symbol[sym] = _summarise(sym_trades, trades_per_window_counts)
    aggregate = _summarise(all_trades, sum(
        [per_symbol[s]["_tpw_raw"] for s in symbols], []
    ))
    return {"per_symbol": per_symbol, "aggregate": aggregate}


def _summarise(trades: list, trades_per_window_counts: list) -> dict:
    if not trades:
        return {
            "n_trades": 0, "n_wins": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "net_pnl": 0.0, "total_fees": 0.0,
            "avg_hold_sec": 0.0, "trades_per_window_mean": 0.0,
            "trades_per_window_max": 0,
            "biggest_win": 0.0, "biggest_loss": 0.0,
            "_tpw_raw": [],
        }
    pnls = [t.pnl for t in trades]
    fees = [t.fee_total for t in trades]
    holds = [t.seconds_held for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "n_trades": len(trades),
        "n_wins": wins,
        "win_rate": wins / len(trades),
        "gross_pnl": float(sum(pnls) + sum(fees)),
        "net_pnl": float(sum(pnls)),
        "total_fees": float(sum(fees)),
        "avg_hold_sec": float(sum(holds) / len(holds)),
        "trades_per_window_mean": (
            float(sum(trades_per_window_counts) / len(trades_per_window_counts))
            if trades_per_window_counts else 0.0
        ),
        "trades_per_window_max": (
            max(trades_per_window_counts) if trades_per_window_counts else 0
        ),
        "biggest_win": float(max(pnls)),
        "biggest_loss": float(min(pnls)),
        "_tpw_raw": trades_per_window_counts,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_jsonl(out_path: Path, rows: list):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")


def print_top_table(rows: list, top_k: int, by: str):
    aggregate_rows = [r for r in rows if r["symbol"] == "ALL"]
    aggregate_rows.sort(key=lambda r: r[by], reverse=True)
    top = aggregate_rows[:top_k]
    print(f"\n### Top {top_k} configs by {by}\n")
    print("| Rank | Hyp | config_id | n_trades | WR | net_pnl | gross_pnl | fees | avg_hold_s | tpw_mean | tpw_max | params |")
    print("|------|-----|-----------|---------:|---:|--------:|----------:|-----:|-----------:|---------:|--------:|--------|")
    for i, r in enumerate(top, 1):
        params_str = ", ".join(f"{k.split('.')[-1]}={v}" for k, v in r["params"].items())
        print(
            f"| {i} | {r['hypothesis']} | `{r['config_id']}` | {r['n_trades']} "
            f"| {r['win_rate']*100:.1f}% | ${r['net_pnl']:.2f} | ${r['gross_pnl']:.2f} "
            f"| ${r['total_fees']:.2f} | {r['avg_hold_sec']:.0f} "
            f"| {r['trades_per_window_mean']:.2f} | {r['trades_per_window_max']} "
            f"| {params_str} |"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothesis", default="ALL", help="H1..H7 or ALL")
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-03-04")
    parser.add_argument("--date-end", default="2026-05-16")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSONL path (default: data/analysis/sweep_<H>_<ts>.jsonl)",
    )
    args = parser.parse_args()

    hypotheses = list(H_GRIDS.keys()) if args.hypothesis == "ALL" else [args.hypothesis]
    for h in hypotheses:
        if h not in H_GRIDS:
            raise SystemExit(f"Unknown hypothesis: {h}. Choices: {list(H_GRIDS.keys())} or ALL")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    outcomes = load_outcomes()
    print(f"Loaded {len(outcomes)} outcomes; symbols={symbols}; date={args.date_start}..{args.date_end}")
    print("Caching market data (loads each CSV once)...")
    markets_cache = load_cached_markets(symbols, args.date_start, args.date_end)

    all_rows = []
    t0 = time.time()
    for h in hypotheses:
        spec = H_GRIDS[h]
        configs = list(expand_grid(spec["base"], spec["sweep"]))
        print(f"\n[{h}] sweeping {len(configs)} configs...")
        for ci, (params, cfg) in enumerate(configs, 1):
            cid = cfg.hash_id()
            res = evaluate_config(cfg, symbols, outcomes, markets_cache, args.seed)
            for sym, stats in res["per_symbol"].items():
                row = {
                    "hypothesis": h, "config_id": cid, "symbol": sym,
                    "params": params,
                    **{k: v for k, v in stats.items() if not k.startswith("_")},
                }
                all_rows.append(row)
            agg = res["aggregate"]
            agg_row = {
                "hypothesis": h, "config_id": cid, "symbol": "ALL",
                "params": params,
                **{k: v for k, v in agg.items() if not k.startswith("_")},
            }
            all_rows.append(agg_row)
            if ci % 5 == 0 or ci == len(configs):
                elapsed = time.time() - t0
                print(
                    f"  [{h}] {ci}/{len(configs)} | "
                    f"latest cid={cid} | n_trades={agg['n_trades']} | "
                    f"WR={agg['win_rate']*100:.1f}% | net=${agg['net_pnl']:.2f} | "
                    f"elapsed={elapsed:.0f}s"
                )

    ts = time.strftime("%Y%m%dT%H%M%S")
    out_path = (
        Path(args.output)
        if args.output
        else ROOT / "data" / "analysis" / f"sweep_{args.hypothesis}_{ts}.jsonl"
    )
    write_jsonl(out_path, all_rows)
    print(f"\nWrote {len(all_rows)} rows → {out_path}")

    print_top_table(all_rows, args.top_k, by="net_pnl")
    print_top_table(all_rows, args.top_k, by="win_rate")


if __name__ == "__main__":
    main()
