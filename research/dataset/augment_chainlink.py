"""One-time: populate `chainlink_price` (+ `cl_cb_basis_bps`) in the EXISTING
joined_<tf>.parquet without a full rebuild. Idempotent and safe — writes a temp file
then atomically replaces, keeping a one-time `.coinbase.bak` of the pre-merge parquet.

Run: uv run python -m research.dataset.augment_chainlink            # 15m
     uv run python -m research.dataset.augment_chainlink 15m 5m
"""
from __future__ import annotations
import os
import shutil
import sys

import pandas as pd

from research.dataset.chainlink_merge import asof_merge_chainlink

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, "data", "research")


def run(tfs) -> None:
    for tf in tfs:
        p = os.path.join(OUT, f"joined_{tf}.parquet")
        if not os.path.exists(p):
            print(f"skip {tf}: {p} missing")
            continue
        df = pd.read_parquet(p)
        n0 = len(df)
        df = asof_merge_chainlink(df)
        assert len(df) == n0, f"row count changed {n0} -> {len(df)}!"
        nz = float((df["chainlink_price"].fillna(0) != 0).mean())
        cov = float(df["chainlink_price"].notna().mean())
        basis = float(df["cl_cb_basis_bps"].abs().median())
        print(f"{tf}: rows={n0:,}  chainlink_price nonzero={nz:.3f} coverage={cov:.3f}  "
              f"|basis| median={basis:.2f} bps")
        tmp = p + ".tmp"
        df.to_parquet(tmp, index=False)
        bak = p + ".coinbase.bak"
        if not os.path.exists(bak):
            shutil.copy2(p, bak)
        os.replace(tmp, p)
        print(f"  -> wrote {p}  (one-time backup: {bak})")


if __name__ == "__main__":
    tfs = [a for a in sys.argv[1:] if a in ("15m", "5m")] or ["15m"]
    run(tfs)
