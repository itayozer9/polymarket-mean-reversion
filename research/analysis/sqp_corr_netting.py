"""Answer the user's question: 'sqp can have massive losses — can we prevent it?'

sqp's −$100–230 hours are 4 coins moving together on ONE macro spot move (it looks
like 4 bets but is one leveraged macro bet — see memory [[sq-variance-macro-correlated]]).
Correlation-netting caps exposure per (window, direction) ACROSS coins. We measure the
loss-tail reduction on the Chainlink-settled sq ledger vs EV cost.

Run: uv run python -m research.analysis.sqp_corr_netting
"""
from __future__ import annotations
import os

import numpy as np
import pandas as pd

from research.analysis.resettle_chainlink import chainlink_outcome_by_slug, _resettle, LED
from research.lib.rigor import (daily_pnl_from_ledger, max_drawdown, longest_losing_streak,
                                block_bootstrap_worstpath)


def _stats(led, label):
    dp = daily_pnl_from_ledger(led)
    mdd = max_drawdown(np.cumsum(dp.values))
    return (f"{label:14} n={len(led):>4}  total ${led['pnl'].sum():>+7.0f}  EV ${led['pnl'].mean():>+5.2f}/tr  "
            f"worst-day ${dp.min():>+7.1f}  maxDD ${mdd:>+7.1f}  longest-loss-streak {longest_losing_streak(dp.values)}d")


def run():
    sq = pd.read_parquet(os.path.join(LED, "sq_full.parquet"))
    cl = chainlink_outcome_by_slug()
    d = _resettle(sq, cl, "symbol_buy").copy()
    d["pnl"] = d["pnl_cl"]                                   # Chainlink-settled PnL
    d["date"] = pd.to_datetime(d["window_start_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    d["dir"] = d["symbol_buy"].astype(str)
    d["cluster"] = d.groupby(["window_start_ts", "dir"])["slug"].transform("count")

    print("=== sqp MACRO-CORRELATION (Chainlink-settled sq ledger) ===")
    print(f"  trades={len(d)}  in same-(window,direction) cluster >=2 coins: {(d['cluster']>=2).mean()*100:.0f}%"
          f"  | >=3 coins: {(d['cluster']>=3).mean()*100:.0f}%  | all-4: {(d['cluster']>=4).mean()*100:.0f}%")
    # how much of the worst single day's loss is correlated clusters?
    dp = daily_pnl_from_ledger(d)
    wd = dp.idxmin()
    sub = d[d["date"] == wd]
    print(f"  worst day {wd}: ${dp.min():+.0f} over {len(sub)} trades; "
          f"{(sub['cluster']>=3).mean()*100:.0f}% were 3-4 coin clusters")

    print("\n=== correlation-netting: keep only first K coins per (window, direction) ===")
    print(_stats(d, "uncapped"))
    for K in (1, 2):
        d2 = d.sort_values(["window_start_ts", "dir", "symbol"]).copy()
        d2["rk"] = d2.groupby(["window_start_ts", "dir"]).cumcount()
        print(_stats(d2[d2["rk"] < K], f"net K={K}"))

    # worst-case drawdown distribution (block bootstrap of daily PnL): uncapped vs K=1
    print("\n=== worst-case drawdown (block-bootstrap of daily PnL, 5000x) ===")
    d1 = d.sort_values(["window_start_ts", "dir", "symbol"]).copy()
    d1["rk"] = d1.groupby(["window_start_ts", "dir"]).cumcount()
    for led, label in ((d, "uncapped"), (d1[d1["rk"] < 1], "net K=1")):
        bb = block_bootstrap_worstpath(daily_pnl_from_ledger(led).values, n=5000, block=2)
        if bb:
            print(f"  {label:9}: maxDD p5/p50 ${bb['max_drawdown']['p5']:+.0f}/{bb['max_drawdown']['p50']:+.0f}  "
                  f"total p5/p50 ${bb['total']['p5']:+.0f}/{bb['total']['p50']:+.0f}  "
                  f"longest-loss-streak p95 {bb['longest_losing_streak']['p95']:.0f}d")


if __name__ == "__main__":
    run()
