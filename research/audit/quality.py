"""Tick data quality audit.

For every (symbol, timeframe) combination over the full range 2026-03-04..2026-05-22,
compute per-day quality metrics and print a summary table.

Metrics per day:
- window_count: number of market windows observed
- tick_count: total tick rows
- ticks_per_window_mean: average ticks per window
- gap_rate: fraction of consecutive ticks within a window where seconds_into_window
  differs by >2 (sampling gaps)
- stale_rate: fraction of ticks where yes_best_bid/ask/no_best_bid/ask are ALL
  identical to the previous tick (frozen book)
- crossed_book_rate: fraction where yes_best_bid > yes_best_ask OR
  no_best_bid > no_best_ask
- mid_sum_error: mean |yes_mid + no_mid - 1.0| (should be ~0)
- median_yes_ask_depth_usd: median yes_ask_depth (USD)
- median_no_ask_depth_usd: median no_ask_depth (USD)

CONCERN thresholds (per plan):
- gap_rate > 10%
- stale_rate > 25%
- crossed_book_rate > 1%
- mid_sum_error > 0.02
"""
from __future__ import annotations

import os
import re
from datetime import datetime, date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from research.data.loader import list_tick_files, load_tick_csv

SYMBOLS = ["btc", "eth", "sol", "xrp"]
TIMEFRAMES = ["15m", "5m"]
DATE_START = "2026-03-04"
DATE_END = "2026-05-22"

# Concern thresholds from the plan
THRESHOLD_GAP_RATE = 0.10       # 10%
THRESHOLD_STALE_RATE = 0.25     # 25%
THRESHOLD_CROSSED_RATE = 0.01   # 1%
THRESHOLD_MID_SUM_ERR = 0.02    # 0.02


def _file_date(path: str) -> Optional[date]:
    m = re.search(r"_(\d{4}-\d{2}-\d{2})(?:_raw)?\.csv\.gz$", path)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def audit_day(df: pd.DataFrame, timeframe: str) -> dict:
    """Compute quality metrics for one day's data (pre-filtered to one timeframe)."""
    prefix = f"-updown-{timeframe}-"
    df = df[df["market_slug"].astype(str).str.contains(prefix, regex=False)].copy()

    if len(df) == 0:
        return None

    windows = df.groupby("market_slug", sort=False)
    window_count = len(windows)
    tick_count = len(df)
    ticks_per_window_mean = tick_count / window_count if window_count > 0 else 0.0

    gap_numerator = 0
    gap_denominator = 0
    stale_numerator = 0
    stale_denominator = 0

    for _, g in windows:
        g = g.sort_values("seconds_into_window")
        sec = g["seconds_into_window"].values
        n = len(sec)
        if n < 2:
            continue

        # Gap rate: consecutive ticks within window with seconds diff > 2
        diffs = np.diff(sec)
        gap_numerator += int((diffs > 2).sum())
        gap_denominator += len(diffs)

        # Stale rate: all 4 book columns identical to previous tick
        cols = ["yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask"]
        book = g[cols].values
        prev = book[:-1]
        curr = book[1:]
        all_same = np.all(prev == curr, axis=1)
        stale_numerator += int(all_same.sum())
        stale_denominator += len(all_same)

    gap_rate = gap_numerator / gap_denominator if gap_denominator > 0 else 0.0
    stale_rate = stale_numerator / stale_denominator if stale_denominator > 0 else 0.0

    # Crossed book rate
    crossed_yes = df["yes_best_bid"] > df["yes_best_ask"]
    crossed_no = df["no_best_bid"] > df["no_best_ask"]
    crossed_any = crossed_yes | crossed_no
    crossed_book_rate = crossed_any.mean() if len(df) > 0 else 0.0

    # Mid-sum error: |yes_mid + no_mid - 1.0|
    mid_sum_err = (df["yes_mid"] + df["no_mid"] - 1.0).abs().mean()

    # Depth profile
    median_yes_ask_depth = df["yes_ask_depth"].median()
    median_no_ask_depth = df["no_ask_depth"].median()

    return {
        "window_count": window_count,
        "tick_count": tick_count,
        "ticks_per_window_mean": round(ticks_per_window_mean, 1),
        "gap_rate": round(gap_rate, 4),
        "stale_rate": round(stale_rate, 4),
        "crossed_book_rate": round(crossed_book_rate, 4),
        "mid_sum_error": round(mid_sum_err, 5),
        "median_yes_ask_depth_usd": round(median_yes_ask_depth, 2),
        "median_no_ask_depth_usd": round(median_no_ask_depth, 2),
    }


