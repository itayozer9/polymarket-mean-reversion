"""det_lwd_v1 entry-price FLOOR sweep — does requiring a higher favourite ask
(raise min_ask 0.50 -> ~0.65?) lift WR / profit / robustness, or trade away the
edge?

The determinism edge = the book LAGS spot. The cheapest favourites (ask ~0.55)
are either (a) the PUREST pickoff (spot already moved, book hasn't caught up) ->
big payoff if the favourite still wins ~as often, or (b) genuine coinflips the
dist>=5bps filter failed to lock -> then a floor helps. This script decides which,
empirically: the calibration table (realized WR by entry-ask bucket) is the
answer; the floor sweep prices it in $/trade with real L2 fills.

Discipline: pick on DEV, confirm on HOLDOUT, check per-symbol. Same harness as
oracle_mechanics (fills_v2 walk_buy, hold-to-resolution, window-clustered CI).

Run: uv run python -m research.analysis.det_floor_sweep
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, settle_pnl, FeeSchedule
from research.dataset.feeds import load_l2_ladders
from research.lib.stats import window_clustered_bootstrap
from research.clean_window import (CLEAN_START, CLEAN_HOLDOUT_END,
                                   available_clean_dates)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
STAKE = 10.0
FEES = FeeSchedule()
_LV = range(1, 11)

# deployed det_lwd_v1 gates (everything EXCEPT the floor we are studying)
T_MIN, T_MAX, DIST_MIN, MAX_ASK = 1, 60, 5.0, 0.90
FLOORS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
CEILINGS = [0.90, 0.92, 0.95, 0.97]  # max_ask sweep, floor fixed at 0.50


def _load(splits) -> pd.DataFrame:
    df = pd.read_parquet(JOINED)
    df = df[df["split"].isin(splits) & df["book_healthy"]
            & df["outcome_up_clean"].notna() & df["cb_spot"].notna()
            & (df["start_price"] > 0)].copy()
    yes_fav = df["yes_mid"] >= 0.5
    df["fav_side"] = np.where(yes_fav, "yes", "no")
    df["fav_ask"] = np.where(yes_fav, df["yes_best_ask"], 1.0 - df["yes_best_bid"])
    df["fav_won"] = np.where(yes_fav, df["outcome_up_clean"] == 1,
                             df["outcome_up_clean"] == 0).astype("f8")
    df["abs_dist_bps"] = df["dist_strike_bps"].abs()
    df["spot_favors_yes"] = df["dist_strike_bps"] > 0
    df["consistent"] = (yes_fav & df["spot_favors_yes"]) | (~yes_fav & ~df["spot_favors_yes"])
    return df


def _entries(df, floor, max_ask=MAX_ASK):
    """One row per window: the FIRST last-60s tick meeting dist/consistent/
    ask in [floor,max_ask] — exactly where the live strategy would enter."""
    cand = df[(df["time_left_sec"] >= T_MIN) & (df["time_left_sec"] <= T_MAX)
              & (df["abs_dist_bps"] >= DIST_MIN) & df["consistent"]
              & (df["fav_ask"] >= floor) & (df["fav_ask"] <= max_ask)].copy()
    cand = cand.sort_values(["slug", "seconds_into_window"])
    return cand.groupby("slug", as_index=False).first()


def calibration_table(df, label):
    """CENTERPIECE: realized favourite WR by ENTRY-ASK bucket (one row/window,
    entered at the incumbent floor 0.50). If WR ~ ask -> efficient, no edge.
    If WR - ask is a ~constant positive margin -> floor only trims trade count.
    If WR - ask SHRINKS at low ask -> the user's intuition holds (floor helps)."""
    first = _entries(df, 0.50, max_ask=0.98)
    print(f"\n=== Calibration: favourite realized WR by entry-ask bucket — {label} "
          f"(n_windows={len(first)}) ===")
    print(f"{'ask_bucket':>13} {'n':>5} {'mean_ask':>9} {'WR':>6} {'WR-ask':>8} "
          f"{'net/sh':>8} {'WR 90%CI':>16}")
    edges = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98]
    for lo_e, hi_e in zip(edges[:-1], edges[1:]):
        sub = first[(first["fav_ask"] >= lo_e) & (first["fav_ask"] < hi_e)]
        tag = f"[{lo_e:.2f},{hi_e:.2f})"
        if len(sub) < 5:
            print(f"{tag:>13} {len(sub):>5}  (too few)"); continue
        ask = sub["fav_ask"].mean(); wr = sub["fav_won"].mean()
        net = wr - ask - FEES.taker_fee(1.0, ask)
        lo, _, hi = window_clustered_bootstrap(sub["fav_won"].to_numpy(),
                                               sub["slug"].to_numpy(), n=2000)
        print(f"{tag:>13} {len(sub):>5} {ask:>9.3f} {wr:>6.3f} {wr-ask:>+8.3f} "
              f"{net:>+8.3f} [{lo:.2f},{hi:.2f}]")


def _ladders(symbols):
    dates = available_clean_dates("btc")
    d1 = dates[-1] if dates else CLEAN_START
    return {s: load_l2_ladders(s, CLEAN_START, d1) for s in symbols}


def backtest_floor(df, ladders, floor, max_ask=MAX_ASK):
    first = _entries(df, floor, max_ask)
    pnls, groups, wins, asks = [], [], [], []
    for _, r in first.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        sec = int(r["seconds_into_window"])
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
        won = bool(r["fav_won"])
        pnls.append(settle_pnl(fill, won)); groups.append(r["slug"])
        wins.append(won); asks.append(fill.avg_price)
    return (np.array(pnls), np.array(groups),
            np.array(wins, dtype=float), np.array(asks))


