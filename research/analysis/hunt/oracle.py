"""HUNT: oracle-divergence family (Tier A).

chainlink_price is the SETTLEMENT oracle (Polymarket pays it); cb_spot / the book
track Coinbase. The collector has now been upgraded so `chainlink_price` and
`cl_cb_basis_bps` are REAL (99.78% populated, not the dead all-zero column the old
e3_oracle_divergence.py found).

Core mechanism under test: when Chainlink has committed DECISIVELY to one side of
start_price (|chainlink/start-1| large in bps) while the book — which tracks the
laggier Coinbase tape — still prices that side cheaply, BUY the chainlink-implied
side and hold to Chainlink resolution. Decide with a >=60s buffer (Tier A), so it
must survive a 2->10s latency sweep.

We test three sub-families and MANY variants in each:
  (A) committed-chainlink + cheap book on that side  (the headline)
  (B) basis cl_cb_basis_bps as a TILT/FILTER on a plain favourite trade
  (C) does a large |basis| predict the settled direction, or mean-revert?

CRITICAL honesty check (from the family brief): cl_dist correlates ~0.96 with the
Coinbase-implied dist, so naive "buy chainlink side" is ~the Coinbase determinism
trade. We therefore (i) report a Coinbase-determinism CONTROL with the identical
gates, and (ii) test the RESIDUAL divergence ("chainlink leads Coinbase":
|cl_dist| - |cb_dist| > 0) to see if the chainlink oracle adds anything beyond
what Coinbase already tells you. Naive disagree-near-strike is known dead (-3.5/tr)
and is AVOIDED — every variant requires chainlink CLEARLY committed.

Run: uv run python -m research.analysis.hunt.oracle
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L


def _prep(b: pd.DataFrame) -> pd.DataFrame:
    b = b.copy()
    # chainlink-implied strike distance (the SETTLEMENT-relevant one), in bps
    b["cl_dist"] = (b["chainlink_price"] / b["start_price"] - 1.0) * 1e4
    b["cb_dist"] = b["dist_strike_bps"]                 # Coinbase-implied (book-tracking)
    b["cl_up_now"] = b["cl_dist"] > 0                   # chainlink side of strike RIGHT NOW
    b["cb_up_now"] = b["cb_dist"] > 0
    b["same_side"] = b["cl_up_now"] == b["cb_up_now"]
    # how much MORE extreme is chainlink than coinbase on the same side
    b["cl_lead_bps"] = b["cl_dist"].abs() - b["cb_dist"].abs()
    # ask the taker pays to buy the chainlink-implied side
    b["cl_side_ask"] = np.where(b["cl_up_now"], b["yes_best_ask"], b["no_best_ask"])
    # ask to buy the COINBASE-implied side (for the determinism control)
    b["cb_side_ask"] = np.where(b["cb_up_now"], b["yes_best_ask"], b["no_best_ask"])
    return b


def _emit(name, cand, buy_yes, store=None):
    """Run cand+buy_yes through the harness and print the verdict line.
    Returns (name, future_lo, dec) for ranking. Stores fut_lo in `store`."""
    if cand.empty:
        print(f"{name:42} n=0 (no qualifying ticks)")
        return name, -99.0, None
    dec = L.first_tick(cand, np.asarray(buy_yes))
    led = L.simulate(dec, latency=2)
    e = L.evaluate(led)
    if e["n"] == 0:
        print(f"{name:42} n=0 (no fills after depth gate)")
        return name, -99.0, dec
    lat = L.latency_survival(dec)
    fu = e["per_split"].get("future")
    fl = e["per_split"].get("FULL")
    if fl is None:
        print(f"{name:42} n={e['n']:>4} (FULL split <3 rows — skip)")
        return name, -99.0, dec
    futs = (f"${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}"
            if fu else "n/a")
    lats = " ".join(
        f"{k}s${v['ev']:+.2f}" if v.get("ev") is not None else f"{k}s-"
        for k, v in lat.items())
    fut_fl = {k: (v.get("fut_ev") if v else None) for k, v in lat.items()}
    fut_lat = " ".join(
        f"{k}s${fut_fl[k]:+.2f}" if fut_fl.get(k) is not None else f"{k}s-"
        for k in lat)
    print(f"{name:42} n={e['n']:>4} FULL${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}]"
          f"WR{fl['wr']:.0f}% fut {futs} CPCV{e['cpcv'].get('pct_pos','?'):.0f}% "
          f"DSR{e['dsr']['dsr']}")
    print(f"{'':42} latFULL {lats} | latFUT {fut_lat}")
    fu_lo = fu["lo"] if fu else -99.0
    if store is not None:
        store[name] = dict(fut_lo=fu_lo, e=e, lat=lat, dec=dec)
    return name, fu_lo, dec


def main():
    b = _prep(L.load_base())
    store: dict = {}

    # ------------------------------------------------------------------
    # FAMILY A — committed chainlink + cheap book on that side (Tier A).
    # Decide with >=60s buffer: time_left in [60,180]. Vary cl_dist commit
    # threshold and the ask band of the chainlink-implied side.
    # ------------------------------------------------------------------
    print("=" * 100)
    print("FAMILY A: committed chainlink, BUY chainlink-implied side, ask-band cheap "
          "(time_left 60-180s)")
    print("=" * 100)
    segA = b[(b["time_left_sec"] >= 60) & (b["time_left_sec"] <= 180)
             & (b["book_healthy"] == True)]
    for d_lo in (10, 15, 20, 30):
        for a_lo, a_hi in [(0.30, 0.70), (0.45, 0.70), (0.50, 0.80), (0.55, 0.85)]:
            c = segA[(segA["cl_dist"].abs() >= d_lo)
                     & (segA["cl_side_ask"] >= a_lo) & (segA["cl_side_ask"] < a_hi)]
            _emit(f"A cl>={d_lo} ask[{a_lo:.2f},{a_hi:.2f}]", c,
                  c["cl_up_now"].to_numpy(), store)

    # ------------------------------------------------------------------
    # FAMILY A2 — same but RESIDUAL: require chainlink to LEAD Coinbase
    # (|cl_dist|-|cb_dist| > lead) so the book is demonstrably underpricing the
    # chainlink move. This is the part that is NOT just Coinbase determinism.
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("FAMILY A2: committed chainlink AND chainlink LEADS coinbase "
          "(cl_lead>thr) -> residual-over-determinism")
    print("=" * 100)
    for d_lo, lead in [(10, 2), (10, 4), (15, 3), (20, 0), (20, 3), (10, 6)]:
        c = segA[(segA["cl_dist"].abs() >= d_lo) & (segA["cl_lead_bps"] >= lead)
                 & (segA["cl_side_ask"] >= 0.45) & (segA["cl_side_ask"] < 0.85)]
        _emit(f"A2 cl>={d_lo} lead>={lead} ask[.45,.85]", c,
              c["cl_up_now"].to_numpy(), store)

    # ------------------------------------------------------------------
    # CONTROL — identical gate but on the COINBASE-implied side / Coinbase dist.
    # If Family A ~= this control, the "oracle" edge is just determinism re-badged.
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("CONTROL (determinism): same gate but COINBASE side & coinbase dist")
    print("=" * 100)
    for d_lo in (10, 20):
        c = segA[(segA["cb_dist"].abs() >= d_lo)
                 & (segA["cb_side_ask"] >= 0.45) & (segA["cb_side_ask"] < 0.85)]
        _emit(f"CTRL cb>={d_lo} ask[.45,.85]", c, c["cb_up_now"].to_numpy(), store)

    # ------------------------------------------------------------------
    # FAMILY A3 — decide-at-window-open (>= 5 min buffer): the strictest Tier-A.
    # Only the very strongly-committed, very-cheap cases survive that far out.
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("FAMILY A3: EARLY decide (time_left 300-600s), strong commit, cheap side")
    print("=" * 100)
    segE = b[(b["time_left_sec"] >= 300) & (b["time_left_sec"] <= 600)
             & (b["book_healthy"] == True)]
    for d_lo in (20, 30, 40):
        for a_lo, a_hi in [(0.45, 0.75), (0.50, 0.80)]:
            c = segE[(segE["cl_dist"].abs() >= d_lo)
                     & (segE["cl_side_ask"] >= a_lo) & (segE["cl_side_ask"] < a_hi)]
            _emit(f"A3 cl>={d_lo} ask[{a_lo:.2f},{a_hi:.2f}]", c,
                  c["cl_up_now"].to_numpy(), store)

    # ------------------------------------------------------------------
    # FAMILY B — basis as a TILT on a plain favourite. Buy the book favourite
    # only when chainlink basis CONFIRMS the favourite's side (basis pushes
    # toward the favourite). time_left wide for Tier A.
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("FAMILY B: plain favourite, FILTERED by basis confirming the favourite side")
    print("=" * 100)
    segB = b[(b["time_left_sec"] >= 60) & (b["time_left_sec"] <= 240)
             & (b["book_healthy"] == True)]
    yes_fav = segB["yes_mid"] >= 0.5
    fav_ask = np.where(yes_fav, segB["yes_best_ask"], segB["no_best_ask"])
    segB = segB.assign(_yes_fav=yes_fav, _fav_ask=fav_ask)
    for basis_thr in (3, 6, 10):
        # basis confirms favourite: yes-favourite wants chainlink-rich (basis>0 means
        # chainlink ABOVE coinbase -> tilts UP/YES); no-favourite wants basis<0.
        conf = ((segB["_yes_fav"] & (segB["cl_cb_basis_bps"] >= basis_thr))
                | (~segB["_yes_fav"] & (segB["cl_cb_basis_bps"] <= -basis_thr)))
        c = segB[conf & (segB["_fav_ask"] >= 0.50) & (segB["_fav_ask"] < 0.90)]
        _emit(f"B fav & basis>={basis_thr} confirm", c, c["_yes_fav"].to_numpy(), store)
    # contra: basis OPPOSES favourite (should be worse — a falsification check)
    opp = ((segB["_yes_fav"] & (segB["cl_cb_basis_bps"] <= -6))
           | (~segB["_yes_fav"] & (segB["cl_cb_basis_bps"] >= 6)))
    c = segB[opp & (segB["_fav_ask"] >= 0.50) & (segB["_fav_ask"] < 0.90)]
    _emit("B fav & basis>=6 OPPOSE (falsify)", c, c["_yes_fav"].to_numpy(), store)

    # ------------------------------------------------------------------
    # FAMILY C — does a large |basis| predict the settled direction directly?
    # Buy the basis-implied side (basis>0 -> UP) regardless of book favourite,
    # in the last 60s where basis is freshest. (and the mean-revert variant.)
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("FAMILY C: buy the BASIS-implied side directly (basis>0 => UP/YES), last 60-120s")
    print("=" * 100)
    segC = b[(b["time_left_sec"] >= 30) & (b["time_left_sec"] <= 120)
             & (b["book_healthy"] == True)]
    for basis_thr in (6, 10, 20, 40):
        c = segC[segC["cl_cb_basis_bps"].abs() >= basis_thr]
        # follow basis
        _emit(f"C follow basis>={basis_thr}", c,
              (c["cl_cb_basis_bps"] > 0).to_numpy(), store)
    # mean-revert the basis (fade it) at the biggest divergence
    c = segC[segC["cl_cb_basis_bps"].abs() >= 20]
    _emit("C FADE basis>=20 (revert)", c, (c["cl_cb_basis_bps"] < 0).to_numpy(), store)

    # ------------------------------------------------------------------
    # RANK and deep-dive the winner by FRESH-OOS (future) CI lower bound.
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    ranked = sorted(store.items(), key=lambda kv: kv[1]["fut_lo"], reverse=True)
    print("TOP 8 by FUTURE (fresh-OOS) CI lower bound:")
    for nm, d in ranked[:8]:
        e = d["e"]
        fu = e["per_split"].get("future")
        print(f"  {nm:42} fut_lo={d['fut_lo']:+.3f}  "
              f"fut_ev={fu['ev'] if fu else None}  n={e['n']}  "
              f"DSR={e['dsr']['dsr']}  CPCV={e['cpcv'].get('pct_pos')}%")

    if ranked and ranked[0][1]["fut_lo"] > -90:
        nm, d = ranked[0]
        print("\n" + "=" * 100)
        print(f"DEEP DIVE WINNER: {nm}")
        print("=" * 100)
        e = d["e"]; lat = d["lat"]
        import json
        print("per_split:", json.dumps(e["per_split"], indent=0))
        print("cpcv:", e["cpcv"])
        print("dsr:", e["dsr"])
        print("latency:", {k: {kk: vv for kk, vv in v.items() if kk in ('n','ev','lo','hi','fut_ev')} for k, v in lat.items()})
        led = L.simulate(d["dec"], latency=2)
        # jackpot check: is FULL total driven by 1-2 windows?
        top = led.nlargest(3, "pnl")[["slug", "split", "pnl"]]
        print("top-3 pnl windows:", top.to_dict("records"))
        print("FULL total:", round(led["pnl"].sum(), 1), "ev*n:",
              round(led["pnl"].mean() * len(led), 1))


if __name__ == "__main__":
    main()