def concern_flags(row: dict) -> list[str]:
    """Return list of concern tags for a metrics dict."""
    flags = []
    if row["gap_rate"] > THRESHOLD_GAP_RATE:
        flags.append(f"GAP({row['gap_rate']:.1%})")
    if row["stale_rate"] > THRESHOLD_STALE_RATE:
        flags.append(f"STALE({row['stale_rate']:.1%})")
    if row["crossed_book_rate"] > THRESHOLD_CROSSED_RATE:
        flags.append(f"CROSSED({row['crossed_book_rate']:.1%})")
    if row["mid_sum_error"] > THRESHOLD_MID_SUM_ERR:
        flags.append(f"MID_ERR({row['mid_sum_error']:.4f})")
    return flags


def run():
    all_rows = []

    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            print(f"\n{'='*70}")
            print(f"  {sym.upper()} / {tf}")
            print(f"{'='*70}")
            print(f"  {'Date':<12} {'Wins':>5} {'Ticks':>7} {'Tick/W':>7} "
                  f"{'Gap%':>7} {'Stale%':>8} {'Xd%':>6} {'MidErr':>8} "
                  f"{'YAskD':>8} {'NAskD':>8}  FLAGS")
            print(f"  {'-'*12} {'-'*5} {'-'*7} {'-'*7} "
                  f"{'-'*7} {'-'*8} {'-'*6} {'-'*8} "
                  f"{'-'*8} {'-'*8}  -----")

            sym_tf_rows = []
            files = list_tick_files(sym, DATE_START, DATE_END)
            for path in files:
                fd = _file_date(path)
                if fd is None:
                    continue
                date_str = fd.isoformat()
                try:
                    df = load_tick_csv(path)
                    metrics = audit_day(df, tf)
                except Exception as e:
                    print(f"  {date_str:<12} ERROR: {e}")
                    continue

                if metrics is None:
                    # No windows for this timeframe in this file
                    continue

                flags = concern_flags(metrics)
                flag_str = " ".join(flags) if flags else "ok"
                sym_tf_rows.append({"date": date_str, "symbol": sym, "timeframe": tf,
                                    **metrics, "concerns": flag_str})

                print(f"  {date_str:<12} {metrics['window_count']:>5} "
                      f"{metrics['tick_count']:>7} {metrics['ticks_per_window_mean']:>7.1f} "
                      f"{metrics['gap_rate']:>7.1%} {metrics['stale_rate']:>8.1%} "
                      f"{metrics['crossed_book_rate']:>6.1%} {metrics['mid_sum_error']:>8.5f} "
                      f"{metrics['median_yes_ask_depth_usd']:>8.2f} "
                      f"{metrics['median_no_ask_depth_usd']:>8.2f}  {flag_str}")

            if not sym_tf_rows:
                print("  (no data)")
                continue

            # Summary for this (sym, tf)
            agg = {
                "window_count": sum(r["window_count"] for r in sym_tf_rows),
                "tick_count": sum(r["tick_count"] for r in sym_tf_rows),
                "gap_rate": np.mean([r["gap_rate"] for r in sym_tf_rows]),
                "stale_rate": np.mean([r["stale_rate"] for r in sym_tf_rows]),
                "crossed_book_rate": np.mean([r["crossed_book_rate"] for r in sym_tf_rows]),
                "mid_sum_error": np.mean([r["mid_sum_error"] for r in sym_tf_rows]),
                "median_yes_ask_depth_usd": np.median([r["median_yes_ask_depth_usd"] for r in sym_tf_rows]),
                "median_no_ask_depth_usd": np.median([r["median_no_ask_depth_usd"] for r in sym_tf_rows]),
                "ticks_per_window_mean": (sum(r["tick_count"] for r in sym_tf_rows) /
                                          sum(r["window_count"] for r in sym_tf_rows)),
            }
            flags = concern_flags(agg)
            flag_str = " ".join(flags) if flags else "ok"
            print(f"  {'SUMMARY':<12} {agg['window_count']:>5} "
                  f"{agg['tick_count']:>7} {agg['ticks_per_window_mean']:>7.1f} "
                  f"{agg['gap_rate']:>7.1%} {agg['stale_rate']:>8.1%} "
                  f"{agg['crossed_book_rate']:>6.1%} {agg['mid_sum_error']:>8.5f} "
                  f"{agg['median_yes_ask_depth_usd']:>8.2f} "
                  f"{agg['median_no_ask_depth_usd']:>8.2f}  {flag_str}")

            all_rows.extend(sym_tf_rows)

    # Cross-symbol summary table
    print(f"\n\n{'='*70}")
    print("  CROSS-SYMBOL SUMMARY (mean over days)")
    print(f"{'='*70}")
    print(f"  {'Sym':>4} {'TF':>4} {'Days':>5} {'Wins':>6} {'Ticks':>8} "
          f"{'Gap%':>7} {'Stale%':>8} {'Xd%':>6} {'MidErr':>8} "
          f"{'YAskD':>8} {'NAskD':>8}  FLAGS")
    print(f"  {'-'*4} {'-'*4} {'-'*5} {'-'*6} {'-'*8} "
          f"{'-'*7} {'-'*8} {'-'*6} {'-'*8} "
          f"{'-'*8} {'-'*8}  -----")

    df_all = pd.DataFrame(all_rows)
    if len(df_all) == 0:
        print("  (no data)")
        return

    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            sub = df_all[(df_all["symbol"] == sym) & (df_all["timeframe"] == tf)]
            if len(sub) == 0:
                continue
            n_days = len(sub)
            total_wins = sub["window_count"].sum()
            total_ticks = sub["tick_count"].sum()
            agg = {
                "gap_rate": sub["gap_rate"].mean(),
                "stale_rate": sub["stale_rate"].mean(),
                "crossed_book_rate": sub["crossed_book_rate"].mean(),
                "mid_sum_error": sub["mid_sum_error"].mean(),
                "median_yes_ask_depth_usd": sub["median_yes_ask_depth_usd"].median(),
                "median_no_ask_depth_usd": sub["median_no_ask_depth_usd"].median(),
            }
            flags = concern_flags(agg)
            flag_str = " ".join(flags) if flags else "ok"
            print(f"  {sym:>4} {tf:>4} {n_days:>5} {total_wins:>6} {total_ticks:>8} "
                  f"{agg['gap_rate']:>7.1%} {agg['stale_rate']:>8.1%} "
                  f"{agg['crossed_book_rate']:>6.1%} {agg['mid_sum_error']:>8.5f} "
                  f"{agg['median_yes_ask_depth_usd']:>8.2f} "
                  f"{agg['median_no_ask_depth_usd']:>8.2f}  {flag_str}")

    # Early vs late March comparison
    print(f"\n\n{'='*70}")
    print("  MARCH REGIME: Mar 4-13 (early, smaller files) vs Mar 14-17 (late)")
    print(f"{'='*70}")
    march_early = df_all[df_all["date"] < "2026-03-14"]
    march_late = df_all[(df_all["date"] >= "2026-03-14") & (df_all["date"] <= "2026-03-17")]
    may_live = df_all[df_all["date"] >= "2026-05-15"]

    for label, sub in [("Mar 04-13", march_early), ("Mar 14-17", march_late),
                        ("May 15-22", may_live)]:
        if len(sub) == 0:
            continue
        agg = {
            "gap_rate": sub["gap_rate"].mean(),
            "stale_rate": sub["stale_rate"].mean(),
            "crossed_book_rate": sub["crossed_book_rate"].mean(),
            "mid_sum_error": sub["mid_sum_error"].mean(),
            "median_yes_ask_depth_usd": sub["median_yes_ask_depth_usd"].median(),
            "median_no_ask_depth_usd": sub["median_no_ask_depth_usd"].median(),
        }
        flags = concern_flags(agg)
        flag_str = " ".join(flags) if flags else "ok"
        print(f"  {label:<10} gap={agg['gap_rate']:.1%} stale={agg['stale_rate']:.1%} "
              f"crossed={agg['crossed_book_rate']:.1%} mid_err={agg['mid_sum_error']:.5f} "
              f"yes_ask_depth_med={agg['median_yes_ask_depth_usd']:.2f} "
              f"no_ask_depth_med={agg['median_no_ask_depth_usd']:.2f}  {flag_str}")

    print(f"\n{'='*70}")
    print("  NOTE: Data covers two separate regimes: Mar 4-17 (historical) and")
    print("        May 15-22 (live). An ~8-week gap (Mar 18 – May 14) has no data.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    run()
