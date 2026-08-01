"""hunt/pricestruct — STAGE 2 deep-dive on the one survivor.

Stage 1 (git history of this file) swept 29 variants. Result:
  - Favourite-longshot bias confirmed: buying the LONGSHOT is -$2..-$5/trade in
    every cheap band. Robust, but you can't trade a guaranteed loser.
  - The ONLY positive pocket is the DEEP FAVOURITE ~0.88-0.95, and it is almost
    entirely the DOWN-favourite (buy NO):
        FAV0.88-0.95 buy-NO  : FULL +0.23[+0.09,+0.37] fut +0.26[+0.03,+0.47]n361
                               lat 2/3/5/10 = .23/.22/.18/.22  CPCV 93%  DSR 0.47
        FAV0.88-0.95 buy-YES : FULL -0.19  fut -0.19   (no edge on the UP side)

This stage stress-tests the DOWN-favourite survivor: time-left robustness incl.
decide-at-OPEN, jackpot-dependence (trimmed EV / top-contributor share), the
UP/DOWN asymmetry mechanism (Chainlink lags Coinbase -> is "buy NO" just shorting
a stale-high Coinbase-implied book?), per-split detail, and a tighter price band.

Run:  uv run python -m research.analysis.hunt.pricestruct
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L
from research.lib.stats import window_clustered_bootstrap


def _fav_buy_yes(df: pd.DataFrame) -> np.ndarray:
    return (df["yes_best_ask"].to_numpy("f8") >= df["no_best_ask"].to_numpy("f8"))


def _fav_ask(df: pd.DataFrame) -> np.ndarray:
    ya = df["yes_best_ask"].to_numpy("f8")
    na = df["no_best_ask"].to_numpy("f8")
    return np.where(ya >= na, ya, na)


def _line(name, e, lat, dec_led=None):
    fu = e["per_split"].get("future")
    ho = e["per_split"].get("holdout")
    fl = e["per_split"]["FULL"]

    def lv(k):
        return lat.get(k, {}).get("ev")
    fut = (f"fut ${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}WR{fu['wr']:.0f}"
           if fu else "fut n/a")
    hol = (f"hold ${ho['ev']:+.2f}[{ho['lo']:+.2f},{ho['hi']:+.2f}]n{ho['n']}"
           if ho else "hold n/a")
    print(f"{name:38} n={e['n']:>4} FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}]"
          f"WR{fl['wr']:.0f} | {fut} | {hol} | "
          f"lat ${lv(2)}/{lv(3)}/{lv(5)}/{lv(10)} | "
          f"CPCV {e['cpcv'].get('pct_pos')}% DSR {e['dsr']['dsr']}")


def _run(b, name, mask, tl_lo, tl_hi, buy_yes_fn=_fav_buy_yes):
    cand = b[np.asarray(mask) & (b["time_left_sec"] >= tl_lo) & (b["time_left_sec"] <= tl_hi)]
    if len(cand) == 0:
        print(f"{name:38} n=0 ticks"); return None
    dec = L.first_tick(cand, buy_yes_fn(cand))
    led = L.simulate(dec, latency=2)
    e = L.evaluate(led)
    if e["n"] == 0:
        print(f"{name:38} n=0 fills"); return None
    lat = L.latency_survival(dec)
    _line(name, e, lat)
    return dict(name=name, e=e, lat=lat, led=led, dec=dec)


def main() -> None:
    b = L.load_base()
    fav_ask = _fav_ask(b)
    healthy = (b["book_healthy"] == True).to_numpy()
    yes_is_fav = _fav_buy_yes(b)
    band = healthy & (fav_ask > 0.88) & (fav_ask <= 0.95)
    down = band & (~yes_is_fav)   # DOWN is the favourite -> buy NO
    up = band & yes_is_fav

    print(f"base windows={b['slug'].nunique():,}\n")

    # 1) DOWN-fav across the whole time-left axis incl decide-at-OPEN (Tier A ideal)
    print("--- 1) DOWN-fav (buy NO), 0.88-0.95, across decide buffer (Tier A) ---")
    rows = []
    for tl_lo, tl_hi in [(840, 899), (720, 840), (600, 720), (480, 600),
                         (420, 480), (360, 480), (240, 360), (120, 240),
                         (60, 120), (300, 600), (60, 600)]:
        r = _run(b, f"DOWNfav tl[{tl_lo},{tl_hi}]", down, tl_lo, tl_hi)
        if r:
            rows.append(r)

    # 2) UP-fav same axis (confirm the asymmetry is persistent, not one window)
    print("\n--- 2) UP-fav (buy YES) same axis (should stay ~0 or -) ---")
    for tl_lo, tl_hi in [(720, 840), (480, 600), (420, 480), (240, 360), (60, 600)]:
        _run(b, f"UPfav tl[{tl_lo},{tl_hi}]", up, tl_lo, tl_hi)

    # 3) jackpot / fragility check on the headline DOWN-fav tl[420,480]
    print("\n--- 3) DOWN-fav tl[420,480] fragility: per-symbol, trimmed EV, top share ---")
    head = next((r for r in rows if r["name"] == "DOWNfav tl[420,480]"), None)
    if head:
        led = head["led"]
        print(f"  by SYMBOL:")
        for sym, g in led.groupby("symbol"):
            print(f"    {sym:6} n={len(g):4d} ev ${g['pnl'].mean():+.3f} wr {g['won'].mean()*100:.0f}% tot ${g['pnl'].sum():+.1f}")
        pnl = np.sort(led["pnl"].values)
        n = len(pnl)
        full = pnl.mean()
        # win pnl is bounded (~+0.7 max on a 0.9 ask); losers are -10. So check if
        # EV is propped by a few big WINS being removed -> drop top-5 winners.
        trimmed = pnl[:-5].mean() if n > 10 else full
        topwin_share = pnl[-5:].sum() / pnl.sum() if pnl.sum() != 0 else float("nan")
        worst_share = pnl[:5].sum() / pnl.sum() if pnl.sum() != 0 else float("nan")
        print(f"  FULL ev ${full:+.3f} | drop-top-5-wins ev ${trimmed:+.3f} | "
              f"top5-win % of total {topwin_share:.2f} | n={n}")
        # daily PnL spread (DSR sanity)
        dly = led.groupby("date")["pnl"].sum()
        print(f"  daily PnL: n_days {len(dly)} mean ${dly.mean():+.2f} pos-days {(dly>0).mean()*100:.0f}% "
              f"min ${dly.min():+.1f} max ${dly.max():+.1f}")

    # 4) mechanism: is DOWN-fav just shorting a stale-HIGH Coinbase book?
    #    Chainlink lags Coinbase. If NO is favourite, the book says price likely
    #    BELOW strike. Split by cl-cb basis sign at entry.
    print("\n--- 4) mechanism: DOWN-fav tl[420,480] split by Chainlink-Coinbase basis ---")
    cand = b[down & (b["time_left_sec"] >= 420) & (b["time_left_sec"] <= 480)]
    cand = cand.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False).first()
    basis = cand["cl_cb_basis_bps"].to_numpy("f8")
    for tag, m in [("CL>CB(basis>2)", basis > 2), ("CL~CB(|b|<=2)", np.abs(basis) <= 2),
                   ("CL<CB(basis<-2)", basis < -2)]:
        sub = cand[m]
        if len(sub) < 20:
            print(f"    {tag:18} n={len(sub)} thin"); continue
        dec = sub.rename(columns={"seconds_into_window": "entry_sec"})
        dec = dec.assign(buy_yes=False)[["slug", "symbol", "date", "split", "window_start_ts", "entry_sec", "buy_yes"]]
        led = L.simulate(dec, latency=2)
        if len(led) < 5:
            print(f"    {tag:18} n_fill={len(led)} thin"); continue
        lo, _, hi = window_clustered_bootstrap(led["pnl"].values, led["slug"].values, n=2000)
        print(f"    {tag:18} n={len(led):4d} ev ${led['pnl'].mean():+.3f}[{lo:+.2f},{hi:+.2f}] "
              f"wr {led['won'].mean()*100:.0f}% tot ${led['pnl'].sum():+.1f}")

    # 5) tighter / shifted price bands around the DOWN-fav sweet spot
    print("\n--- 5) DOWN-fav tl[420,480], price-band sensitivity ---")
    for lo, hi in [(0.86, 0.93), (0.88, 0.93), (0.89, 0.94), (0.90, 0.95),
                   (0.88, 0.96), (0.87, 0.94), (0.91, 0.96)]:
        m = healthy & (~yes_is_fav) & (fav_ask > lo) & (fav_ask <= hi)
        _run(b, f"DOWNfav ask({lo:.2f},{hi:.2f}]", m, 420, 480)

    # 6) DOWN-fav tl[420,480] + low-vol stack (does the loVol future-pocket persist
    #    once we condition on the working side AND a wider buffer?)
    print("\n--- 6) DOWN-fav x vol regime (tl 300-600, robustness of loVol pocket) ---")
    rv = b["realized_vol"].to_numpy("f8")
    qlo, qhi = np.nanpercentile(rv, [33, 66])
    for tag, mvol in [("loVol", rv <= qlo), ("midVol", (rv > qlo) & (rv <= qhi)), ("hiVol", rv > qhi)]:
        _run(b, f"DOWNfav {tag}", down & mvol, 300, 600)


if __name__ == "__main__":
    main()
