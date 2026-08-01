"""hunt/wildcard_lowvol — STAGE 2 refinement of the pass-1 leader:
the LOW-REALIZED-VOL FAVOURITE ("over-determined favourite") edge.

Mechanism (Tier A, latency-free): mid-window, when realized vol is LOW and the
favourite already has a cushion (|spot-strike| >= dist_min bps), the outcome is
near-locked yet the book underprices the favourite (longshot/structure lag). We
decide with a big buffer (>=180s, time_left 240-420) and HOLD to resolution, so
there is no speed race. Settle on CHAINLINK; judge on the fresh-OOS future split.

Pass-1 hits (latency-FLAT 2s~5s, future EV positive):
  v<=1.0,d>=6   FULL +0.22  future +0.23  l5 +0.22  (fut_n 340)
  v<=1.0,d>=10  FULL +0.24  future +0.31  l5 +0.16  (fut_n 249)
  v<=0.7,d>=8   FULL +0.20  future +0.34  l5 +0.33  (fut_n 143)
future CI lower bounds were still slightly <0; this stage sweeps vol / dist /
fav_ask / time-window to try to lift the future CI lo above 0 WITHOUT shrinking n
to a jackpot-driven handful, then runs the FULL gauntlet (CPCV, DSR, full latency
sweep) on the finalists.

Run: uv run python -m research.analysis.hunt.wildcard_lowvol
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L
from research.analysis.hunt.wildcard import fast_sim, _book, _cl
from research.lib.stats import window_clustered_bootstrap


def _prep(b: pd.DataFrame) -> pd.DataFrame:
    b = b.copy()
    b["vol_bps"] = b["realized_vol"] * 100.0
    return b


def screen(name, cand, store):
    if cand is None or len(cand) == 0:
        print(f"{name:40} (empty)", flush=True); return
    dec = L.first_tick(cand, (cand["fav_side"] == "yes").to_numpy())
    if len(dec) < 5:
        print(f"{name:40} n={len(dec)} (thin)", flush=True); return
    l2 = fast_sim(dec, 2)
    if len(l2) < 5:
        print(f"{name:40} fills<5", flush=True); return
    l5 = fast_sim(dec, 5)
    l10 = fast_sim(dec, 10)
    fu = l2[l2["split"] == "future"]
    full_ev, full_tot = float(l2["pnl"].mean()), float(l2["pnl"].sum())
    fut_ev = float(fu["pnl"].mean()) if len(fu) else float("nan")
    fut5 = float(l5[l5["split"] == "future"]["pnl"].mean()) if len(l5) else float("nan")
    fut10 = float(l10[l10["split"] == "future"]["pnl"].mean()) if len(l10) else float("nan")
    if len(fu) >= 5:
        flo, _, fhi = window_clustered_bootstrap(fu["pnl"].values, fu["slug"].values, n=2000)
        # also FULL CI
        Flo, _, Fhi = window_clustered_bootstrap(l2["pnl"].values, l2["slug"].values, n=2000)
    else:
        flo = fhi = Flo = Fhi = float("nan")
    print(f"{name:40} n={len(l2):>4} FULL ${full_ev:+.2f}[{Flo:+.2f},{Fhi:+.2f}]WR{l2['won'].mean()*100:.0f}%"
          f" | fut ${fut_ev:+.2f}[{flo:+.2f},{fhi:+.2f}]n{len(fu)}"
          f" | futlat 2/5/10 ${fut_ev:+.2f}/${fut5:+.2f}/${fut10:+.2f}"
          f" | tot{full_tot:+.0f}", flush=True)
    store.append(dict(name=name, dec=dec, full_ev=full_ev, fut_ev=fut_ev, fut_lo=flo,
                      fut_hi=fhi, fut_n=len(fu), n=len(l2), fut5=fut5, fut10=fut10,
                      full_lo=Flo, full_hi=Fhi))


def run(top_k: int = 6):
    b = _prep(L.load_base())
    print("warming caches ...", flush=True)
    _book(); _cl()
    store = []

    print("\n=== A) vol x dist grid (fav_ask 0.55-0.92, time_left 240-420) ===", flush=True)
    for vmax in [0.5, 0.7, 0.9, 1.2]:
        for dmin in [6, 8, 10, 12, 15]:
            c = b[b["time_left_sec"].between(240, 420) & b["book_healthy"]
                  & (b["vol_bps"] <= vmax) & (b["abs_dist_bps"] >= dmin)
                  & b["fav_ask"].between(0.55, 0.92) & b["consistent"]]
            screen(f"A v<={vmax} d>={dmin}", c, store)

    print("\n=== B) fav_ask band sweep (v<=1.0, d>=8, time_left 240-420) ===", flush=True)
    for lo, hi in [(0.55, 0.75), (0.60, 0.80), (0.65, 0.85), (0.70, 0.90), (0.55, 0.90), (0.75, 0.92)]:
        c = b[b["time_left_sec"].between(240, 420) & b["book_healthy"]
              & (b["vol_bps"] <= 1.0) & (b["abs_dist_bps"] >= 8)
              & b["fav_ask"].between(lo, hi) & b["consistent"]]
        screen(f"B fav[{lo:.2f},{hi:.2f}]", c, store)

    print("\n=== C) time-window sweep (v<=1.0, d>=8, fav 0.55-0.92) ===", flush=True)
    for tl in [(180, 300), (240, 420), (300, 480), (180, 480), (360, 600), (120, 360)]:
        c = b[b["time_left_sec"].between(*tl) & b["book_healthy"]
              & (b["vol_bps"] <= 1.0) & (b["abs_dist_bps"] >= 8)
              & b["fav_ask"].between(0.55, 0.92) & b["consistent"]]
        screen(f"C tl{tl[0]}-{tl[1]}", c, store)

    print("\n=== D) tighter: low vol AND big cushion AND high-confidence fav ===", flush=True)
    for vmax, dmin, lo, hi in [(0.7, 10, 0.60, 0.90), (0.8, 12, 0.55, 0.90),
                               (0.6, 8, 0.55, 0.85), (0.9, 12, 0.60, 0.92),
                               (0.7, 12, 0.55, 0.92), (1.0, 8, 0.60, 0.88)]:
        c = b[b["time_left_sec"].between(240, 420) & b["book_healthy"]
              & (b["vol_bps"] <= vmax) & (b["abs_dist_bps"] >= dmin)
              & b["fav_ask"].between(lo, hi) & b["consistent"]]
        screen(f"D v<={vmax}d>={dmin}f[{lo:.2f},{hi:.2f}]", c, store)

    # ---- rank by future CI lower bound; require fut_n>=40 (decision-rule floor) ----
    ranked = sorted([s for s in store if s["fut_n"] >= 40 and np.isfinite(s["fut_lo"])],
                    key=lambda s: s["fut_lo"], reverse=True)
    print("\n----- ranking by future CI lower bound (fut_n>=40) -----", flush=True)
    for s in ranked[:14]:
        print(f"  {s['name']:40} fut ${s['fut_ev']:+.2f}[{s['fut_lo']:+.2f},{s['fut_hi']:+.2f}]"
              f"n{s['fut_n']} l5/10 ${s['fut5']:+.2f}/${s['fut10']:+.2f} | "
              f"FULL ${s['full_ev']:+.2f}[{s['full_lo']:+.2f},{s['full_hi']:+.2f}] n{s['n']}", flush=True)

    print(f"\n===== FULL GAUNTLET on top {top_k} (CPCV + DSR + full latency sweep) =====\n", flush=True)
    fin = []
    for s in ranked[:top_k]:
        led = L.simulate(s["dec"], latency=2)
        if led is None or len(led) < 3:
            print(f"{s['name']:40} (no full-sim fills)"); continue
        lat = L.latency_survival(s["dec"])
        print(L.verdict_line(s["name"], led, lat), flush=True)
        e = L.evaluate(led)
        # jackpot sanity: FULL total vs ev*n, and max single-trade pnl share
        tot = e["per_split"]["FULL"]["total"]
        mx = float(led["pnl"].max())
        print(f"    jackpot-check: FULL total {tot:+.0f} vs ev*n {e['ev']*e['n']:+.0f}; "
              f"max single trade ${mx:+.2f} ({100*mx/max(tot,1e-9):.0f}% of total); "
              f"fut latlo: " + " ".join(f"{k}s${lat[k]['fut_ev']:+.2f}" for k in (2,3,5,10) if lat.get(k,{}).get('fut_ev') is not None), flush=True)
        fin.append(dict(s=s, e=e, lat=lat, led=led))
        print()
    return fin


if __name__ == "__main__":
    run()
