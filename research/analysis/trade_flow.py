"""Phase 4 — trade-flow / panic mean-reversion (the user's original thesis).

H2 (market_hypotheses.md): an odds drop alone doesn't predict reversion; whether
SPOT moved with it does. Odds fell + spot FLAT => noise => should revert (buy the
dip). Odds fell + spot genuinely moved => signal => continues to 0 (don't buy).
This is the user's manual "buy the dip near the strike" rule with the spot-flat /
proximity filter that the proximity unit-bug (phase0_audit Task 5) never let any
prior backtest test. Phases 1-2 found book-LAGS-spot momentum on spot-driven
moves; this asks the opposite for NON-spot-driven drops.

Rule: a side's odds dropped >= D% over 30 s, the drop was NOT spot-justified
(|spot_move_30s| <= flat_max), price in a dip band -> buy that side (taker), hold
to resolution. One trade/window. Diagnostic edge map first, then backtest.

Run: uv run python -m research.analysis.trade_flow
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, settle_pnl
from research.dataset.feeds import load_l2_ladders
from research.lib.stats import window_clustered_bootstrap
from research.clean_window import CLEAN_START, available_clean_dates

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
STAKE = 10.0
_LV = range(1, 11)


def _prep(df):
    df = df[df["book_healthy"] & df["outcome_up_clean"].notna()
            & df["cb_spot"].notna() & (df["start_price"] > 0)].copy()
    # The side that just dropped (pick the larger of yes/no 30s drop).
    yes_drop = df["yes_drop_30s"].to_numpy("f8")
    no_drop = df["no_drop_30s"].to_numpy("f8")
    df["drop_side"] = np.where(yes_drop >= no_drop, "yes", "no")
    df["drop_mag"] = np.maximum(yes_drop, no_drop)
    df["dip_ask"] = np.where(df["drop_side"] == "yes",
                             df["yes_best_ask"], 1.0 - df["yes_best_bid"])
    df["dip_won"] = np.where(df["drop_side"] == "yes",
                             df["outcome_up_clean"] == 1, df["outcome_up_clean"] == 0).astype("f8")
    df["abs_spot_move_30s"] = df["spot_move_30s"].abs() * 100  # %->bps-ish
    return df


def edge_map(df):
    print("\n=== Dipped-side realized WR vs ask, by drop magnitude × spot-move (noise vs signal) ===")
    print(f"{'drop%':>8} {'spotmove':>10} {'n_win':>6} {'dip_ask':>8} {'WR':>6} {'gross':>7} {'WR_ci':>14}")
    for dlo, dhi in [(10, 25), (25, 50), (50, 101)]:
        for slo, shi, tag in [(0, 3, "FLAT"), (3, 10, "mid"), (10, 1e9, "MOVED")]:
            m = (df["drop_mag"] > dlo) & (df["drop_mag"] <= dhi) \
                & (df["abs_spot_move_30s"] > slo) & (df["abs_spot_move_30s"] <= shi) \
                & df["dip_ask"].between(0.05, 0.6) & (df["time_left_sec"] > 60)
            sub = df[m]
            if len(sub) < 40:
                continue
            ask = sub["dip_ask"].mean(); wr = sub["dip_won"].mean()
            lo, _, hi = window_clustered_bootstrap(sub["dip_won"].to_numpy(),
                                                   sub["slug"].to_numpy(), n=1500)
            star = "  <==" if lo > ask else ""
            print(f"{dhi:>7.0f}% {tag:>10} {sub['slug'].nunique():>6} {ask:>8.3f} "
                  f"{wr:>6.3f} {wr-ask:>+7.3f} [{lo:.2f},{hi:.2f}]{star}")


def _ladders(symbols):
    dates = available_clean_dates("btc")
    return {s: load_l2_ladders(s, CLEAN_START, dates[-1] if dates else CLEAN_START) for s in symbols}


def backtest(df, ladders, drop_min, flat_max, ask_lo, ask_hi, latency=2):
    cand = df[(df["drop_mag"] >= drop_min) & (df["abs_spot_move_30s"] <= flat_max)
              & df["dip_ask"].between(ask_lo, ask_hi) & (df["time_left_sec"] > 60)]
    first = cand.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False).first()
    pnl, grp, wins = [], [], []
    for _, r in first.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        try:
            lr = lad.loc[(r["slug"], int(r["seconds_into_window"]) + latency)]
        except KeyError:
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        if r["drop_side"] == "yes":
            px = [lr[f"ask_px_{i}"] for i in _LV]; sz = [lr[f"ask_sz_{i}"] for i in _LV]
        else:
            px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]; sz = [lr[f"bid_sz_{i}"] for i in _LV]
        f = walk_buy(px, sz, STAKE)
        if not f.filled or f.unfilled_usd > STAKE * 0.5:
            continue
        pnl.append(settle_pnl(f, bool(r["dip_won"]))); grp.append(r["slug"]); wins.append(bool(r["dip_won"]))
    return np.array(pnl), np.array(grp), np.array(wins)


def run():
    df = _prep(pd.read_parquet(JOINED))
    dev = df[df["split"] == "dev"]; hold = df[df["split"] == "holdout"]
    print(f"Phase 4 — noise-drop reversion. dev windows={dev['slug'].nunique()}")
    edge_map(dev)
    ladders = _ladders(sorted(df["symbol"].unique()))
    for label, d in (("dev", dev), ("HOLD-OUT", hold)):
        print(f"\n=== backtest {label} (buy noise-drop dip, hold to resolution) ===")
        print(f"{'drop_min':>8} {'flat_max':>8} {'ask_band':>10} {'trades':>7} {'WR':>6} {'$/trade':>9} {'90% CI':>18}")
        for drop_min in (15, 25):
            for flat_max in (3, 5):
                for band in [(0.10, 0.35), (0.10, 0.50)]:
                    pnl, grp, wins = backtest(d, ladders, drop_min, flat_max, band[0], band[1])
                    if len(pnl) < 15:
                        continue
                    lo, _, hi = window_clustered_bootstrap(pnl, grp, n=1500)
                    flag = "  <==" if lo > 0 else ""
                    print(f"{drop_min:>8} {flat_max:>8} {str(band):>10} {len(pnl):>7} {wins.mean():>6.3f} "
                          f"${pnl.mean():>+8.3f} [{lo:>+6.3f},{hi:>+6.3f}]{flag}")


if __name__ == "__main__":
    run()
