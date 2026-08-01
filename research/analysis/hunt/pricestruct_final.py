"""hunt/pricestruct_final — lock in the DOWN-favourite survivor + the basis gate.

Stage-2 mechanism block showed the DOWN-favourite edge is concentrated where the
Chainlink-Coinbase basis is NOT strongly negative:
    CL>CB(basis>2)   +0.51   CL~CB(|b|<=2) +0.44   CL<CB(basis<-2) -0.29
So a `cl_cb_basis_bps >= -2` decision-time gate should sharpen it. This script
confirms the headline + the basis-gated variant with the FULL latency sweep and a
per-split readout, on the cleanest band (0.88-0.93, decide @ 7-8min left).

Run:  uv run python -m research.analysis.hunt.pricestruct_final
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L


def _down_fav(b):
    ya = b["yes_best_ask"].to_numpy("f8"); na = b["no_best_ask"].to_numpy("f8")
    return (b["book_healthy"] == True).to_numpy() & (na > ya)  # NO is favourite


def _run(b, name, mask, lo, hi):
    fav_ask = np.maximum(b["yes_best_ask"].to_numpy("f8"), b["no_best_ask"].to_numpy("f8"))
    cand = b[np.asarray(mask) & (fav_ask > lo) & (fav_ask <= hi)
             & (b["time_left_sec"] >= 420) & (b["time_left_sec"] <= 480)]
    if len(cand) == 0:
        print(f"{name}: n=0"); return
    dec = L.first_tick(cand, np.zeros(len(cand), bool))  # buy NO
    led = L.simulate(dec, latency=2)
    e = L.evaluate(led)
    lat = L.latency_survival(dec)
    ps = e["per_split"]
    print(f"\n### {name}")
    print(f"  n={e['n']} FULL ${e['ev']:+.3f} WR{e['wr']:.0f}% total ${e['total']:+.0f}")
    for sp in ("dev", "holdout", "future", "FULL"):
        s = ps.get(sp)
        if s:
            print(f"    {sp:8} n={s['n']:>4} ev ${s['ev']:+.3f} [{s['lo']:+.3f},{s['hi']:+.3f}] WR{s['wr']:.0f}%")
    print(f"  latency EV: " + "  ".join(
        f"{k}s ${v['ev']:+.3f}(fut {v['fut_ev']:+.3f})" for k, v in lat.items() if v.get("ev") is not None))
    print(f"  CPCV pos {e['cpcv'].get('pct_pos')}% (mean ${e['cpcv'].get('ev_mean')}, p5 ${e['cpcv'].get('p5')}) "
          f"| DSR {e['dsr']['dsr']} sr {round(e['dsr']['sr'],3)}")


def main():
    b = L.load_base()
    down = _down_fav(b)
    basis = b["cl_cb_basis_bps"].to_numpy("f8")
    gate = down & (basis >= -2)              # the mechanism gate

    print("="*78)
    print("PRICESTRUCT survivor: BUY the DEEP DOWN-FAVOURITE at 7-8 min left.")
    print("  filter: book_healthy & NO is favourite (no_ask>yes_ask) & fav_ask band")
    print("          & first tick with time_left in [420,480]s ; buy NO ; hold to CL.")
    print("="*78)

    _run(b, "HEADLINE  DOWNfav 0.88-0.93  (no basis gate)", down, 0.88, 0.93)
    _run(b, "GATED     DOWNfav 0.88-0.93  & cl_cb_basis>=-2", gate, 0.88, 0.93)
    _run(b, "WIDE      DOWNfav 0.86-0.95  & cl_cb_basis>=-2", gate, 0.86, 0.95)
    _run(b, "WIDE-raw  DOWNfav 0.86-0.95  (no gate)", down, 0.86, 0.95)


if __name__ == "__main__":
    main()
