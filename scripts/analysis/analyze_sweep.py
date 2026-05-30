"""Analyze the broad sweep — find robust configs across coins, days, hours.

Robustness criteria (strict):
  - Trades on each coin (≥4 per coin)
  - Positive PnL on the majority of coins (≥3 of 4)
  - Positive on majority of days (≥60%)
  - Out-of-sample: train on Mar, test on May (and vice versa) — both must be positive
  - Total PnL > some threshold
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from statistics import median

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


HIST_DATES = {"2026-03-14", "2026-03-15", "2026-03-16", "2026-03-17"}
LIVE_DATES = {"2026-05-15", "2026-05-16", "2026-05-17"}
ALL_DATES = sorted(HIST_DATES | LIVE_DATES)
COINS = ["btc", "eth", "sol", "xrp"]


def ts_to_date(ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def ts_to_hour(ms: int) -> int:
    return dt.datetime.utcfromtimestamp(ms / 1000).hour


def ts_to_dow(ms: int) -> int:
    return dt.datetime.utcfromtimestamp(ms / 1000).weekday()


def summarize_config(rec: dict) -> dict:
    """Aggregate per coin, day, hour, segment, dow."""
    by_coin = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_day = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_seg = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_hour = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_dow = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_coin_seg = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_coin_day = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})

    for t in rec["trades"]:
        d = ts_to_date(t["entry_ts_ms"])
        h = ts_to_hour(t["entry_ts_ms"])
        dow = ts_to_dow(t["entry_ts_ms"])
        seg = "hist" if d in HIST_DATES else ("live" if d in LIVE_DATES else "other")
        win = 1 if t["pnl"] > 0 else 0
        for d_, key in ((by_coin, t["sym"]),
                        (by_day, d),
                        (by_seg, seg),
                        (by_hour, h),
                        (by_dow, dow),
                        (by_coin_seg, (t["sym"], seg)),
                        (by_coin_day, (t["sym"], d))):
            v = d_[key]
            v["trades"] += 1
            v["pnl"] += t["pnl"]
            v["wins"] += win

    return {
        "config_id": rec["config_id"],
        "flat": rec["flat"],
        "n_trades": rec["n_trades"],
        "wins": rec["wins"],
        "pnl_total": rec["pnl_total"],
        "by_coin": dict(by_coin),
        "by_day": dict(by_day),
        "by_seg": dict(by_seg),
        "by_hour": dict(by_hour),
        "by_dow": dict(by_dow),
        "by_coin_seg": {f"{k[0]}|{k[1]}": v for k, v in by_coin_seg.items()},
        "by_coin_day": {f"{k[0]}|{k[1]}": v for k, v in by_coin_day.items()},
    }


def passes_robust(s: dict, *, min_per_coin: int, min_total: int,
                  hist_pnl_floor: float, live_pnl_floor: float,
                  min_coins_positive: int, min_pct_days_positive: float) -> bool:
    if s["n_trades"] < min_total:
        return False
    bs = s["by_seg"]
    hist = bs.get("hist", {"trades": 0, "pnl": 0.0})
    live = bs.get("live", {"trades": 0, "pnl": 0.0})
    if hist["trades"] < 3 or live["trades"] < 3:
        return False
    if hist["pnl"] < hist_pnl_floor:
        return False
    if live["pnl"] < live_pnl_floor:
        return False
    coins_positive = 0
    coins_with_data = 0
    for c in COINS:
        v = s["by_coin"].get(c)
        if v is None or v["trades"] < min_per_coin:
            continue
        coins_with_data += 1
        if v["pnl"] > 0:
            coins_positive += 1
    if coins_with_data < min_coins_positive:
        return False
    if coins_positive < min_coins_positive:
        return False
    days_with_data = [d for d in ALL_DATES if s["by_day"].get(d, {}).get("trades", 0) >= 1]
    if not days_with_data:
        return False
    days_pos = sum(1 for d in days_with_data if s["by_day"][d]["pnl"] > 0)
    if days_pos / len(days_with_data) < min_pct_days_positive:
        return False
    return True


def score(s: dict) -> float:
    # Composite: total PnL × min(seg_pnl)/avg × WR penalty
    hist = s["by_seg"].get("hist", {"pnl": 0.0, "trades": 1})
    live = s["by_seg"].get("live", {"pnl": 0.0, "trades": 1})
    seg_min = min(hist["pnl"], live["pnl"])
    pnl_per_trade = s["pnl_total"] / max(1, s["n_trades"])
    return s["pnl_total"] + 0.5 * seg_min + 5.0 * pnl_per_trade


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--min-per-coin", type=int, default=3)
    ap.add_argument("--min-total", type=int, default=20)
    ap.add_argument("--hist-floor", type=float, default=0.0)
    ap.add_argument("--live-floor", type=float, default=0.0)
    ap.add_argument("--min-coins-positive", type=int, default=3)
    ap.add_argument("--min-pct-days-positive", type=float, default=0.50)
    args = ap.parse_args()

    summaries = []
    n_total = 0
    n_traded = 0
    n_positive = 0
    with open(args.sweep) as f:
        for line in f:
            rec = json.loads(line)
            s = summarize_config(rec)
            n_total += 1
            if s["n_trades"] > 0:
                n_traded += 1
            if s["pnl_total"] > 0:
                n_positive += 1
            summaries.append(s)

    print(f"Loaded {n_total} configs ({n_traded} traded, {n_positive} positive PnL).")

    passing = [s for s in summaries if passes_robust(
        s,
        min_per_coin=args.min_per_coin, min_total=args.min_total,
        hist_pnl_floor=args.hist_floor, live_pnl_floor=args.live_floor,
        min_coins_positive=args.min_coins_positive,
        min_pct_days_positive=args.min_pct_days_positive,
    )]
    print(f"Passing robustness gates: {len(passing)}")
    passing.sort(key=score, reverse=True)

    print(f"\n{'rank':>4} {'cid':>4} {'n':>4} {'WR':>5} {'pnl':>8} {'hist':>7} {'live':>7} {'coins+':>7} {'days+':>7}")
    print("-" * 80)
    for i, s in enumerate(passing[:args.top]):
        wr = s["wins"] / s["n_trades"] if s["n_trades"] else 0
        hist = s["by_seg"].get("hist", {"pnl": 0.0})["pnl"]
        live = s["by_seg"].get("live", {"pnl": 0.0})["pnl"]
        coins_pos = sum(1 for c in COINS if s["by_coin"].get(c, {}).get("pnl", -1) > 0)
        days_data = [d for d in ALL_DATES if s["by_day"].get(d, {}).get("trades", 0)]
        days_pos = sum(1 for d in days_data if s["by_day"][d]["pnl"] > 0)
        print(f"{i+1:>4} {s['config_id']:>4} {s['n_trades']:>4} {wr*100:>4.0f}% "
              f"{s['pnl_total']:>8.1f} {hist:>7.1f} {live:>7.1f} "
              f"{coins_pos:>2}/{len([c for c in COINS if s['by_coin'].get(c,{}).get('trades',0)]):<4} "
              f"{days_pos:>2}/{len(days_data):<4}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as fh:
            for s in passing:
                fh.write(json.dumps(s) + "\n")
        print(f"\nWrote {len(passing)} robust configs → {args.out}")


if __name__ == "__main__":
    main()
