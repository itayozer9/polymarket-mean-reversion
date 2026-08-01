"""new_signals_sweep (WP3) — three untested signal families, judged by edge_lab.

(a) CUMULATIVE whole-window signed trade-flow (NOT the dead 5s flow): does net
    taker pressure accumulated open->decision predict the settlement (follow/fade),
    standalone AND as a confirmation filter on determinism?
(b) ROUND-NUMBER strike-pinning: does the strike's proximity to a round number
    change settlement odds (esp XRP, 31% near-round)? filter + weak standalone.
(c) L2 LADDER-SHAPE / wall: depth-behind-top (l2_depth_ask_2c vs top) as a
    confirmation filter on determinism (resting size agrees with the favourite).

All hold-to-resolution, Chainlink-settled, future-blind. Skeptical priors: (a) likely
re-labels determinism, (b) likely XRP noise, (c) likely a next-tick signal that dies
on hold-to-resolution. A survivor must beat the incremental test (adds EV vs det
alone) + full verify.

Run: uv run python -m research.analysis.new_signals_sweep
Out: data/research/hypotheses/new_signals.jsonl
"""
from __future__ import annotations
import itertools
import json
import os

import numpy as np
import pandas as pd

from research.analysis.edge_lab import load_base, first_tick, simulate, evaluate, latency_survival

OUT = os.path.join("data", "research", "hypotheses", "new_signals.jsonl")
ROUND = {"btc": 100.0, "eth": 10.0, "sol": 1.0, "xrp": 0.01}


def prep(b):
    """add cumulative whole-window flow, round-number distance, wall ratio."""
    b = b.sort_values(["slug", "seconds_into_window"]).copy()
    b["cum_flow"] = b.groupby("slug", sort=False)["tr_signed_usd"].cumsum()
    inc = b["symbol"].map(ROUND).to_numpy("f8")
    sp = b["start_price"].to_numpy("f8")
    nearest = np.round(sp / inc) * inc
    b["round_dist_bps"] = np.abs(sp - nearest) / np.where(sp > 0, sp, np.nan) * 1e4
    top = b["l2_ask_depth"].to_numpy("f8")
    d2c = b["l2_depth_ask_2c"].to_numpy("f8")
    b["wall_ratio"] = np.divide(d2c, top, out=np.full(len(b), np.nan), where=top > 1e-9)
    return b


def _fav_yes(c):
    return (c["yes_mid"] >= 0.5).to_numpy()


# ---- family builders (return cand, buy_yes) ----
def fam_flow_signal(b, p):
    """standalone: late window, |cumulative flow| large, follow or fade it."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["cum_flow"].abs() >= p["thr"]) & b["book_healthy"]
         & b["yes_best_ask"].between(0.10, 0.90))
    c = b[m]
    follow = (c["cum_flow"] > 0).to_numpy()        # flow>0 = net YES buying = bullish
    by = follow if p["dir"] == "follow" else ~follow
    return c, by


def fam_flow_det(b, p):
    """determinism + cumulative-flow confirmation (flow agrees with favourite)."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & b["consistent"]
         & b["fav_ask"].between(0.50, 0.90))
    c = b[m]
    fy = _fav_yes(c)
    flow_fav = np.where(fy, c["cum_flow"].to_numpy("f8"), -c["cum_flow"].to_numpy("f8"))
    keep = flow_fav >= p["thr"]
    return c[keep], fy[keep]


def fam_round_det(b, p):
    """determinism, gated near OR far from a round-number strike."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & b["consistent"]
         & b["fav_ask"].between(0.50, 0.90))
    if p["where"] == "near":
        m &= (b["round_dist_bps"] <= p["thr"])
    else:
        m &= (b["round_dist_bps"] >= p["thr"])
    if p.get("sym"):
        m &= (b["symbol"] == p["sym"])
    c = b[m]
    return c, _fav_yes(c)


def fam_wall_det(b, p):
    """determinism + a wall behind the favourite's top (resting size conviction)."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & b["consistent"]
         & b["fav_ask"].between(0.50, 0.90)
         & (b["wall_ratio"] >= p["thr"]))
    c = b[m]
    return c, _fav_yes(c)


BUILD = {"flow_sig": fam_flow_signal, "flow_det": fam_flow_det,
         "round_det": fam_round_det, "wall_det": fam_wall_det}


