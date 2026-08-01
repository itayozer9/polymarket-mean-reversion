"""martingale_sweep — test the user's intra-window reversion + martingale idea.

Thesis under test: "the binary price is volatile; if it dips, buy it; if it dips
further, double/triple (martingale); take a small TP (10-15%) on the bounce; if it
never bounces, the 15m window settles and backstops you."

This is a DIFFERENT machine from the hold-to-resolution sweep: it simulates the
intra-window price path with TP exits (sell into the bid), position adds on deeper
dips, and forced settlement at expiry (Chainlink). It captures the two things that
kill intra-window scalps and martingales:
  - the bid/ask SPREAD paid on every round-trip, and
  - the fact that a binary settles 0/1 at a HARD expiry -> a doubled losing leg
    loses 100%, and "wait for it to revert" is capped by the clock.

We report not just mean EV but the TAIL (worst single window, max drawdown, ruin),
because a martingale's average hides its risk.

Settlement: Chainlink (resettle_chainlink), the oracle Polymarket pays.
Fills (first pass): top-of-book ask to buy / bid to sell + taker fee; survivors,
if any, get the full L2 ladder-walk. NO LOOKAHEAD: decisions use only the path so
far. Entry needs time-left buffer so the "wait for reversion" is real, not a race.

Run: uv run python -m research.analysis.martingale_sweep
Out: data/research/hypotheses/martingale.jsonl
"""
from __future__ import annotations
import itertools
import json
import os

import numpy as np
import pandas as pd

from research.analysis.edge_lab import load_base, cl_outcomes
from research.sim.fills_v2 import FeeSchedule

OUT = os.path.join("data", "research", "hypotheses", "martingale.jsonl")
BASE_STAKE = 10.0
FEES = FeeSchedule()
DAILY_CAP = 50.0
MIN_BUF = 120          # seconds-left required to OPEN (need room to revert)
MIN_BUF_ADD = 60       # seconds-left required to ADD


def _windows():
    """precompute per-window price arrays once: (sec, time_left, yes_ask, yes_bid)."""
    b = load_base()
    b = b[b["book_healthy"]].sort_values(["slug", "seconds_into_window"])
    cl = cl_outcomes().set_index("slug")["cl_up"].to_dict()
    wins = {}
    meta = {}
    for slug, g in b.groupby("slug", sort=False):
        if slug not in cl:
            continue
        wins[slug] = (g["seconds_into_window"].to_numpy("f8"),
                      g["time_left_sec"].to_numpy("f8"),
                      g["yes_best_ask"].to_numpy("f8"),
                      g["yes_best_bid"].to_numpy("f8"))
        r0 = g.iloc[0]
        meta[slug] = (r0["symbol"], str(r0["date"]), r0["split"],
                      float(r0["window_start_ts"]), int(cl[slug]))
    return wins, meta


def _side_prices(yes_ask, yes_bid, side):
    """ask (to buy) and bid (to sell) for the chosen side, per tick."""
    if side == "yes":
        return yes_ask, yes_bid
    # NO side: buy NO at 1-yes_bid ask, sell NO at 1-yes_ask bid
    return 1.0 - yes_bid, 1.0 - yes_ask


def _sim_window(arr, cl_up, side, p):
    """one window, one side. returns (pnl, entered, tp_hit) or None if no entry.
    p: mode, trig, fixed_t, tp, mult, max_adds, add_step."""
    sec, tleft, ya, yb = arr
    ask, bid = _side_prices(ya, yb, side)
    n = len(sec)
    won_side = (cl_up == 1) if side == "yes" else (cl_up == 0)

    # find entry index
    ei = -1
    if p["mode"] == "adverse":
        peak = -1.0
        for i in range(n):
            if not np.isfinite(ask[i]):
                continue
            peak = max(peak, ask[i])
            if peak > 0 and ask[i] <= peak * (1 - p["trig"]) and tleft[i] >= MIN_BUF \
                    and 0.02 < ask[i] < 0.98:
                ei = i
                break
    else:  # fixed: first tick at/after fixed_t where this side is below its open
        open_px = next((ask[j] for j in range(n) if np.isfinite(ask[j])), np.nan)
        for i in range(n):
            if sec[i] >= p["fixed_t"] and tleft[i] >= MIN_BUF and np.isfinite(ask[i]) \
                    and 0.02 < ask[i] < 0.98 and ask[i] <= open_px:
                ei = i
                break
    if ei < 0:
        return None

    # open position
    shares = 0.0
    cash = 0.0
    stake = BASE_STAKE
    last_buy = ask[ei]
    s0 = stake / ask[ei]
    shares += s0
    cash -= s0 * ask[ei] + FEES.taker_fee(s0, ask[ei])
    avg = ask[ei]
    adds = 0

    for i in range(ei + 1, n):
        if not (np.isfinite(ask[i]) and np.isfinite(bid[i])):
            continue
        # TP check (sell into the bid)
        if bid[i] >= avg * (1 + p["tp"]):
            cash += shares * bid[i] - FEES.taker_fee(shares, bid[i])
            return cash, 1, 1
        # ADD check (deeper dip)
        if adds < p["max_adds"] and ask[i] <= last_buy * (1 - p["add_step"]) \
                and tleft[i] >= MIN_BUF_ADD and 0.02 < ask[i] < 0.98:
            stake *= p["mult"]
            si = stake / ask[i]
            shares += si
            cash -= si * ask[i] + FEES.taker_fee(si, ask[i])
            avg = (-cash if False else (avg * (shares - si) + ask[i] * si) / shares)
            last_buy = ask[i]
            adds += 1
    # expiry settlement (held to the clock)
    if won_side:
        cash += shares  # winners redeem at $1, no fee
    return cash, 1, 0


