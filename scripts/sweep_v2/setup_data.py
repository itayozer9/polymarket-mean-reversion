"""Prepare data/sweep_v2/combined/ with all May live data + March historical + fresh outcomes.

Run once before any sweep_v2 stage. Idempotent — re-running refreshes the outcomes copy
and re-links any missing CSVs.
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
SWEEP_DIR = DATA / "sweep_v2"
COMBINED = SWEEP_DIR / "combined"


def link_csvs(src_dir: Path, dest_dir: Path, prefix_filter: str | None = None) -> int:
    n = 0
    for csv in sorted(src_dir.glob("*.csv.gz")):
        if prefix_filter and not csv.name.startswith(prefix_filter):
            continue
        if csv.name.endswith("_raw.csv.gz"):
            continue
        dest = dest_dir / csv.name
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        os.symlink(csv.resolve(), dest)
        n += 1
    return n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-march",
        action="store_true",
        help="Also link historical/ (March 4-17) for Stage-13 cross-regime replay.",
    )
    args = parser.parse_args()

    COMBINED.mkdir(parents=True, exist_ok=True)

    n_live = link_csvs(DATA / "live", COMBINED)
    print(f"Linked {n_live} live CSVs from data/live/.")

    if args.include_march:
        n_hist = link_csvs(DATA / "historical", COMBINED)
        print(f"Linked {n_hist} historical CSVs from data/historical/.")

    out_src = DATA / "outcomes.csv"
    out_dest = COMBINED / "outcomes.csv"
    if out_dest.exists() or out_dest.is_symlink():
        out_dest.unlink()
    shutil.copy2(out_src, out_dest)
    print(f"Copied outcomes.csv ({out_src.stat().st_size:,} bytes).")

    print(f"\nReady at {COMBINED}")


if __name__ == "__main__":
    main()