def floor_sweep(df, ladders, label, n_days):
    print(f"\n=== Floor sweep — {label} (t_max={T_MAX}, dist>={DIST_MIN}, "
          f"max_ask={MAX_ASK}, ~{n_days} days) ===")
    print(f"{'min_ask':>8} {'trades':>7} {'WR':>6} {'avg_ask':>8} {'$/trade':>9} "
          f"{'90%CI':>20} {'total$':>9} {'$/day':>8}")
    out = {}
    for floor in FLOORS:
        pnl, grp, wins, asks = backtest_floor(df, ladders, floor)
        if len(pnl) < 8:
            print(f"{floor:>8.2f} {len(pnl):>7}  (too few)"); out[floor] = None; continue
        lo, med, hi = window_clustered_bootstrap(pnl, grp, n=2000)
        mu = pnl.mean()
        flag = "  <==CI+" if lo > 0 else ""
        print(f"{floor:>8.2f} {len(pnl):>7} {wins.mean():>6.3f} {asks.mean():>8.3f} "
              f"${mu:>+8.3f} [{lo:>+6.3f},{hi:>+6.3f}] ${pnl.sum():>+8.2f} "
              f"${mu*len(pnl)/n_days:>+7.2f}{flag}")
        out[floor] = (len(pnl), float(wins.mean()), mu, lo, hi, float(pnl.sum()))
    return out


def ceiling_sweep(df, ladders, label, n_days):
    """Fix floor=0.50, sweep max_ask. Does raising the cap 0.90 -> 0.95 add
    +EV trades, or does the payoff asymmetry (pay 0.93 to win 0.07, lose 0.93)
    make the dear favourites net-negative?"""
    print(f"\n=== Ceiling sweep — {label} (floor=0.50, t_max={T_MAX}, "
          f"dist>={DIST_MIN}, ~{n_days} days) ===")
    print(f"{'max_ask':>8} {'trades':>7} {'WR':>6} {'avg_ask':>8} {'$/trade':>9} "
          f"{'90%CI':>20} {'total$':>9}")
    for cap in CEILINGS:
        pnl, grp, wins, asks = backtest_floor(df, ladders, 0.50, max_ask=cap)
        if len(pnl) < 8:
            print(f"{cap:>8.2f} {len(pnl):>7}  (too few)"); continue
        lo, med, hi = window_clustered_bootstrap(pnl, grp, n=2000)
        mu = pnl.mean()
        flag = "  <==CI+" if lo > 0 else ""
        print(f"{cap:>8.2f} {len(pnl):>7} {wins.mean():>6.3f} {asks.mean():>8.3f} "
              f"${mu:>+8.3f} [{lo:>+6.3f},{hi:>+6.3f}] ${pnl.sum():>+8.2f}{flag}")


def ceiling_marginal(df, ladders, label):
    """The decisive cut: trades in the (0.90, 0.95] band ALONE — would the
    *added* trades from raising the cap be profitable on their own?"""
    pnl, grp, wins, asks = backtest_floor(df, ladders, 0.90, max_ask=0.95)
    if len(pnl) < 5:
        print(f"\n[{label}] marginal (0.90,0.95] band: n={len(pnl)} (too few to judge)")
        return
    lo, med, hi = window_clustered_bootstrap(pnl, grp, n=2000)
    print(f"\n[{label}] marginal favourites in (0.90,0.95] ONLY: n={len(pnl)} "
          f"WR={wins.mean():.3f} avg_ask={asks.mean():.3f} ${pnl.mean():+.3f}/tr "
          f"90%CI[{lo:+.3f},{hi:+.3f}] total=${pnl.sum():+.2f}")


def per_symbol(df, ladders, floors):
    print(f"\n=== Per-symbol stability (dev+holdout pooled) ===")
    for floor in floors:
        print(f"  floor {floor:.2f}:")
        for sym in sorted(df["symbol"].unique()):
            d = df[df["symbol"] == sym]
            pnl, grp, wins, asks = backtest_floor(d, ladders, floor)
            if len(pnl) < 3:
                print(f"    {sym:>4}: n={len(pnl)} (too few)"); continue
            print(f"    {sym:>4}: n={len(pnl):>3} WR={wins.mean():.2f} "
                  f"${pnl.mean():+.3f}/tr total=${pnl.sum():+.2f}")


def run():
    dev = _load(["dev"]); hold = _load(["holdout"]); alld = _load(["dev", "holdout"])
    print(f"det floor sweep. dev windows~{dev['slug'].nunique()} "
          f"holdout windows~{hold['slug'].nunique()} ({CLEAN_START}..{CLEAN_HOLDOUT_END})")
    calibration_table(alld, "DEV+HOLDOUT pooled (descriptive)")
    calibration_table(dev, "DEV only")
    ladders = _ladders(sorted(alld["symbol"].unique()))
    floor_sweep(dev, ladders, "DEV (decision set)", n_days=5)
    floor_sweep(hold, ladders, "HOLDOUT (confirm)", n_days=2)
    ceiling_sweep(dev, ladders, "DEV (decision set)", n_days=5)
    ceiling_sweep(hold, ladders, "HOLDOUT (confirm)", n_days=2)
    ceiling_marginal(dev, ladders, "DEV")
    ceiling_marginal(hold, ladders, "HOLDOUT")
    per_symbol(alld, ladders, [0.50, 0.65])


if __name__ == "__main__":
    run()
