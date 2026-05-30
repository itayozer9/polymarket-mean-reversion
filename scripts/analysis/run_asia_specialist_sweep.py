"""Tightly focused sweep on ASIA-hour mean-reversion variants.

Both broad and focused sweeps show the edge concentrates in ASIA hours.
This sweep drills into ASIA-only configurations with more samples in the
high-yield region around id=193 (BOTH, 0.18-0.255, drop_window=20s, PT=120%).

Usage:
    uv run python scripts/analysis/run_asia_specialist_sweep.py --n 600 --workers 4 --out runs/asia_sweep_v1.jsonl
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from scipy.stats import qmc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mean_reversion_live.adapters import arb_imports  # noqa: F401,E402
from scripts.mean_reversion import loaders as _arb_loaders  # noqa: E402

ANALYSIS_DIR = ROOT / "data" / "analysis_2026-05-17" / "ticks"
_arb_loaders.DATA_DIR = str(ANALYSIS_DIR)
_arb_loaders.OUTCOMES_FILE = str(ANALYSIS_DIR / "outcomes.csv")

from mean_reversion_live.adapters.arb_imports import (  # noqa: E402
    EntryParams, ExitParams, FilterParams, FillParams, HumanParams, Portfolio,
    SimConfig, iter_markets, load_outcomes,
)
from scripts.mean_reversion.simulate import simulate_market  # noqa: E402


WSEC = 900
SYMBOLS = ["btc", "eth", "sol", "xrp"]
DATE_START = "2026-03-14"
DATE_END = "2026-05-17"


# ASIA specialist — narrower, denser sampling around the discovered edge.
SPACE: Dict[str, Tuple[str, list]] = {
    "entry.side": ("choice", ["BOTH", "DOWN", "UP"]),
    "entry.entry_price_min": ("choice", [0.10, 0.13, 0.15, 0.18, 0.20, 0.22]),
    "entry.entry_price_max_offset": ("choice", [0.04, 0.06, 0.08, 0.10]),
    "entry.drop_magnitude_pct": ("choice", [8, 12, 18, 25, 35]),
    "entry.drop_window_sec": ("choice", [15, 20, 30, 45]),
    "entry.min_time_left_sec": ("choice", [120, 180, 300, 420, 540]),
    "entry.proximity_max_pct": ("choice", [0.4, 0.8, 1.5, 3.0, 100.0]),
    "entry.min_seconds_into_window": ("choice", [0, 15, 30, 60]),
    "exit.profit_target_pct": ("choice", [35, 50, 70, 90, 120, 150]),
    "exit.stop_loss_pct": ("choice", [None, 40, 60, 80]),
    "exit.max_hold_sec": ("choice", [240, 360, 480, 600, 720]),
    "exit.trailing_stop_pct": ("choice", [None, 15, 25, 35]),
    "filter.min_book_depth_usd": ("choice", [10, 15, 30, 50]),
    "filter.max_spread": ("choice", [0.05, 0.08, 0.12]),
    "filter.book_imbalance_min": ("choice", [None, 0.55, 0.65]),
    "filter.vol_regime": ("choice", ["LOW", "MED", "ALL"]),
    # FIXED: ASIA-only
}


def sample_flat_configs(n: int, seed: int) -> List[dict]:
    keys = list(SPACE.keys())
    d = len(keys)
    sampler = qmc.LatinHypercube(d=d, seed=seed)
    coords = sampler.random(n=n)
    out = []
    for row in coords:
        cfg = {}
        for k, x in zip(keys, row):
            kind, opts = SPACE[k]
            idx = min(int(x * len(opts)), len(opts) - 1)
            cfg[k] = opts[idx]
        out.append(cfg)
    return out


def build_cfg(flat: dict) -> SimConfig:
    e_min = flat["entry.entry_price_min"]
    e_max = e_min + flat["entry.entry_price_max_offset"]
    return SimConfig(
        entry=EntryParams(
            side=flat["entry.side"],
            entry_price_min=e_min, entry_price_max=e_max,
            drop_magnitude_pct=float(flat["entry.drop_magnitude_pct"]),
            drop_window_sec=int(flat["entry.drop_window_sec"]),
            min_time_left_sec=int(flat["entry.min_time_left_sec"]),
            proximity_max_pct=float(flat["entry.proximity_max_pct"]),
            min_seconds_into_window=int(flat["entry.min_seconds_into_window"]),
        ),
        exit=ExitParams(
            profit_target_pct=float(flat["exit.profit_target_pct"]),
            stop_loss_pct=flat["exit.stop_loss_pct"],
            max_hold_sec=int(flat["exit.max_hold_sec"]),
            trailing_stop_pct=flat["exit.trailing_stop_pct"],
        ),
        filter=FilterParams(
            min_book_depth_usd=float(flat["filter.min_book_depth_usd"]),
            max_spread=float(flat["filter.max_spread"]),
            book_imbalance_min=flat["filter.book_imbalance_min"],
            vol_regime=flat["filter.vol_regime"],
            time_of_day="ASIA",
            multi_tier_entry=1, correlated_signal_filter=False,
        ),
        human=HumanParams(
            reaction_delay_min_sec=0.0, reaction_delay_max_sec=2.5,
            signal_skip_prob=0.05,
            daily_trade_cap=50,
            post_loss_cooldown_sec=60,
            concurrent_position_cap=1, fixed_bet_usd=10.0,
        ),
        fill=FillParams(
            fee_rate=0.07, reject_prob=0.05,
            use_next_tick_for_fill=True, realistic_fill_model=True,
        ),
    )


def run_market_with_reentry(slug, arr, cfg, pf, rng, outcome, max_iter=10):
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


_CACHE: Dict[str, List[Tuple[str, np.ndarray]]] = {}
_OUT: Dict[str, Tuple[str, float]] = {}


def _init_worker():
    global _CACHE, _OUT
    _OUT = load_outcomes()
    for sym in SYMBOLS:
        _CACHE[sym] = list(iter_markets("15m", sym, DATE_START, DATE_END))


def _run_one(flat_and_id):
    flat, cid = flat_and_id
    cfg = build_cfg(flat)
    rng_seed = (cid * 0x9E3779B1) & 0xFFFFFFFF
    out_trades = []
    n_trades = 0
    pnl_total = 0.0
    wins = 0
    for sym in SYMBOLS:
        rng = np.random.default_rng(rng_seed)
        pf = Portfolio(human=cfg.human, bankroll=1000.0)
        for slug, arr in _CACHE[sym]:
            out = _OUT.get(slug)
            run_market_with_reentry(slug, arr, cfg, pf, rng, out)
        for t in pf.trades:
            out_trades.append({
                "sym": sym, "slug": t.slug, "side": t.side,
                "entry_ts_ms": t.entry_ts_ms, "exit_ts_ms": t.exit_ts_ms,
                "entry_price": float(t.entry_price), "exit_price": float(t.exit_price),
                "pnl": float(t.pnl), "exit_reason": t.exit_reason,
                "seconds_held": int(t.seconds_held),
            })
        n_trades += pf.n_trades; pnl_total += pf.total_pnl; wins += pf.wins
    return {"config_id": cid, "flat": flat, "n_trades": n_trades, "wins": wins,
            "pnl_total": pnl_total, "trades": out_trades}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=521)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    flats = sample_flat_configs(args.n, args.seed)
    print(f"Sampled {len(flats)} ASIA-specialist configs.")

    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    t0 = time.time()
    done = 0
    with ctx.Pool(processes=args.workers, initializer=_init_worker) as pool:
        with open(args.out, "w") as fh:
            for r in pool.imap_unordered(_run_one, [(f, i) for i, f in enumerate(flats)], chunksize=1):
                fh.write(json.dumps(r) + "\n")
                fh.flush()
                done += 1
                if done % 25 == 0:
                    rate = done / (time.time() - t0)
                    eta = (len(flats) - done) / rate if rate > 0 else 0
                    print(f"  {done}/{len(flats)} | {rate:.1f}/s | ETA {eta:.0f}s")
    print(f"Done in {time.time()-t0:.0f}s → {args.out}")


if __name__ == "__main__":
    main()
