"""REFINE the persistence reversion edge (fade a large prior-window move).

The broad sweep found: REVERSAL after a large prior-window |move| is the only
positive direction, latency-IMMUNE (EV flat/rising 2->10s = genuine Tier-A), but
the future CI lower bound sits just below 0 (~-0.15 to -0.29). This script:

  1. fine magnitude grid (40/45/50/55/60/65 bps) on the cl prior outcome, with the
     full deep gauntlet, to locate the best future CI-lo at defensible n.
  2. the OBSERVABLE (coinbase) prior-move version (what a live trader actually
     sees at window open) at the same grid.
  3. JACKPOT audit on the leading variants: biggest single-trade pnls, and EV
     after removing the top-2 winners (is the edge a handful of lottery hits?).
  4. per-split breakdown (dev/holdout/future) so we see whether 'future' is the
     weak split (overfit risk) or just thin.

Run: uv run python -m research.analysis.hunt.persistence_refine
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L
from research.analysis.hunt.persistence import build_windows, decide


def deep_line(name: str, dec: pd.DataFrame):
    if dec is None or dec.empty:
        print(f"{name:34} n=0"); return None
    led = L.simulate(dec, latency=2)
    if led is None or len(led) < 5:
        print(f"{name:34} n={0 if led is None else len(led)} (few)"); return None
    e = L.evaluate(led)
    lat = L.latency_survival(dec)
    fl = e["per_split"]["FULL"]; fu = e["per_split"].get("future")
    de = e["per_split"].get("dev"); ho = e["per_split"].get("holdout")
    l = {k: lat.get(k, {}).get("ev") for k in (2, 5, 10)}
    flt = {k: lat.get(k, {}).get("fut_ev") for k in (2, 5, 10)}
    fu_s = (f"${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}" if fu else "n/a")
    print(f"{name:34} n{e['n']:>3} FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}]WR{fl['wr']:.0f} fut {fu_s}")
    print(f"     dev ${de['ev'] if de else float('nan'):+.2f}n{de['n'] if de else 0} "
          f"hold ${ho['ev'] if ho else float('nan'):+.2f}n{ho['n'] if ho else 0} | "
          f"lat2/5/10 ${l[2]:+.2f}/{l[5]:+.2f}/{l[10]:+.2f} futlat ${flt[2]:+.2f}/{flt[5]:+.2f}/{flt[10]:+.2f} "
          f"CPCV{e['cpcv'].get('pct_pos',0):.0f} DSR{e['dsr']['dsr']}")
    return dict(name=name, led=led, e=e, lat=lat, fl=fl, fu=fu, dec=dec)


def jackpot_audit(name: str, led: pd.DataFrame):
    p = np.sort(led["pnl"].values)
    top = p[-3:][::-1]
    bot = p[:3]
    # EV with top-2 winners removed
    ev_no2 = led.sort_values("pnl").iloc[:-2]["pnl"].mean()
    fut = led[led["split"] == "future"]
    fut_ev_no2 = (fut.sort_values("pnl").iloc[:-2]["pnl"].mean() if len(fut) > 4 else float("nan"))
    print(f"  [{name}] biggest wins {np.round(top,1)} biggest losses {np.round(bot,1)} | "
          f"FULL ev {led['pnl'].mean():+.2f} -> drop-top2 {ev_no2:+.2f} | "
          f"future ev {fut['pnl'].mean():+.2f} -> drop-top2 {fut_ev_no2:+.2f} (n_fut {len(fut)})")


def main():
    b = L.load_base().copy()
    w = build_windows(b)
    base_ok = w["consec"] & w["prev_cl_up"].notna()
    base_cb = w["consec"] & w["prev_cb_up"].notna()

    print("=== 1. FINE magnitude grid — REVERSAL on CHAINLINK prior outcome ===")
    recs = {}
    for thr in (40, 45, 50, 55, 60, 65):
        m = base_ok & (w["prev_abs_move"] >= thr)
        r = deep_line(f"rev(cl) |move|>={thr}", decide(b, w, m, w["prev_cl_up"] == 0, 780, 860))
        if r: recs[r["name"]] = r

    print("\n=== 2. FINE magnitude grid — REVERSAL on OBSERVABLE (coinbase) prior move ===")
    # buy AGAINST the observed prior direction (prev_cb_up): if prev went up, buy DOWN.
    for thr in (40, 50, 60):
        m = base_cb & (w["prev_abs_move"] >= thr)
        r = deep_line(f"rev(cb-obs) |move|>={thr}", decide(b, w, m, w["prev_cb_up"] == 0, 780, 860))
        if r: recs[r["name"]] = r

    print("\n=== 3. JACKPOT audit (is the edge a few lottery hits?) ===")
    for nm in ("rev(cl) |move|>=50", "rev(cl) |move|>=45", "rev(cl) |move|>=40"):
        if nm in recs:
            jackpot_audit(nm, recs[nm]["led"])

    print("\n=== 4. RANK candidates by future CI lower bound (n_fut>=40 only) ===")
    ok = [r for r in recs.values() if r["fu"] is not None and r["fu"]["n"] >= 40]
    ok.sort(key=lambda r: r["fu"]["lo"], reverse=True)
    for r in ok:
        fu, fl = r["fu"], r["fl"]
        l = r["lat"]
        print(f"  {r['name']:34} fut ${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']} "
              f"FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}]n{r['e']['n']} "
              f"lat2/5/10 ${l[2]['ev']:+.2f}/{l[5]['ev']:+.2f}/{l[10]['ev']:+.2f} "
              f"CPCV{r['e']['cpcv'].get('pct_pos',0):.0f}")
    if ok:
        best = ok[0]
        print(f"\nBEST defensible (n_fut>=40): {best['name']}")
        e, fl, fu, lat = best["e"], best["fl"], best["fu"], best["lat"]
        print(f"  n={e['n']} FULL ev={fl['ev']} ci=[{fl['lo']},{fl['hi']}] total={fl['total']} "
              f"ev*n={round(fl['ev']*e['n'],1)}")
        print(f"  future ev={fu['ev']} ci=[{fu['lo']},{fu['hi']}] n_fut={fu['n']}")
        print(f"  latency EV: " + ", ".join(f"{k}s {lat[k]['ev']:+.2f}" for k in (2,5,10)))
        print(f"  latency FUT EV: " + ", ".join(f"{k}s {lat[k].get('fut_ev'):+.2f}" for k in (2,5,10)))
        print(f"  CPCV {e['cpcv'].get('pct_pos')}% DSR {e['dsr']['dsr']}")


if __name__ == "__main__":
    main()
