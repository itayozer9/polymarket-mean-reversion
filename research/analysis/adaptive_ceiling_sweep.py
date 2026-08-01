"""Comprehensive sweep: should the max_ask ceiling be a CONTINUOUS function of the Chainlink
lock depth (cl_dist), not a binary 0.78/0.85 step?

Step 1 (diagnostic): the raw EV surface over (fav_ask bucket x |cl_dist| bucket) — this shows the
TRUE safe-ask boundary. If EV rises smoothly with cl_dist for the expensive asks, a graduated/ramp
ceiling is justified; if it's flat/noisy, the simple step (or flat) is right and a ramp overfits.

Step 2 (sweep): flat / binary-step / graduated-multistep / linear-ramp ceilings over a WIDE range
(lo 0.72-0.78, hi 0.82-0.90), Chainlink-settled through edge_lab, ranked by future-split EV + CI
lower bound + CPCV + DSR + total + volume. Discipline: prefer the SIMPLEST shape that captures the
EV; only adopt extra params if they beat the step on robustness-adjusted terms (short 2.5wk sample).

Run: uv run python -m research.analysis.adaptive_ceiling_sweep
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.analysis import edge_lab
from research.analysis.dynamic_max_ask import _prep, _ledger, T_MIN, T_MAX, DIST_MIN, ASK_LO, ADVEL

WIDE_HI = 0.90  # widest ask we ever consider


# ---- Step 1: EV surface (ask x cl_dist) ------------------------------------
def ev_surface(b: pd.DataFrame):
    cand = ((b["time_left_sec"] >= T_MIN) & (b["time_left_sec"] <= T_MAX)
            & (b["abs_dist_bps"] >= DIST_MIN) & (b["consistent"])
            & (b["fav_ask"].between(ASK_LO, WIDE_HI)) & (b["adverse_vel_10s"] <= ADVEL)
            & (b["cl_ok"]) & (b["oracle_agree"]))
    c = b[cand]
    dd = edge_lab.first_tick(c, (c["yes_mid"] >= 0.5).to_numpy())
    led = edge_lab.simulate(dd, latency=2)
    # attach the entry tick's cl_dist (by slug+entry_sec)
    feat = b[["slug", "seconds_into_window", "cl_dist_bps"]].drop_duplicates(["slug", "seconds_into_window"])
    led = led.merge(feat, left_on=["slug", "entry_sec"], right_on=["slug", "seconds_into_window"], how="left")
    led["acl"] = led["cl_dist_bps"].abs()
    abins = [0.50, 0.65, 0.72, 0.78, 0.82, 0.86, 0.90]
    cbins = [0, 8, 16, 24, 40, 1e9]
    led["ab"] = pd.cut(led["entry_ask"], abins)
    led["cb"] = pd.cut(led["acl"], cbins)
    print("=== EV/tr surface: rows=fav_ask, cols=|cl_dist|bps  (n in parens; '.'=n<8) ===")
    piv_ev = led.groupby(["ab", "cb"], observed=True)["pnl"].mean().unstack()
    piv_n = led.groupby(["ab", "cb"], observed=True)["pnl"].size().unstack()
    cols = [c for c in piv_ev.columns]
    hdr = "ask\\cl"
    print(f"  {hdr:>12} " + " ".join(f"{str(c):>12}" for c in cols))
    for ab in piv_ev.index:
        cells = []
        for cb in cols:
            ev = piv_ev.loc[ab, cb]; n = piv_n.loc[ab, cb]
            cells.append("        .   " if (pd.isna(ev) or n < 8) else f"{ev:+5.2f}({int(n):>3})")
        print(f"  {str(ab):>12} " + " ".join(f"{c:>12}" for c in cells))
    print("  -> read each ROW: at what |cl_dist| does that ask band turn solidly +EV?\n")


# ---- Step 2: ceiling functions ---------------------------------------------
def f_flat(cld, v): return np.full(len(cld), v)
def f_step(cld, edges):  # edges = [(thresh, ceiling), ...] ascending; ceiling for |cld|>=thresh
    out = np.full(len(cld), edges[0][1])
    for th, c in edges[1:]:
        out = np.where(cld >= th, c, out)
    return out
def f_ramp(cld, lo, hi, k0, k1):  # linear lo@k0 -> hi@k1, clipped
    return np.clip(lo + (hi - lo) * (cld - k0) / (k1 - k0), min(lo, hi), max(lo, hi))


def _report(name, b, ceil, full=False):
    led = _ledger(b, ceil, None)
    if led is None or len(led) == 0:
        print(f"  {name:30} n=0"); return None
    if full:
        e = edge_lab.evaluate(led); ps = e["per_split"]; fl = ps["FULL"]; fu = ps.get("future")
        lat = edge_lab.latency_survival(led_to_dec(led))
        print(f"  {name:30} n={e['n']:>3} FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}] "
              f"tot${fl['total']:+.0f} | fut ${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']} "
              f"| CPCV {e['cpcv'].get('pct_pos','?'):.0f}% DSR {e['dsr']['dsr']}")
        return e
    ci = edge_lab._split_ci(led); fl, fu = ci["FULL"], ci.get("future")
    print(f"  {name:30} n={fl['n']:>3} WR{fl['wr']:.0f}% EV${fl['ev']:+.2f} tot${fl['total']:+.0f} "
          f"| fut ${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}" if fu else "")
    return ci


def led_to_dec(led):  # rebuild a decision frame for latency_survival
    return led[["slug", "symbol", "date", "split", "window_start_ts", "entry_sec", "buy_yes"]].copy()


def run():
    b = _prep(edge_lab.load_base())
    cld = b["cl_dist_bps"].abs().to_numpy("f8")
    ev_surface(b)

    print("=== flat baselines ===")
    for v in (0.75, 0.78, 0.80, 0.82, 0.85, 0.88):
        _report(f"flat {v}", b, f_flat(cld, v))

    print("\n=== binary step (current family) ===")
    for lo, hi, k in [(0.78,0.85,20),(0.75,0.85,20),(0.75,0.88,20),(0.78,0.88,24)]:
        _report(f"step {lo}->{hi}@{k}", b, f_step(cld, [(0,lo),(k,hi)]))

    print("\n=== graduated multi-step ===")
    grads = {
        "grad 75/80/85 @8/20":      [(0,0.75),(8,0.80),(20,0.85)],
        "grad 75/80/85/88 @8/16/28":[(0,0.75),(8,0.80),(16,0.85),(28,0.88)],
        "grad 78/82/86 @12/24":     [(0,0.78),(12,0.82),(24,0.86)],
        "grad 75/82/88 @10/24":     [(0,0.75),(10,0.82),(24,0.88)],
    }
    for nm, edges in grads.items():
        _report(nm, b, f_step(cld, edges))

    print("\n=== continuous linear ramp lo@k0 -> hi@k1 ===")
    ramps = [(0.75,0.88,8,28),(0.75,0.85,8,24),(0.78,0.88,12,28),
             (0.75,0.90,8,32),(0.72,0.88,6,28),(0.78,0.90,12,32)]
    for lo,hi,k0,k1 in ramps:
        _report(f"ramp {lo}->{hi} [{k0},{k1}]", b, f_ramp(cld, lo, hi, k0, k1))

    print("\n=== FULL verdict (CPCV/DSR/latency) on finalists ===")
    finals = {
        "flat 0.78 (incumbent base)": f_flat(cld, 0.78),
        "step 0.78->0.85@20 (LIVE now)": f_step(cld, [(0,0.78),(20,0.85)]),
        "grad 75/80/85/88 @8/16/28": f_step(cld, [(0,0.75),(8,0.80),(16,0.85),(28,0.88)]),
        "ramp 0.75->0.88 [8,28]": f_ramp(cld, 0.75, 0.88, 8, 28),
        "ramp 0.75->0.90 [8,32]": f_ramp(cld, 0.75, 0.90, 8, 32),
    }
    for nm, ceil in finals.items():
        _report(nm, b, ceil, full=True)


if __name__ == "__main__":
    run()
