"""Phase 1 — late-window determinism pickoff.

Settlement is the near-real-time Chainlink Data Stream (phase0a). So the edge is
NOT oracle staleness; it is: in the final seconds, when spot (Coinbase WS, a
fast public proxy for the stream) is decisively far from the strike, the outcome
is near-locked. If the market still prices the favourite below its realized
win rate, a taker who buys the favourite and HOLDS TO RESOLUTION captures a
rare, >cost edge.

Honesty guards:
  - condition only on data observable AT decision time (Coinbase spot distance,
    time-left) -> any edge found is capturable in real time;
  - measure realized WR against the TRUE outcome -> cases where Coinbase misleads
    (vs the stream) are already netted into the WR;
  - book_healthy filter (no decided-market artifact);
  - ONE trade per window (no stale-tick over-weighting);
  - fills walk the real L2 book (fills_v2); hold-to-resolution = one-way cost;
  - both-halves dev CV; hold-out stays sealed (Phase 5).

Run: uv run python -m research.analysis.oracle_mechanics
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, settle_pnl, FeeSchedule
from research.dataset.feeds import load_l2_ladders
from research.lib.stats import window_clustered_bootstrap
from research.clean_window import CLEAN_START, CLEAN_DEV_END, available_clean_dates

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
STAKE = 10.0
FEES = FeeSchedule()
_LV = range(1, 11)


def _load_dev() -> pd.DataFrame:
    df = pd.read_parquet(JOINED)
    df = df[(df["split"] == "dev") & df["book_healthy"]
            & df["outcome_up_clean"].notna() & df["cb_spot"].notna()
            & (df["start_price"] > 0)].copy()
    # favourite side + the taker ask to buy it + did it win
    yes_fav = df["yes_mid"] >= 0.5
    df["fav_side"] = np.where(yes_fav, "yes", "no")
    df["fav_ask"] = np.where(yes_fav, df["yes_best_ask"], 1.0 - df["yes_best_bid"])
    df["fav_won"] = np.where(yes_fav, df["outcome_up_clean"] == 1,
                             df["outcome_up_clean"] == 0).astype("f8")
    df["abs_dist_bps"] = df["dist_strike_bps"].abs()
    # the favourite must agree with the side spot currently favours (else the
    # "determinism" is contradicted by spot — skip those, they aren't a pickoff)
    df["spot_favors_yes"] = df["dist_strike_bps"] > 0
    df["consistent"] = (yes_fav & df["spot_favors_yes"]) | (~yes_fav & ~df["spot_favors_yes"])
    return df


def edge_map(df: pd.DataFrame) -> None:
    """Diagnostic: conditional favourite WR vs market ask, by (time_left, dist)."""
    print("\n=== Edge map: favourite realized WR vs market ask (dev, one row per tick) ===")
    print(f"{'t_left':>10} {'dist_bps':>12} {'n_win':>6} {'fav_ask':>8} "
          f"{'WR':>6} {'gross':>7} {'net/sh':>7} {'WR_ci':>14}")
    tl_buckets = [(0, 15), (15, 30), (30, 60), (60, 120), (120, 240)]
    dist_buckets = [(0, 5), (5, 15), (15, 40), (40, 1e9)]
    for tl in tl_buckets:
        for db in dist_buckets:
            m = (df["time_left_sec"] > tl[0]) & (df["time_left_sec"] <= tl[1]) \
                & (df["abs_dist_bps"] > db[0]) & (df["abs_dist_bps"] <= db[1]) \
                & df["consistent"]
            sub = df[m]
            if len(sub) < 50:
                continue
            ask = sub["fav_ask"].mean()
            wr = sub["fav_won"].mean()
            fee_sh = FEES.taker_fee(1.0, ask)
            net = wr - ask - fee_sh
            lo, _, hi = window_clustered_bootstrap(
                sub["fav_won"].to_numpy(), sub["slug"].to_numpy(), n=2000)
            star = "  <==" if (lo - ask - fee_sh) > 0 else ""
            print(f"{tl[1]:>10} {db[1] if db[1]<1e9 else '40+':>12} {sub['slug'].nunique():>6} "
                  f"{ask:>8.3f} {wr:>6.3f} {wr-ask:>+7.3f} {net:>+7.3f} "
                  f"[{lo:.2f},{hi:.2f}]{star}")


def _ladders(symbols):
    dates = available_clean_dates("btc")
    d1 = dates[-1] if dates else CLEAN_START
    return {s: load_l2_ladders(s, CLEAN_START, d1) for s in symbols}


def selective_backtest(df, ladders, t_min, t_max, dist_min, max_ask, latency_s=0):
    """ONE trade per window: the first tick with time_left in [t_min,t_max],
    abs_dist>=dist_min, consistent, fav_ask<=max_ask. Buy favourite (walk L2),
    hold to resolution. Returns per-trade pnl + slug groups + diagnostics."""
    cand = df[(df["time_left_sec"] >= t_min) & (df["time_left_sec"] <= t_max)
              & (df["abs_dist_bps"] >= dist_min) & df["consistent"]
              & (df["fav_ask"] <= max_ask) & (df["fav_ask"] >= 0.50)].copy()
    cand = cand.sort_values(["slug", "seconds_into_window"])
    first = cand.groupby("slug", as_index=False).first()
    pnls, groups, wins = [], [], []
    for _, r in first.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        sec = int(r["seconds_into_window"]) + latency_s
        try:
            lr = lad.loc[(r["slug"], sec)]
        except KeyError:
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        if r["fav_side"] == "yes":
            px = [lr[f"ask_px_{i}"] for i in _LV]; sz = [lr[f"ask_sz_{i}"] for i in _LV]
        else:
            px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]; sz = [lr[f"bid_sz_{i}"] for i in _LV]
        fill = walk_buy(px, sz, STAKE)
        if not fill.filled or fill.unfilled_usd > STAKE * 0.5:
            continue
        # recompute won at the (possibly latency-shifted) entry using window outcome
        won = bool(r["fav_won"])
        pnls.append(settle_pnl(fill, won)); groups.append(r["slug"]); wins.append(won)
    return np.array(pnls), np.array(groups), np.array(wins)


def sweep(df, ladders):
    print("\n=== Selective backtest sweep (dev, one trade/window, hold-to-resolution) ===")
    print(f"{'t_max':>6} {'dist_min':>9} {'max_ask':>8} {'trades':>7} {'WR':>6} "
          f"{'$/trade':>9} {'90%CI':>18} {'$/day':>9}")
    n_days = 5  # dev = 05-23..05-27
    rows = []
    for t_max in (15, 30, 60):
        for dist_min in (5, 10, 20, 40):
            for max_ask in (0.90, 0.95, 0.97):
                pnl, grp, wins = selective_backtest(df, ladders, 1, t_max, dist_min, max_ask)
                if len(pnl) < 20:
                    continue
                lo, med, hi = window_clustered_bootstrap(pnl, grp, n=2000)
                mu = pnl.mean()
                rows.append((t_max, dist_min, max_ask, len(pnl), wins.mean(), mu, lo, hi, mu*len(pnl)/n_days))
                flag = "  <==" if lo > 0 else ""
                print(f"{t_max:>6} {dist_min:>9} {max_ask:>8.2f} {len(pnl):>7} {wins.mean():>6.3f} "
                      f"${mu:>+8.3f} [{lo:>+6.3f},{hi:>+6.3f}] ${mu*len(pnl)/n_days:>+8.2f}{flag}")
    return rows


def both_halves(df, ladders, t_max, dist_min, max_ask):
    """Require the rule to be CI-positive in BOTH dev halves (anti-overfit)."""
    early = df[df["date"] <= "2026-05-24"]
    late = df[df["date"] >= "2026-05-25"]
    out = {}
    for name, d in (("early(23-24)", early), ("late(25-27)", late)):
        pnl, grp, wins = selective_backtest(d, ladders, 1, t_max, dist_min, max_ask)
        if len(pnl) < 10:
            out[name] = (len(pnl), None, None, None); continue
        lo, med, hi = window_clustered_bootstrap(pnl, grp, n=2000)
        out[name] = (len(pnl), pnl.mean(), lo, hi)
    return out


def run():
    df = _load_dev()
    print(f"Phase 1 — late-window determinism. dev rows={len(df):,} "
          f"windows={df['slug'].nunique()} ({CLEAN_START}..{CLEAN_DEV_END})")
    edge_map(df)
    ladders = _ladders(sorted(df["symbol"].unique()))
    rows = sweep(df, ladders)
    # report both-halves for the best CI-positive configs
    pos = [r for r in rows if r[6] > 0]
    pos.sort(key=lambda r: r[6], reverse=True)
    if pos:
        print("\n=== Both-halves dev CV for CI-positive configs ===")
        for r in pos[:6]:
            t_max, dist_min, max_ask = r[0], r[1], r[2]
            bh = both_halves(df, ladders, t_max, dist_min, max_ask)
            print(f"  t_max={t_max} dist_min={dist_min} max_ask={max_ask}: {bh}")
    else:
        print("\nNo CI-positive selective config on dev.")


if __name__ == "__main__":
    run()
