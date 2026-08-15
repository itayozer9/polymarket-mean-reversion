"""V4a — EDGE ATLAS v4 (Edge Hunt v4, pre-registered 2026-08-01, sealed; reveal 2026-08-15).

Persistence + new-cell scan on the never-mined window 2026-07-24..2026-08-14 (v3 mined
through 07-23 EOD; the dated R1/R2/R4 and rung gates read narrow registered slices only
and are not sweeps).

SPLIT MAPPING (prereg -> instrument). The prereg names two blocks:
  selection block  = 07-24..08-06   ("dev", selection ONLY)
  SEALED holdout   = 08-07..08-14   (revealed ONCE)
The V3b instrument selects on dev+holdout (`cand_*` needs dev CI-lo>0 AND the holdout
sign to agree AND BH) and reveals `future` once. So the prereg's selection block is
carried by the instrument's dev+holdout pair and the prereg's sealed holdout is carried
by `future`. Same data in selection, same data sealed, and "same cell grid and method as
V3b" is preserved literally. The internal 10d/4d cut mirrors v3's own 10d/4d.

  dev     = 2026-07-24..08-02  (10d, selection)
  holdout = 2026-08-03..08-06  ( 4d, selection)
  future  = 2026-08-07..08-14  ( 8d, SEALED — this is the prereg's one look)

All 7 coins (v3 excluded bnb/doge/hype for partial windows; 07-24+ collection is
complete). Official labels, live-2 slip. All days causally clean (post 06-13 strike fix).

Run:  PYTHONPATH=. uv run python -m research.analysis.atlas_v4
"""
from __future__ import annotations
import os
import shutil

import numpy as np

import research.analysis.edge_atlas as ea
import research.analysis.edge_lab as el
import research.dataset.official_outcomes as oo

_orig_frame = ea.atlas_tick_frame

DEV_END = "2026-08-02"
HOLD_END = "2026-08-06"
WIN_LO, WIN_HI = "2026-07-24", "2026-08-14"

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
V4_JOINED = os.path.join(REPO, "data", "research", "v4_frame", "joined_15m.parquet")
V4_SLIM = os.path.join(REPO, "data", "research", "v4_frame", "joined_15m_slim.parquet")


def build_v4_slim() -> None:
    """Write the column-subset frame for the v4 window (run as its own process)."""
    import pandas as pd
    from research.analysis.loss_patterns import _base
    if os.path.exists(V4_SLIM):
        print(f"[atlas_v4] slim exists -> {V4_SLIM}")
        return
    b = _base(pd.read_parquet(V4_JOINED))
    cols = [c for c in el.SLIM_COLS if c in b.columns]
    b[cols].to_parquet(V4_SLIM, index=False)
    print(f"[atlas_v4] slim -> {V4_SLIM} "
          f"({len(b):,} rows, {os.path.getsize(V4_SLIM)/1e6:.0f} MB, {len(cols)} cols)")


def use_v4_frame() -> None:
    """Point edge_lab's canonical loader at the v4 scratch frame.

    The canonical joined_15m.parquet was last built 2026-07-24 10:06 and holds only the
    first ~10h of the campaign window plus 4 coins, so reading it would silently score a
    truncated window. `load_base` resolves JOINED/SLIM by module-global lookup, so
    rebinding them here (and clearing the lru_cache) redirects it without touching the
    canonical artifact. SLIM is pointed at a non-existent path on purpose: it forces
    load_base to derive _base() from the v4 JOINED instead of reusing the stale slim.
    """
    if not os.path.exists(V4_JOINED):
        raise SystemExit(f"v4 frame missing: {V4_JOINED}\n"
                         f"run: PYTHONPATH=. nice -n 19 uv run python "
                         f"-m research.dataset.build_v4_window")
    el.JOINED = V4_JOINED
    # Prefer the slim frame when it exists: load_base() otherwise materializes ALL ~80
    # columns x 13.2M rows before subsetting, which is the memory peak. build_v4_slim()
    # writes it once in its own process so that peak never overlaps the atlas run.
    el.SLIM = V4_SLIM if os.path.exists(V4_SLIM) else V4_JOINED + ".__no_slim__"
    el.load_base.cache_clear()
    el.cl_outcomes.cache_clear()

    # Labels: official-ONLY, per the prereg ("official on-chain outcomes only, pending
    # excluded, never imputed"). edge_lab.cl_outcomes uses official_outcome_by_slug,
    # which left-merges official onto the RECONSTRUCTED frame — so (a) its slug universe
    # is capped by the stale canonical parquet (23,663 slugs ending 07-24: it surfaced
    # only 96 of this window's 14,690 windows) and (b) it FALLS BACK to reconstructed
    # labels, which the prereg forbids. official_only_by_slug reads the raw cache
    # (14,774 in-window 15m slugs) and drops unresolved rows instead of imputing them.
    def _official_only():
        o = oo.official_only_by_slug()
        return o.rename(columns={"official_up": "cl_up"})

    el.cl_outcomes = _official_only
    ea.cl_outcomes = _official_only          # edge_atlas imports it into its own frame fn


def v4_frame():
    t = _orig_frame()
    d = t["date"].astype(str)
    t = t[(d >= WIN_LO) & (d <= WIN_HI)].copy()
    ds = t["date"].astype(str)
    t["split"] = np.where(ds <= DEV_END, "dev",
                          np.where(ds <= HOLD_END, "holdout", "future"))
    print(f"[atlas_v4] frame: {len(t):,} ticks | "
          f"{t.groupby('split')['slug'].nunique().to_dict()} windows/split")
    print(f"[atlas_v4] coins: {sorted(t['symbol'].unique())}")
    return t


def main() -> None:
    use_v4_frame()
    if os.path.isdir(ea.OUT_DIR) and not os.path.isdir(ea.OUT_DIR + "_v3"):
        shutil.copytree(ea.OUT_DIR, ea.OUT_DIR + "_v3")
        print(f"[atlas_v4] backed up v3 artifact -> {ea.OUT_DIR}_v3")
    ea.atlas_tick_frame = v4_frame
    ea.build()
    ea.reveal_future(force=True)   # v3 artifact's revealed flag; this is v4's ONE look
    ea.print_tables()


if __name__ == "__main__":
    main()
