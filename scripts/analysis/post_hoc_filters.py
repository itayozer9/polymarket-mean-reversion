"""Post-hoc filter analyzer.

Given a sweep with per-trade details, applies post-hoc filters on entry
timestamps (hour, day-of-week, coin, segment) and re-evaluates each config
as if those filters had been part of it.

This is way faster than running a separate sweep per filter, and lets us
discover sub-regime specialists from a single broad sweep.

Usage:
    uv run python scripts/analysis/post_hoc_filters.py \
        --sweep runs/broad_sweep_v1.jsonl \
        --out runs/post_hoc_v1.jsonl
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

HIST_DATES = {"2026-03-14", "2026-03-15", "2026-03-16", "2026-03-17"}
LIVE_DATES = {"2026-05-15", "2026-05-16", "2026-05-17"}
ALL_DATES = sorted(HIST_DATES | LIVE_DATES)
COINS = ["btc", "eth", "sol", "xrp"]


def ts_to_date(ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def hour_bucket(ms: int) -> str:
    h = dt.datetime.utcfromtimestamp(ms / 1000).hour
    if 0 <= h < 8:
        return "ASIA"
    if 8 <= h < 14:
        return "EU"
    if 14 <= h < 22:
        return "US"
    return "OVERNIGHT"


def dow(ms: int) -> int:
    return dt.datetime.utcfromtimestamp(ms / 1000).weekday()


def apply_filter(trades: List[dict], pred) -> List[dict]:
    return [t for t in trades if pred(t)]


def metrics(trades: List[dict]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "sharpe": 0.0, "hist": 0.0, "live": 0.0,
                "by_coin_pnl": {c: 0.0 for c in COINS},
                "by_day_pos": 0, "by_day_data": 0}
    pnls = [t["pnl"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    pnl = sum(pnls)
    std = statistics.stdev(pnls) if n >= 2 else 1.0
    sh = (pnl / n) / std if std > 1e-9 else 0.0
    hist = sum(t["pnl"] for t in trades if ts_to_date(t["entry_ts_ms"]) in HIST_DATES)
    live = sum(t["pnl"] for t in trades if ts_to_date(t["entry_ts_ms"]) in LIVE_DATES)
    by_coin = defaultdict(float)
    for t in trades:
        by_coin[t["sym"]] += t["pnl"]
    day_pnls = defaultdict(float)
    day_trades = defaultdict(int)
    for t in trades:
        d = ts_to_date(t["entry_ts_ms"])
        day_pnls[d] += t["pnl"]
        day_trades[d] += 1
    days_data = sum(1 for d in ALL_DATES if day_trades.get(d, 0) > 0)
    days_pos = sum(1 for d in ALL_DATES if day_pnls.get(d, 0.0) > 0)
    return {
        "n": n, "pnl": pnl, "wr": wins / n, "sharpe": sh, "hist": hist, "live": live,
        "by_coin_pnl": {c: by_coin[c] for c in COINS},
        "by_day_pos": days_pos, "by_day_data": days_data,
    }


def evaluate_with_filters(rec: dict) -> List[dict]:
    """Return a list of filter-variant rows."""
    out = []
    trades = rec["trades"]
    flat = rec["flat"]
    cid = rec["config_id"]

    variants: List[Tuple[str, callable]] = [
        ("ALL", lambda t: True),
        ("hour:ASIA", lambda t: hour_bucket(t["entry_ts_ms"]) == "ASIA"),
        ("hour:EU", lambda t: hour_bucket(t["entry_ts_ms"]) == "EU"),
        ("hour:US", lambda t: hour_bucket(t["entry_ts_ms"]) == "US"),
        ("hour:OVERNIGHT", lambda t: hour_bucket(t["entry_ts_ms"]) == "OVERNIGHT"),
        ("dow:weekday", lambda t: dow(t["entry_ts_ms"]) < 5),
        ("dow:weekend", lambda t: dow(t["entry_ts_ms"]) >= 5),
    ]
    coin_variants = [(f"coin:{c}", lambda t, c=c: t["sym"] == c) for c in COINS]
    variants += coin_variants

    for label, pred in variants:
        sub = apply_filter(trades, pred)
        m = metrics(sub)
        if m["n"] >= 5:
            out.append({
                "config_id": cid,
                "filter": label,
                "flat": flat,
                **m,
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweeps", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out_rows = []
    total = 0
    for path in args.sweeps:
        with open(path) as f:
            for line in f:
                total += 1
                rec = json.loads(line)
                if not rec.get("trades"):
                    continue
                rows = evaluate_with_filters(rec)
                out_rows.extend(rows)
    print(f"Evaluated {total} sweep configs → {len(out_rows)} (config, filter) rows.")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as fh:
        for r in out_rows:
            fh.write(json.dumps(r) + "\n")

    # Quick top-N
    out_rows.sort(key=lambda r: r["pnl"], reverse=True)
    print(f"\nTop 30 (config, filter) by total PnL:")
    print(f"{'cid':>5} {'filter':>15} {'n':>4} {'WR%':>5} {'pnl':>7} {'hist':>7} {'live':>7} {'sharpe':>6}")
    for r in out_rows[:30]:
        print(f"{r['config_id']:>5} {r['filter']:>15} {r['n']:>4} {r['wr']*100:>4.0f}  {r['pnl']:>7.1f} {r['hist']:>7.1f} {r['live']:>7.1f} {r['sharpe']:>6.2f}")

    # Top per-coin
    print("\nTop 10 per coin:")
    for c in COINS:
        rows = [r for r in out_rows if r["filter"] == f"coin:{c}"]
        rows.sort(key=lambda r: r["pnl"], reverse=True)
        print(f"\n  {c}:")
        print(f"  {'cid':>5} {'n':>4} {'WR%':>5} {'pnl':>7} {'hist':>7} {'live':>7} {'sharpe':>6}")
        for r in rows[:10]:
            print(f"  {r['config_id']:>5} {r['n']:>4} {r['wr']*100:>4.0f}  {r['pnl']:>7.1f} {r['hist']:>7.1f} {r['live']:>7.1f} {r['sharpe']:>6.2f}")

    # Top per hour bucket
    print("\nTop 5 per hour bucket:")
    for hb in ("ASIA", "EU", "US", "OVERNIGHT"):
        rows = [r for r in out_rows if r["filter"] == f"hour:{hb}"]
        rows.sort(key=lambda r: r["pnl"], reverse=True)
        print(f"\n  {hb}:")
        print(f"  {'cid':>5} {'n':>4} {'WR%':>5} {'pnl':>7} {'hist':>7} {'live':>7}")
        for r in rows[:5]:
            print(f"  {r['config_id']:>5} {r['n']:>4} {r['wr']*100:>4.0f}  {r['pnl']:>7.1f} {r['hist']:>7.1f} {r['live']:>7.1f}")


if __name__ == "__main__":
    main()