def gen():
    specs = []
    for t, thr, d in itertools.product([(1, 60), (1, 120), (60, 300), (120, 420)],
                                       [100, 500, 2000], ["follow", "fade"]):
        specs.append(("flow_sig", dict(t_lo=t[0], t_hi=t[1], thr=thr, dir=d)))
    for t, thr, dm in itertools.product([(1, 120), (60, 300), (120, 420)],
                                        [0, 100, 500], [5, 8, 12]):
        specs.append(("flow_det", dict(t_lo=t[0], t_hi=t[1], thr=thr, dist_min=dm)))
    for where, thr, sym in itertools.product(["near", "far"], [3, 5, 10],
                                             [None, "xrp", "btc"]):
        specs.append(("round_det", dict(t_lo=1, t_hi=180, dist_min=8, where=where,
                                        thr=thr, sym=sym)))
    for t, thr, dm in itertools.product([(1, 120), (60, 300)], [1.0, 2.0, 4.0], [5, 8, 12]):
        specs.append(("wall_det", dict(t_lo=t[0], t_hi=t[1], thr=thr, dist_min=dm)))
    return specs


def run_spec(b, fam, p):
    c, by = BUILD[fam](b, p)
    if c is None or c["slug"].nunique() < 20:
        return None
    dec = first_tick(c, by)
    led = simulate(dec, latency=2)
    if len(led) < 25:
        return None
    full_ev = float(led["pnl"].mean())
    dev = led[led["split"] == "dev"]
    dev_ev = float(dev["pnl"].mean()) if len(dev) else -9
    row = dict(family=fam, params=p, n=int(len(led)), wr=round(float(led["won"].mean()*100), 1),
               full_ev=round(full_ev, 3), dev_ev=round(dev_ev, 3))
    if not (full_ev > 0 and dev_ev > 0):
        row["screened"] = False
        return row
    row["screened"] = True
    ev = evaluate(led, n_trials=400)
    lat = latency_survival(dec, latencies=(2, 3, 5, 10))
    ps = ev["per_split"]
    row.update(full_ci=ps["FULL"]["lo"] if ps.get("FULL") else None,
               holdout=ps.get("holdout"), future=ps.get("future"),
               cpcv=ev["cpcv"].get("pct_pos"),
               lat5=(lat.get(5) or {}).get("ev"), lat10=(lat.get(10) or {}).get("ev"),
               lat5_lo=(lat.get(5) or {}).get("lo"))
    return row


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    b = prep(load_base())
    specs = gen()
    print(f"{len(specs)} new-signal specs")
    results = []
    with open(OUT, "w") as f:
        for fam, p in specs:
            r = run_spec(b, fam, p)
            if r:
                results.append(r)
                f.write(json.dumps(r) + "\n")
    scr = [r for r in results if r.get("screened")]
    # survivors: FULL CI lo>0, cpcv>=80, lat5>0 & lat10>0, future ev>0
    surv = [r for r in scr if r.get("full_ci") and r["full_ci"] > 0
            and (r.get("cpcv") or 0) >= 80 and (r.get("lat5") or -9) > 0
            and (r.get("lat10") or -9) > 0 and (r.get("future") or {}).get("ev", -9) > 0]
    print(f"screened-in {len(scr)} / {len(results)}; passing all gates: {len(surv)}\n")
    surv.sort(key=lambda r: -(r.get("lat10") or 0))
    print(f"{'family':10}{'params':52}{'n':>4}{'full_ev':>8}{'fullLo':>7}{'cpcv':>5}{'lat5':>6}{'lat10':>6}{'futEV':>7}")
    for r in surv[:25]:
        fu = (r.get("future") or {})
        print(f"{r['family']:10}{json.dumps(r['params'])[:50]:52}{r['n']:>4}{r['full_ev']:>8.2f}"
              f"{r['full_ci']:>7.2f}{(r.get('cpcv') or 0):>5.0f}{(r.get('lat5') or 0):>6.2f}"
              f"{(r.get('lat10') or 0):>6.2f}{fu.get('ev',0):>7.2f}")
    if not surv:
        print("NO new-signal survivors passed all gates.")
        # show best screened-in by future for context
        scr.sort(key=lambda r: -((r.get('future') or {}).get('ev', -9)))
        print("\nbest screened-in by future EV (context, NOT passing gates):")
        for r in scr[:8]:
            fu = r.get('future') or {}
            print(f"  {r['family']:10}{json.dumps(r['params'])[:48]:50} n={r['n']:>4} "
                  f"full {r['full_ev']:+.2f} future {fu.get('ev','-')} lat10 {r.get('lat10','-')}")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
