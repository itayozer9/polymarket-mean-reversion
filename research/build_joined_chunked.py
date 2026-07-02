"""Resumable per-(timeframe, symbol) joined-frame builder.

The monolithic `research.build_joined` run was externally killed twice on 2026-07-02
(~50 min of work lost each time; not OOM — 63% RAM free, no jetsam). This builder writes
one part per (tf, symbol) to data/research/parts/ and skips parts that are already fresh,
so a kill loses at most one part (~10 min). Finishes by concatenating parts into the
canonical joined_{tf}.parquet + rebuilding the 15m slim.

Run:  uv run python -m research.build_joined_chunked            # 15m then 5m
      uv run python -m research.build_joined_chunked --only-15m
"""
from __future__ import annotations
import os
import sys

import pandas as pd

import research.dataset.joined as J
from research.clean_window import CLEAN_START, available_clean_dates

PARTS = os.path.join(J.OUTPUT_DIR, "parts")
SYMS = ("btc", "eth", "sol", "xrp")


def build_tf(tf: str, outcomes, date_start: str, date_end: str) -> None:
    os.makedirs(PARTS, exist_ok=True)
    frames = []
    for sym in SYMS:
        part = os.path.join(PARTS, f"joined_{tf}_{sym}.parquet")
        if os.path.exists(part):
            have = pd.read_parquet(part, columns=["date"])["date"].max()
            if have >= date_end:
                print(f"  [{tf}/{sym}] part fresh through {have} — skip", flush=True)
                frames.append(pd.read_parquet(part))
                continue
        print(f"  [{tf}/{sym}] building {date_start}..{date_end} ...", flush=True)
        d = J._enrich_symbol(sym, tf, date_start, date_end, outcomes)
        if d.empty:
            print(f"  [{tf}/{sym}] EMPTY", flush=True)
            continue
        d.to_parquet(part, index=False)
        print(f"  [{tf}/{sym}] {len(d):,} ticks, {d['slug'].nunique():,} windows", flush=True)
        frames.append(d)
    if not frames:
        print(f"  (no data for {tf})", flush=True)
        return
    out = pd.concat(frames, ignore_index=True)
    path = os.path.join(J.OUTPUT_DIR, f"joined_{tf}.parquet")
    out.to_parquet(path, index=False)
    print(f"  -> {path} ({len(out):,} rows, {os.path.getsize(path)/1e6:.1f} MB)", flush=True)


def main() -> None:
    outcomes = J.load_outcomes()
    dates = available_clean_dates("btc")
    date_start, date_end = CLEAN_START, (dates[-1] if dates else CLEAN_START)
    print(f"Chunked joined build: {date_start} .. {date_end}", flush=True)
    tfs = ("15m",) if "--only-15m" in sys.argv else ("15m", "5m")
    for tf in tfs:
        build_tf(tf, outcomes, date_start, date_end)
    if "15m" in tfs:
        from research.analysis.edge_lab import build_slim
        build_slim()
        print("  slim rebuilt", flush=True)


if __name__ == "__main__":
    main()
