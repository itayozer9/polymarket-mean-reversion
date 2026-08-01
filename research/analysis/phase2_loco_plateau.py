"""Phase 2 — leave-one-coin-out (cross-sectional generalization) + parameter
plateau (is the edge on a stable plateau, not a knife-edge spike?).

LOCO: per-coin EV/WR/CI for both edges (full + fresh-OOS). If the edge is
structural it should be positive on EACH coin, not carried by one (e.g. BTC-only).

PLATEAU (determinism): EV across the (dist_min, max_ask) grid by SUBSETTING the
parity ledger (which is the loosest primary rule). A robust edge is a smooth
plateau; a spike that collapses off the exact params is overfit. (Approximate —
subsetting can only tighten; the rigorous per-config sweep is in generalize.det_pbo,
PBO=0.107.)

Run: uv run python -m research.analysis.phase2_loco_plateau
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.lib.stats import window_clustered_bootstrap

LED = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "data", "research", "ledgers"))


def _ci(s):
    if len(s) < 5:
        return (np.nan, np.nan)
    lo, _, hi = window_clustered_bootstrap(s["pnl"].values, s["slug"].values, n=3000)
    return lo, hi


def loco(name, led):
    print(f"\n=== {name}: leave-one-coin-out (per-coin EV; structural edge => all positive) ===")
    print(f"{'coin':>6} {'n':>5} {'WR':>6} {'$/tr':>7} {'90% CI':>18} {'future $/tr':>12}")
    for c in ("btc", "eth", "sol", "xrp"):
        s = led[led["symbol"] == c]
        if not len(s):
            continue
        lo, hi = _ci(s)
        sf = s[s["split"] == "future"]
        fev = sf["pnl"].mean() if len(sf) else float("nan")
        flag = "" if np.isnan(lo) else (" +" if lo > 0 else " ~0")
        print(f"{c:>6} {len(s):>5} {s['won'].mean()*100:>5.1f}% ${s['pnl'].mean():>+6.3f} "
              f"[{lo:>+6.3f},{hi:>+6.3f}]{flag} ${fev:>+10.3f}")


def plateau_det(det):
    print("\n=== determinism: parameter-plateau sketch (subset the parity ledger) ===")
    print("   EV/trade across (dist_min_bps x max_ask); smooth plateau = robust")
    print(f"   {'dist>=':>7} | " + " ".join(f"ask<={a:>4}" for a in (0.85, 0.88, 0.90)))
    for dmin in (5, 8, 12, 20, 30):
        cells = []
        for amax in (0.85, 0.88, 0.90):
            s = det[(det["dist_bps"] >= dmin) & (det["fav_ask"] <= amax)]
            cells.append(f"${s['pnl'].mean():>+5.2f}({len(s):>3})" if len(s) >= 10 else "   --   ")
        print(f"   {dmin:>7} | " + " ".join(cells))
    print("   (cell = $/tr (n);  primary live rule = dist>=5, ask<=0.90)")


def run():
    det = pd.read_parquet(os.path.join(LED, "det_full.parquet"))
    sq = pd.read_parquet(os.path.join(LED, "sq_full.parquet"))
    loco("determinism", det)
    loco("stale_quote", sq)
    plateau_det(det)


if __name__ == "__main__":
    run()
