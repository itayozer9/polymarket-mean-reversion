"""V3b — EDGE ATLAS v3 (Edge Hunt v3, pre-registered 2026-07-24, test_ledger.md).

Persistence + new-cell scan on the never-mined window 2026-07-03..2026-07-23:
  dev      = 2026-07-03..07-12   (selection)
  holdout  = 2026-07-13..07-16   (selection)
  future   = 2026-07-17..07-23   (SEALED — revealed ONCE for dev+holdout candidates)
All days causally clean (post 06-13 strike fix) — no sec>=35 filter needed. Official
labels, live-2 slip, 4 original coins (new coins excluded: partial window, own gate).
Prior v2 artifact backed up to edge_atlas_v2/ once.

Run:  uv run python -m research.analysis.atlas_v3
"""
from __future__ import annotations
import os
import shutil

import numpy as np

import research.analysis.edge_atlas as ea

_orig_frame = ea.atlas_tick_frame


def v3_frame():
    t = _orig_frame()
    d = t["date"].astype(str)
    t = t[(d >= "2026-07-03") & (d <= "2026-07-23")].copy()
    ds = t["date"].astype(str)
    t["split"] = np.where(ds <= "2026-07-12", "dev",
                          np.where(ds <= "2026-07-16", "holdout", "future"))
    print(f"[atlas_v3] frame: {len(t):,} ticks | "
          f"{t.groupby('split')['slug'].nunique().to_dict()} windows/split")
    return t


def main() -> None:
    if os.path.isdir(ea.OUT_DIR) and not os.path.isdir(ea.OUT_DIR + "_v2"):
        shutil.copytree(ea.OUT_DIR, ea.OUT_DIR + "_v2")
        print(f"[atlas_v3] backed up v2 artifact -> {ea.OUT_DIR}_v2")
    ea.atlas_tick_frame = v3_frame
    ea.build()
    ea.reveal_future(force=True)   # v2 artifact's revealed flag; this is v3's ONE look
    ea.print_tables()


if __name__ == "__main__":
    main()
