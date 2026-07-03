"""Honest-era EDGE ATLAS run (T3, Edge Hunt v2) — registered splits, official labels.

Wraps edge_atlas with the campaign's era discipline (test_ledger "HONEST EDGE HUNT v2"):
  dev      = 2026-05-23..05-31   (pre-degradation)
  holdout  = 2026-06-01..06-04 + 06-13..06-18  (clean, already revealed on 06-18/19)
  DROPPED  = 06-05..06-12 (stale-book degraded epoch, whole days)
  future   = >= 2026-06-19 (virgin — revealed ONCE here)
plus the pre-06-13 causal filter (drop the first 35s of each window: the 5m/15m strike
back-fill look-ahead) — the atlas takes the FIRST tick per (window, cell), which without
this filter could be an acausal early tick.

Labels: official (edge_lab.cl_outcomes, already switched). Slip: live-2. Build and reveal
run in ONE process so the patched frame is identical for both (reveal asserts n_obs).
The pre-v2 artifact (built 06-10, revealed) is backed up to edge_atlas_pre_v2/ once.

Run:  uv run python -m research.analysis.atlas_honest
"""
from __future__ import annotations
import os
import shutil

import numpy as np

import research.analysis.edge_atlas as ea

_orig_frame = ea.atlas_tick_frame


def honest_frame():
    t = _orig_frame()
    d = t["date"].astype(str)
    keep = (d < "2026-06-05") | (d >= "2026-06-13")
    t = t[keep]
    t = t[~((t["date"].astype(str) < "2026-06-13") & (t["seconds_into_window"] < 35))]
    ds = t["date"].astype(str)
    t = t.copy()
    t["split"] = np.where(ds <= "2026-05-31", "dev",
                          np.where(ds <= "2026-06-18", "holdout", "future"))
    print(f"[atlas_honest] frame: {len(t):,} ticks | "
          f"{t.groupby('split')['slug'].nunique().to_dict()} windows/split")
    return t


def main() -> None:
    if os.path.isdir(ea.OUT_DIR) and not os.path.isdir(ea.OUT_DIR + "_pre_v2"):
        shutil.copytree(ea.OUT_DIR, ea.OUT_DIR + "_pre_v2")
        print(f"[atlas_honest] backed up prior artifact -> {ea.OUT_DIR}_pre_v2")
    ea.atlas_tick_frame = honest_frame
    ea.build()
    ea.reveal_future(force=True)   # prior artifact's revealed flag; this is v2's ONE look
    ea.print_tables()


if __name__ == "__main__":
    main()
