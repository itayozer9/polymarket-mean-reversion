"""Phase 3 — maker spread-capture, measured with the REAL trade tape.

The prior market-making study (`market_making_feasibility.md`) was a NO-GO built
on a PESSIMISTIC adverse-selection PROXY (-2.25c at +30s) inferred from book
moves, with no trade tape and no liquidity rewards. It explicitly flagged
NEEDS-BETTER-DATA. We now have the executed-trade tape — so we measure the real
thing: when actual taker SELL flow hits the bid (a maker BUY would fill), where
does the mid go next? That forward drift IS the adverse selection. Symmetric for
SELL fills. Then net it against spread capture + maker rebate (+ a liquidity-
rewards range) to render a data-driven go/no-go.

Run: uv run python -m research.analysis.maker_real_fill
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import FeeSchedule

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
FEES = FeeSchedule()
FLOW_MIN_USD = 20.0   # a "real" taker hit: >= $20 traded that second on that side


def _fwd_mid(df, horizons=(15, 30, 60)):
    g = df.groupby("slug", sort=False)
    for h in horizons:
        df[f"mid_fwd_{h}"] = g["yes_mid"].shift(-h)
    return df


def run():
    df = pd.read_parquet(JOINED)
    df = df[df["book_healthy"] & df["yes_mid"].between(0.05, 0.95)].copy()
    df = df.sort_values(["slug", "seconds_into_window"])
    df = _fwd_mid(df)
    half_spread = df["spread_yes"].to_numpy("f8") / 2.0
    df["half_spread"] = half_spread

    print("Phase 3 — maker economics from the REAL trade tape (dev+holdout, 15m)")
    print(f"flow threshold = ${FLOW_MIN_USD}/sec on the hit side\n")

    for scope, d in [("ALL", df), ("BTC", df[df["symbol"] == "btc"]),
                     ("ETH", df[df["symbol"] == "eth"])]:
        print(f"=== {scope} ===")
        # Maker BUY fills: a real SELL hit the bid this second
        buy_fill = d[d["tr_bear_usd"] >= FLOW_MIN_USD]
        # Maker SELL fills: a real BUY lifted the ask this second
        sell_fill = d[d["tr_bull_usd"] >= FLOW_MIN_USD]
        for h in (30,):
            # adverse drift (signed mid move after the fill)
            bdrift = (buy_fill[f"mid_fwd_{h}"] - buy_fill["yes_mid"]).dropna()
            sdrift = (sell_fill[f"mid_fwd_{h}"] - sell_fill["yes_mid"]).dropna()
            hs_b = buy_fill["half_spread"].reindex(bdrift.index).mean()
            hs_s = sell_fill["half_spread"].reindex(sdrift.index).mean()
            # per-share markout in CENTS: you BUY at bid (mid - hs); value vs mid_fwd
            #   buy markout = (mid_fwd - bid) = drift + half_spread
            #   sell markout = (ask - mid_fwd) = half_spread - drift
            buy_markout = (bdrift.mean() + hs_b) * 100
            sell_markout = (hs_s - sdrift.mean()) * 100
            rebate_c = FEES.maker_rebate(1.0, 0.5) * 100  # ~ at p=0.5, cents/share
            print(f"  +{h}s, n_buyfill={len(bdrift):,} n_sellfill={len(sdrift):,}")
            print(f"    buy-fill mid drift  = {bdrift.mean()*100:+.2f}c  half-spread {hs_b*100:.2f}c "
                  f"-> buy markout {buy_markout:+.2f}c")
            print(f"    sell-fill mid drift = {sdrift.mean()*100:+.2f}c  half-spread {hs_s*100:.2f}c "
                  f"-> sell markout {sell_markout:+.2f}c")
            roundtrip = buy_markout + sell_markout + 2 * rebate_c
            print(f"    rebate ~{rebate_c:.2f}c/leg ; ROUND-TRIP (both legs+rebate) = "
                  f"{roundtrip:+.2f}c  (+ liquidity rewards, not counted)")
            # to net positive at $10 stake (~20-40 shares) you need roundtrip > 0
            print(f"    => maker round-trip is {'POSITIVE' if roundtrip>0 else 'NEGATIVE'} "
                  f"before inventory/resolution risk\n")


if __name__ == "__main__":
    run()
