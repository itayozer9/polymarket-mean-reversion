"""Evaluate candidate loss-avoidance filters: dev discovery -> both-halves dev
robustness -> ONE sealed-holdout confirmation, all with window-clustered CIs.

Filters are defined by the dev loss-mining (research.analysis.loss_patterns).
Each KEEPS the trades passing a causal condition; we report whether removing the
flagged losers raises EV without gutting the trade count, and whether the lift
PERSISTS out-of-sample (holdout = 05-28..29, opened once here).

Run: uv run python -m research.analysis.loss_filter_eval
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.lib.stats import window_clustered_bootstrap

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LED = os.path.join(REPO, "data", "research", "ledgers")


def _ci(df):
    if not len(df):
        return (0, 0, 0)
    return window_clustered_bootstrap(df["pnl"].to_numpy(), df["slug"].to_numpy(), n=4000)


def _line(tag, df):
    if not len(df):
        print(f"   {tag:<26} n=  0"); return
    lo, _, hi = _ci(df)
    print(f"   {tag:<26} n={len(df):>4}  WR={df['won'].mean()*100:4.1f}%  "
          f"EV=${df['pnl'].mean():+6.3f}  CI[{lo:+.3f},{hi:+.3f}]  tot=${df['pnl'].sum():+8.1f}")


# Candidate filters: name -> predicate KEEPING the trade (True = keep)
DET_FILTERS = {
    "D1 drop adverse_vel>2":   lambda d: d["adverse_vel_10s"] <= 2.0,
    "D2 tighten ask<=0.88":    lambda d: d["fav_ask"] <= 0.88,
    "D3 drop prev_fav_lost":   lambda d: d["prev_fav_lost"] != 1.0,
    "D4 D1+D2 combined":       lambda d: (d["adverse_vel_10s"] <= 2.0) & (d["fav_ask"] <= 0.88),
    "D5 D1+D2+D3 combined":    lambda d: (d["adverse_vel_10s"] <= 2.0) & (d["fav_ask"] <= 0.88) & (d["prev_fav_lost"] != 1.0),
}
SQ_FILTERS = {
    "S1 margin abs_mis>=0.12": lambda d: d["abs_mis"] >= 0.12,
    "S2 drop dist>19bps":      lambda d: d["dist_bps"] <= 19.0,
    "S3 drop n_coins_vol>=3":  lambda d: d["n_coins_volatile"] < 3,
    "S4 S1+S2 combined":       lambda d: (d["abs_mis"] >= 0.12) & (d["dist_bps"] <= 19.0),
    "S5 S1+S2+S3 combined":    lambda d: (d["abs_mis"] >= 0.12) & (d["dist_bps"] <= 19.0) & (d["n_coins_volatile"] < 3),
}


def both_halves(dev, pred):
    early = dev[dev["date"] <= "2026-05-24"]
    late = dev[dev["date"] >= "2026-05-25"]
    out = []
    for nm, d in (("early(23-24)", early), ("late(25-27)", late)):
        k = d[pred(d)]
        base_ev = d["pnl"].mean() if len(d) else 0
        ev = k["pnl"].mean() if len(k) else 0
        out.append(f"{nm}: base${base_ev:+.2f}->filt${ev:+.2f}(n{len(k)})")
    return "  ".join(out)


def evaluate(edge, filters):
    dev = pd.read_parquet(os.path.join(LED, f"{edge}_dev.parquet"))
    hold = pd.read_parquet(os.path.join(LED, f"{edge}_holdout.parquet"))
    print("\n" + "=" * 78)
    print(f"{edge.upper()}   dev n={len(dev)}  holdout n={len(hold)}")
    print("=" * 78)
    print(" DEV baseline & filters:")
    _line("baseline", dev)
    for nm, pred in filters.items():
        _line(nm, dev[pred(dev)])
    print("\n Both-halves dev robustness (does the lift hold in BOTH halves?):")
    for nm, pred in filters.items():
        print(f"   {nm:<26} {both_halves(dev, pred)}")
    print("\n HOLD-OUT (sealed 05-28..29 — opened once):")
    _line("baseline", hold)
    for nm, pred in filters.items():
        _line(nm, hold[pred(hold)])


if __name__ == "__main__":
    evaluate("det", DET_FILTERS)
    evaluate("sq", SQ_FILTERS)
