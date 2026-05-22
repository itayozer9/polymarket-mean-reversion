"""Phase 1 — canonical dataset build entrypoint.

Builds three Parquet files under data/research/:
  - windows.parquet     : one row per market window (all symbols × timeframes)
  - ticks_15m.parquet   : one row per tick, 15m windows, all features attached
  - ticks_5m.parquet    : one row per tick, 5m windows, all features attached

Data scope: 2026-05-15 .. 2026-05-22 (March is quarantined; the loader enforces
this by default — do NOT pass include_quarantined here).

Usage:
    uv run python -m research.build_dataset
"""
from __future__ import annotations
import os
import time
from typing import Iterator

import pandas as pd

from research.data.loader import iter_windows, load_outcomes
from research.dataset.windows import build_window_row
from research.dataset.ticks import build_window_ticks

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(REPO_ROOT, "data", "research")

SYMBOLS = ["btc", "eth", "sol", "xrp"]
TIMEFRAMES = ["15m", "5m"]
DATE_START = "2026-05-15"
DATE_END = "2026-05-22"


def _iter_windows_with_outcome(
    symbol: str,
    timeframe: str,
    outcomes: pd.DataFrame,
) -> Iterator[tuple[str, pd.DataFrame, str | None, float | None]]:
    """Yield (slug, ticks, outcome, end_price) for every window."""
    for slug, ticks in iter_windows(symbol, timeframe, DATE_START, DATE_END):
        if slug in outcomes.index:
            oc = outcomes.loc[slug]
            outcome = str(oc["outcome"])
            end_price = float(oc["end_price"])
        else:
            outcome = None
            end_price = None
        yield slug, ticks, outcome, end_price


def run() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    outcomes = load_outcomes()

    total_t0 = time.time()
    print(f"Building canonical dataset: {DATE_START} .. {DATE_END}")
    print(f"Symbols: {SYMBOLS}  Timeframes: {TIMEFRAMES}\n")

    # ------------------------------------------------------------------
    # Collect window rows + tick frames in a single pass
    # ------------------------------------------------------------------
    all_window_rows: list[dict] = []
    tick_frames: dict[str, list[pd.DataFrame]] = {"15m": [], "5m": []}

    for symbol in SYMBOLS:
        for timeframe in TIMEFRAMES:
            t0 = time.time()
            n_windows = 0
            n_ticks = 0
            for slug, ticks, outcome, end_price in _iter_windows_with_outcome(
                symbol, timeframe, outcomes
            ):
                # Window row
                row = build_window_row(slug, ticks, outcome, end_price)
                all_window_rows.append(row)

                # Tick frame with features
                tick_df = build_window_ticks(slug, ticks, outcome)
                tick_frames[timeframe].append(tick_df)

                n_windows += 1
                n_ticks += len(ticks)

            elapsed = time.time() - t0
            print(
                f"  {symbol:3s} {timeframe}: "
                f"{n_windows:5d} windows, "
                f"{n_ticks:8d} ticks  "
                f"[{elapsed:.1f}s]"
            )

    # ------------------------------------------------------------------
    # Write windows.parquet
    # ------------------------------------------------------------------
    print("\nWriting windows.parquet …")
    windows_df = pd.DataFrame(all_window_rows)
    windows_path = os.path.join(OUTPUT_DIR, "windows.parquet")
    windows_df.to_parquet(windows_path, index=False)
    print(f"  {len(windows_df):,} rows → {windows_path}")
    print(f"  size: {os.path.getsize(windows_path) / 1024:.1f} KB")

    # ------------------------------------------------------------------
    # Write ticks_15m.parquet and ticks_5m.parquet
    # ------------------------------------------------------------------
    for tf in TIMEFRAMES:
        print(f"\nWriting ticks_{tf}.parquet …")
        if not tick_frames[tf]:
            print(f"  (no frames for {tf})")
            continue
        ticks_df = pd.concat(tick_frames[tf], ignore_index=True)
        out_path = os.path.join(OUTPUT_DIR, f"ticks_{tf}.parquet")
        ticks_df.to_parquet(out_path, index=False)
        print(f"  {len(ticks_df):,} rows → {out_path}")
        print(f"  size: {os.path.getsize(out_path) / 1024:.1f} KB")

    total_elapsed = time.time() - total_t0
    print(f"\nBuild complete in {total_elapsed:.1f}s")


if __name__ == "__main__":
    run()
