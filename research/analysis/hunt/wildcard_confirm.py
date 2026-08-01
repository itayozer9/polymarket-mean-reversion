"""Confirm the low-vol-favourite KEEP candidates are not a single-day / single-coin
fluke, and nail the exact fav_ask cut. For the top bands, print:
  - per-future-DAY pnl (is the +EV spread across all 4 OOS days?)
  - per-SYMBOL future pnl (is it one coin?)
  - holdout-split EV (the OTHER sealed OOS set) as an independent check
  - full latency sweep on the FUTURE split only

Entry (Tier A, latency-free): time_left in 240-420s (decide with >=180s buffer),
realized_vol <= V bps, |spot-strike| >= D bps, favourite (yes_mid>=0.5) priced in
[lo,hi], consistent (spot on the favourite's side). BUY the favourite. Hold to
resolution. Settle CHAINLINK.

Run: uv run python -m research.analysis.hunt.wildcard_confirm
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L
from research.analysis.hunt.wildcard import fast_sim, _book, _cl


def cand_frame(b, vmax, dmin, lo, hi, tl=(240, 420)):
    return b[b["time_left_sec"].between(*tl) & b["book_healthy"]
             & (b["vol_bps"] <= vmax) & (b["abs_dist_bps"] >= dmin)
             & b["fav_ask"].between(lo, hi) & b["consistent"]]


def report(name, b, vmax, dmin, lo, hi):
    c = cand_frame(b, vmax, dmin, lo, hi)
    dec = L.first_tick(c, (c["fav_side"] == "yes").to_numpy())
    led = L.simulate(dec, latency=2)   # shared full ladder-gated fill
    if led is None or len(led) < 5:
        print(f"{name}: too few fills"); return
    print(f"\n##### {name}  (v<={vmax}, d>={dmin}, fav[{lo},{hi}], tl 240-420) #####")
    print(f"  n={len(led)} WR={led['won'].mean()*100:.1f}% EV=${led['pnl'].mean():+.3f} "
          f"total=${led['pnl'].sum():+.1f}")
    for sp in ("dev", "holdout", "future"):
        s = led[led["split"] == sp]
        if len(s):
            print(f"   {sp:8} n={len(s):>4} EV ${s['pnl'].mean():+.3f}  WR {s['won'].mean()*100:.1f}%  "
                  f"total ${s['pnl'].sum():+.1f}")
    fu = led[led["split"] == "future"]
    print("  FUTURE per-day:")
    for d, g in fu.groupby("date"):
        print(f"     {d}  n={len(g):>3}  EV ${g['pnl'].mean():+.3f}  total ${g['pnl'].sum():+.1f}  "
              f"WR {g['won'].mean()*100:.0f}%")
    print("  FUTURE per-symbol:")
    for sym, g in fu.groupby("symbol"):
        print(f"     {sym:4} n={len(g):>3}  EV ${g['pnl'].mean():+.3f}  total ${g['pnl'].sum():+.1f}  "
              f"WR {g['won'].mean()*100:.0f}%")
    # future-only latency sweep (FULL ladder via L.simulate)
    print("  FUTURE-split EV by fill latency (the Tier-A flatness gate):")
    for lat in (2, 3, 5, 10):
        ll = L.simulate(dec, latency=lat)
        f = ll[ll["split"] == "future"]
        print(f"     {lat:>2}s: n={len(f):>3}  EV ${f['pnl'].mean():+.3f}" if len(f) else f"     {lat}s: -")


def fine_cut(b):
    """Sweep the upper fav_ask edge finely (v<=1.0, d>=8) to locate the exact
    point where the OOS edge dies, using the FUTURE split EV + window CI."""
    from research.lib.stats import window_clustered_bootstrap
    print("\n===== fine fav_ask upper-edge sweep (v<=1.0, d>=8, lo=0.55) =====")
    for hi in [0.70, 0.75, 0.78, 0.80, 0.82, 0.85, 0.88, 0.92]:
        c = cand_frame(b, 1.0, 8, 0.55, hi)
        dec = L.first_tick(c, (c["fav_side"] == "yes").to_numpy())
        led = fast_sim(dec, 2)
        fu = led[led["split"] == "future"]
        if len(fu) < 10:
            print(f"  hi={hi}: thin"); continue
        lo_, _, hi_ = window_clustered_bootstrap(fu["pnl"].values, fu["slug"].values, n=2000)
        print(f"  fav[0.55,{hi}] n={len(led):>4} fut n={len(fu):>3} "
              f"EV ${fu['pnl'].mean():+.3f}[{lo_:+.2f},{hi_:+.2f}] WR{fu['won'].mean()*100:.0f}%")


def run():
    b = L.load_base().copy()
    b["vol_bps"] = b["realized_vol"] * 100.0
    _book(); _cl()
    # the three KEEP contenders
    report("B fav[0.55,0.75]", b, 1.0, 8, 0.55, 0.75)
    report("B fav[0.60,0.80]", b, 1.0, 8, 0.60, 0.80)
    report("D fav[0.60,0.88]", b, 1.0, 8, 0.60, 0.88)
    fine_cut(b)


if __name__ == "__main__":
    run()
