"""Task 8c — window-open (t=0) forensics probe.

Settles whether the "divergence edge" reported in docs/research/divergence_edge.md
(+$6/trade, 78% WR, ~74% of trades at seconds_into_window==0) is a real, tradeable
dislocation or a data artifact.

Reads ONLY:
  data/research/ticks_15m.parquet   (1 Hz tick rows, 15m windows)
  data/research/ticks_5m.parquet    (used only as an extra coinbase-price source)
  data/research/windows.parquet     (one row/window, clean outcome_up)

Does NOT modify any data or the loader. Run:  uv run python research/audit/window_open_probe.py
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TICKS15 = os.path.join(REPO, "data", "research", "ticks_15m.parquet")
TICKS5 = os.path.join(REPO, "data", "research", "ticks_5m.parquet")
WINDOWS = os.path.join(REPO, "data", "research", "windows.parquet")


def hr(s: str) -> None:
    print("\n" + "=" * 78 + "\n" + s + "\n" + "=" * 78)


def main() -> None:
    t = pd.read_parquet(TICKS15).sort_values(["slug", "seconds_into_window"])
    w = (pd.read_parquet(WINDOWS)[["slug", "outcome_up", "end_price"]]
         .drop_duplicates("slug").set_index("slug"))
    outcome_up = w["outcome_up"]

    # Per-window aggregates ----------------------------------------------------
    t0 = t[t.seconds_into_window == 0].groupby("slug").first()
    last = t.groupby("slug").last()
    sp = t.groupby("slug")["start_price"].first()
    cb0 = t0["coinbase_price"]
    mv0 = t0["move_pct"]
    ym0 = t0["yes_mid"]
    cblast = last["coinbase_price"]

    # ---- Q1: start_price identity ------------------------------------------
    hr("Q1  start_price identity")
    nuniq = t.groupby("slug")["start_price"].nunique()
    print(f"  start_price constant within window: "
          f"{(nuniq == 1).mean():.4f} of {len(nuniq)} windows")

    # ---- Q2: why is move_pct != 0 at t=0 -----------------------------------
    hr("Q2  move_pct at seconds_into_window == 0")
    a = mv0.abs()
    print(f"  n t=0 ticks                : {len(mv0)}")
    print(f"  move_pct  mean/median/std  : "
          f"{mv0.mean():+.4f} / {mv0.median():+.4f} / {mv0.std():.4f}")
    print(f"  |move_pct| median          : {a.median():.4f}")
    print(f"  frac |move_pct| > 0.05%    : {(a > 0.05).mean():.4f}")
    print(f"  (coinbase@t0 - start_price)/start_price*100 == move_pct: "
          f"max abs diff {(mv0 - (cb0 - sp) / sp * 100).abs().max():.2e}")

    # what timestamp does start_price actually equal?
    res = []
    for sym, g in t.groupby("symbol"):
        g = g.reset_index(drop=True)
        cb = g["coinbase_price"].values
        ts = g["timestamp_ms"].values // 1000
        win = g.groupby("slug").agg(wst=("window_start_ts", "first"),
                                    s=("start_price", "first")).reset_index()
        win["wst"] = win["wst"].astype("int64")
        for _, r in win.iterrows():
            eq = np.where(cb == r["s"])[0]
            if len(eq):
                offs = (ts[eq] - r["wst"])
                res.append(offs[np.argmin(np.abs(offs))])
    off = pd.Series(res)
    in_band = off.between(-1900, -1700)
    print(f"  start_price exact-equals a coinbase tick: nearest-to-open offset")
    print(f"    frac in [-1900,-1700]s   : {in_band.mean():.4f}  "
          f"(=> sampled ~30 min BEFORE window-open)")
    print(f"    frac within +/-30s of open: {(off.abs() <= 30).mean():.4f}")

    # ---- Q3: window time structure -----------------------------------------
    hr("Q3  window time structure")
    info = t.groupby("slug").agg(symbol=("symbol", "first"),
                                 wst=("window_start_ts", "first"),
                                 wet=("window_end_ts", "first")).reset_index()
    info["wst"] = info["wst"].astype("int64")
    info["wet"] = info["wet"].astype("int64")
    gaps = []
    for _, g in info.groupby("symbol"):
        g = g.sort_values("wst")
        gaps.append((g["wst"].shift(-1) - g["wet"]).dropna())
    gp = pd.concat(gaps)
    print(f"  durations (wet-wst) == 900s : {(info.wet - info.wst).eq(900).mean():.4f}")
    print(f"  contiguous (gap == 0)       : {(gp == 0).mean():.4f}")
    print(f"  overlapping (gap < 0)       : {(gp < 0).mean():.4f}")

    # ---- Q4: is the t=0 book fresh or carried over -------------------------
    hr("Q4  is the t=0 book fresh or carried over from the prior window")
    chains = []
    for _, g in info.groupby("symbol"):
        g = g.sort_values("wst").reset_index(drop=True)
        g["prev"] = g["slug"].shift(1)
        g["contig"] = (g["wst"] - g["wst"].shift(1) == 900)
        chains.append(g[g["contig"]].dropna(subset=["prev"]))
    C = pd.concat(chains)
    ident = 0
    for _, r in C.iterrows():
        if r["slug"] not in t0.index or r["prev"] not in last.index:
            continue
        c, p = t0.loc[r["slug"]], last.loc[r["prev"]]
        ident += int(c["yes_best_bid"] == p["yes_best_bid"]
                     and c["yes_best_ask"] == p["yes_best_ask"]
                     and c["no_best_bid"] == p["no_best_bid"]
                     and c["no_best_ask"] == p["no_best_ask"])
    print(f"  t=0 book identical to prior-window last tick: {ident}/{len(C)}")
    print(f"  t=0 yes_mid  median        : {ym0.median():.4f}")
    print(f"  frac t=0 yes_mid in [.45,.55]: {ym0.between(0.45, 0.55).mean():.4f}")
    print(f"  t=0 yes_ask_depth median   : {t0['yes_ask_depth'].median():.1f} shares")
    twosided = ((t0.yes_best_bid > 0) & (t0.yes_best_ask > 0)
                & (t0.no_best_bid > 0) & (t0.no_best_ask > 0))
    print(f"  frac t=0 two-sided book    : {twosided.mean():.4f}")

    # ---- Q5: reconcile the edge with outcomes ------------------------------
    hr("Q5  does the t=0 edge reconcile with the true outcome")
    g5 = t0.join(outcome_up.rename("true_up")).dropna(subset=["true_up"])
    g5 = g5[(g5.yes_best_ask > 0) & (g5.no_best_ask > 0) & (g5.move_pct.abs() > 1e-9)]
    fav_up = g5.move_pct > 0
    fav_ask = np.where(fav_up, g5.yes_best_ask, g5.no_best_ask)
    fav_win = np.where(fav_up, g5.true_up == 1, g5.true_up == 0)
    print(f"  n trades                   : {len(g5)}")
    print(f"  move_pct-favored side WIN  : {fav_win.mean():.4f}")
    print(f"  move_pct-favored mean ask  : {fav_ask.mean():.4f}")
    shares = 10.0 / fav_ask
    fee = 0.07 * fav_ask * (1 - fav_ask) * shares
    pnl = np.where(fav_win, shares, 0.0) - 10.0 - fee
    print(f"  mean PnL/trade ($10 stake) : ${pnl.mean():+.3f}  "
          f"(reproduces divergence_edge.md headline)")
    for lo, hi in [(0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 99)]:
        m = g5.move_pct.abs().between(lo, hi)
        if m.any():
            print(f"    |move_pct| [{lo},{hi}): n={m.sum():4d} "
                  f"win={fav_win[m.values].mean():.3f} ask={fav_ask[m.values].mean():.3f}")

    # ---- Q6: reprice speed -------------------------------------------------
    hr("Q6  reprice speed - book vs move_pct, book vs outcome")
    print("  sec | corr(yes_mid,move_pct) | corr(yes_mid,outcome) | favored_mid")
    for s in [0, 5, 15, 30, 60, 120, 300, 540]:
        ts = t[t.seconds_into_window == s].set_index("slug")
        ts = ts[(ts.yes_best_bid > 0) & (ts.no_best_bid > 0)]
        ou = outcome_up.reindex(ts.index)
        v = ou.notna()
        c_mv = ts.yes_mid.corr(ts.move_pct)
        c_ou = ts.yes_mid[v].corr(ou[v])
        fav = mv0.reindex(ts.index) > 0
        fm = np.where(fav, ts.yes_mid, ts.no_mid)
        print(f"  {s:4d} |        {c_mv:+.3f}        |        {c_ou:+.3f}        "
              f"|   {np.nanmean(fm):.4f}")

    # ---- the decisive cross-check -----------------------------------------
    hr("DECISIVE  what strike does the market price vs what outcome is scored on")
    df = pd.DataFrame({"sp": sp, "cb0": cb0, "wend": w["end_price"],
                       "true_up": outcome_up, "ym0": ym0}).dropna()
    print(f"  outcome_up == (end_price > start_price)   : "
          f"{((df.wend > df.sp).astype(int) == df.true_up).mean():.4f}")
    print(f"  outcome_up == (end_price > coinbase@t0)   : "
          f"{((df.wend > df.cb0).astype(int) == df.true_up).mean():.4f}")
    print(f"  corr(yes_mid@t0, outcome_up)              : "
          f"{df.ym0.corr(df.true_up):+.3f}   (book leans toward the LOSER)")
    last2 = (t[(t.yes_best_bid > 0) & (t.no_best_bid > 0)]
             .groupby("slug").tail(1).set_index("slug"))
    last2 = last2.join(outcome_up.rename("ou")).dropna(subset=["ou"])
    print(f"  book FINAL lean agrees with outcome       : "
          f"{((last2.yes_mid > 0.5) == (last2.ou == 1)).mean():.4f} "
          f"(mean last_sec {last2.seconds_into_window.mean():.0f})")
    print("\n  => start_price is spot ~30 min BEFORE window-open (discovery k=+2")
    print("     backfill bug). outcomes.csv scores end vs that stale strike. The")
    print("     real Polymarket book never converges to it. The +$6/trade is fake.")


if __name__ == "__main__":
    main()
