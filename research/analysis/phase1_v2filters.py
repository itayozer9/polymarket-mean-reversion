"""Phase 1 (H1/H2) — do the v2 loss-avoidance filters hold on the FULL window,
especially the FRESH OOS days (06-01..04) that postdate their discovery?

v2 filters (from loss_pattern_filters.md), applied as subsets of the parity
full-window ledgers:
  - det_lwd_v2: fav_ask <= 0.88  AND  adverse_vel_10s <= 2 bps
    (strike_crossings>=1 is NOT in the base ledger -> reported as 'partial v2')
  - det_sqp_v2: abs_mis >= 0.12  AND  dist_bps <= 19

Compares v1 (no filter) vs v2 per split, with window-clustered CIs. The decision
rule (test_ledger H1/H2): adopt only if EV/trade lifts on the FRESH OOS split AND
the OOS CI lower bound stays > 0.

Run: uv run python -m research.analysis.phase1_v2filters
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.lib.stats import window_clustered_bootstrap

LED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..",
                   "data", "research", "ledgers")
LED = os.path.normpath(LED)
SPLITS = ("dev", "holdout", "future", "FULL")


def _row(led, label):
    if not len(led):
        return f"   {label:>22}: (no trades)"
    lo, _, hi = window_clustered_bootstrap(led["pnl"].values, led["slug"].values, n=3000)
    return (f"   {label:>22}: n={len(led):>4} WR={led['won'].mean()*100:5.1f}% "
            f"${led['pnl'].mean():+.3f}/tr CI[{lo:+.2f},{hi:+.2f}] tot=${led['pnl'].sum():+8.1f}")


def compare(name, v1, v2mask, v2desc):
    print(f"\n=== {name}: v1 vs v2 ({v2desc}) ===")
    for sp in SPLITS:
        seg = v1 if sp == "FULL" else v1[v1["split"] == sp]
        seg2 = seg[v2mask.reindex(seg.index, fill_value=False)]
        if not len(seg):
            continue
        print(f"  -- {sp} --")
        print(_row(seg, "v1 (all)"))
        print(_row(seg2, "v2 (filtered)"))
        if len(seg) and len(seg2):
            lift = seg2["pnl"].mean() - seg["pnl"].mean()
            kept = len(seg2) / len(seg) * 100
            print(f"   {'lift':>22}: ${lift:+.3f}/tr   keeps {kept:.0f}%")


def run():
    det = pd.read_parquet(os.path.join(LED, "det_full.parquet"))
    sq = pd.read_parquet(os.path.join(LED, "sq_full.parquet"))

    det_v2 = (det["fav_ask"] <= 0.88) & (det["adverse_vel_10s"] <= 2.0)
    compare("determinism", det, det_v2, "ask<=0.88 & adverse_vel<=2  [partial: no crossings]")

    sq_v2 = (sq["abs_mis"] >= 0.12) & (sq["dist_bps"] <= 19.0)
    compare("stale_quote", sq, sq_v2, "abs_mis>=0.12 & dist<=19bps")


if __name__ == "__main__":
    run()