def run_config(wins, meta, p):
    trades = []
    for slug, arr in wins.items():
        sym, date, split, wst, cl_up = meta[slug]
        for side in ("yes", "no"):
            r = _sim_window(arr, cl_up, side, p)
            if r is None:
                continue
            pnl, entered, tp = r
            trades.append((wst, date, split, sym, slug, side, pnl, tp))
    if len(trades) < 20:
        return {**p, "n": len(trades)}
    df = pd.DataFrame(trades, columns=["wst", "date", "split", "sym", "slug",
                                       "side", "pnl", "tp"]).sort_values("wst")
    # apply $50/day soft cap sequentially (skip new entries once day realized<=-50)
    capped = []
    day = None
    day_pnl = 0.0
    for _, t in df.iterrows():
        if t["date"] != day:
            day, day_pnl = t["date"], 0.0
        if day_pnl <= -DAILY_CAP:
            continue
        capped.append(t)
        day_pnl += t["pnl"]
    cdf = pd.DataFrame(capped) if capped else df.iloc[:0]

    def stats(d):
        if len(d) == 0:
            return None
        cum = d["pnl"].cumsum().to_numpy()
        dd = float((np.maximum.accumulate(cum) - cum).max()) if len(cum) else 0.0
        return dict(n=int(len(d)), ev=round(float(d["pnl"].mean()), 3),
                    total=round(float(d["pnl"].sum()), 1),
                    tp_rate=round(float(d["tp"].mean() * 100), 1),
                    worst=round(float(d["pnl"].min()), 1),
                    max_dd=round(dd, 1))
    out = {**p, "n": int(len(df))}
    out["uncapped"] = stats(df)
    out["capped"] = stats(cdf)
    for sp in ("dev", "holdout", "future"):
        out[f"cap_{sp}"] = stats(cdf[cdf["split"] == sp])
    return out


def gen_grid():
    g = []
    common = dict(tp=[0.10, 0.15, 0.20], mult=[2, 3], max_adds=[2, 3],
                  add_step=[0.05, 0.10])
    for trig, tp, mult, ma, step in itertools.product(
            [0.05, 0.10, 0.15], *[common[k] for k in ("tp", "mult", "max_adds", "add_step")]):
        g.append(dict(mode="adverse", trig=trig, fixed_t=0, tp=tp, mult=mult,
                      max_adds=ma, add_step=step))
    for ft, tp, mult, ma, step in itertools.product(
            [180, 300, 450], *[common[k] for k in ("tp", "mult", "max_adds", "add_step")]):
        g.append(dict(mode="fixed", trig=0.0, fixed_t=ft, tp=tp, mult=mult,
                      max_adds=ma, add_step=step))
    return g


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    wins, meta = _windows()
    grid = gen_grid()
    print(f"{len(wins)} windows, {len(grid)} configs")
    rows = []
    with open(OUT, "w") as f:
        for i, p in enumerate(grid):
            r = run_config(wins, meta, p)
            rows.append(r)
            f.write(json.dumps(r) + "\n")
            f.flush()
            if (i + 1) % 12 == 0:
                print(f"  {i+1}/{len(grid)}")
    # rank by capped future EV
    ok = [r for r in rows if r.get("cap_future") and r["cap_future"].get("ev") is not None]
    ok.sort(key=lambda r: -r["cap_future"]["ev"])
    print(f"\n{'mode':8}{'trig/t':>7}{'tp':>5}{'mult':>5}{'adds':>5}{'step':>5}"
          f"{'capN':>6}{'capEV':>7}{'TP%':>6}{'worst':>7}{'maxDD':>7}"
          f"{'futEV':>7}{'futN':>6}")
    for r in ok[:20]:
        c = r["capped"]; fu = r["cap_future"]
        trig = f"{r['trig']:.2f}" if r["mode"] == "adverse" else str(r["fixed_t"])
        print(f"{r['mode']:8}{trig:>7}{r['tp']:>5}{r['mult']:>5}{r['max_adds']:>5}"
              f"{r['add_step']:>5}{c['n']:>6}{c['ev']:>7.2f}{c['tp_rate']:>6.0f}"
              f"{c['worst']:>7.0f}{c['max_dd']:>7.0f}{fu['ev']:>7.2f}{fu['n']:>6}")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
