"""Phase 1 — canonical dataset REBUILD with corrected strikes + real outcomes.

Background
----------
A discovery bug (Task 8c) froze each window's ``start_price`` at the Coinbase
spot sampled ~30 min *before* the window opened, corrupting ``start_price``,
``move_pct``, ``outcome``, ``outcome_up``, ``proximity_pct`` and
``sigma_proximity`` for all May 15m windows. The old ``outcome_up`` was wrong on
31% of windows.

``data/research/corrected_labels.parquet`` (produced by
``research/analysis/corrected_labels.py``) carries the correct data:
  - ``true_strike``           — Coinbase spot at the genuine window-open tick
  - ``api_price_to_beat``     — Chainlink strike (cross-check only)
  - ``authoritative_outcome_up`` — real Polymarket-resolved outcome (100% API)

Strike choice
-------------
We use ``true_strike`` (Coinbase) as the strike for ``move_pct`` / proximity
features because:
  a) the tick stream uses ``coinbase_price`` as the underlying, so move_pct
     must be consistent with the same feed;
  b) ``api_price_to_beat`` is the Chainlink strike (a different feed) and
     would introduce a systematic basis when computing per-tick move_pct.

We record which source was used in ``strike_source`` (always "coinbase").

Scope
-----
Only 15m windows are rebuilt here (5m is a control, out of scope for this
correction). ``ticks_5m.parquet`` is left untouched.

Usage
-----
    uv run python -m research.rebuild_dataset
"""
from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd

from research.data.loader import iter_windows
from research.dataset.ticks import build_window_ticks
from research.dataset.windows import build_window_row

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(REPO_ROOT, "data", "research")
CORRECTED_LABELS_PATH = os.path.join(OUTPUT_DIR, "corrected_labels.parquet")

SYMBOLS = ["btc", "eth", "sol", "xrp"]
DATE_START = "2026-05-15"
DATE_END = "2026-05-22"


def _load_corrected_labels() -> pd.DataFrame:
    """Load corrected_labels.parquet, indexed by slug."""
    cl = pd.read_parquet(CORRECTED_LABELS_PATH)
    return cl.set_index("slug")


def run() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    t_total = time.time()
    print("=" * 68)
    print("Rebuilding canonical 15m dataset with corrected strikes + outcomes")
    print(f"  Labels source : {CORRECTED_LABELS_PATH}")
    print(f"  Strike source : true_strike (Coinbase at genuine window-open)")
    print(f"  Outcome source: authoritative_outcome_up (Polymarket API, 100%)")
    print(f"  Scope         : {SYMBOLS}  15m  {DATE_START} .. {DATE_END}")
    print("=" * 68)

    cl = _load_corrected_labels()
    print(f"\nLoaded corrected_labels: {len(cl)} windows\n")

    # ------------------------------------------------------------------
    # Load the existing windows.parquet to preserve 5m rows intact
    # ------------------------------------------------------------------
    windows_path = os.path.join(OUTPUT_DIR, "windows.parquet")
    existing_windows = pd.read_parquet(windows_path)
    rows_5m = existing_windows[existing_windows["timeframe"] == "5m"].copy()
    print(f"Preserved {len(rows_5m):,} existing 5m window rows (out of scope).")

    # ------------------------------------------------------------------
    # Iterate over all 15m windows, apply corrected strike + outcome
    # ------------------------------------------------------------------
    all_15m_window_rows: list[dict] = []
    tick_frames_15m: list[pd.DataFrame] = []

    n_total_windows = 0
    n_total_ticks = 0
    n_slug_not_in_labels = 0

    for symbol in SYMBOLS:
        t0 = time.time()
        n_windows = 0
        n_ticks = 0

        for slug, ticks in iter_windows(symbol, "15m", DATE_START, DATE_END):
            if slug not in cl.index:
                n_slug_not_in_labels += 1
                continue

            row_cl = cl.loc[slug]

            # --- Corrected strike (Coinbase-at-open) ---
            strike = float(row_cl["true_strike"])
            strike_source = "coinbase"

            # --- Recompute move_pct per tick ---
            ticks = ticks.copy()
            ticks["start_price"] = strike
            ticks["move_pct"] = (ticks["coinbase_price"] - strike) / strike * 100.0

            # --- Corrected outcome ---
            auth_up = row_cl["authoritative_outcome_up"]
            if pd.isna(auth_up):
                outcome = None
                outcome_up_val = np.nan
            elif float(auth_up) == 1.0:
                outcome = "Up"
                outcome_up_val = 1.0
            else:
                outcome = "Down"
                outcome_up_val = 0.0

            # --- Corrected end_price: use api_final_price if available ---
            api_final = row_cl.get("api_final_price", np.nan)
            end_price = float(api_final) if pd.notna(api_final) else None

            # --- Build window row ---
            win_row = build_window_row(slug, ticks, outcome, end_price)
            win_row["strike"] = strike           # override with corrected strike
            win_row["strike_source"] = strike_source
            all_15m_window_rows.append(win_row)

            # --- Build tick frame with corrected move_pct ---
            tick_df = build_window_ticks(slug, ticks, outcome)
            tick_frames_15m.append(tick_df)

            n_windows += 1
            n_ticks += len(ticks)

        elapsed = time.time() - t0
        print(
            f"  {symbol:3s} 15m: "
            f"{n_windows:5d} windows, "
            f"{n_ticks:9d} ticks  "
            f"[{elapsed:.1f}s]"
        )
        n_total_windows += n_windows
        n_total_ticks += n_ticks

    if n_slug_not_in_labels:
        print(
            f"\nWARNING: {n_slug_not_in_labels} slugs had no entry in "
            f"corrected_labels.parquet and were SKIPPED."
        )

    # ------------------------------------------------------------------
    # Merge 15m window rows with existing 5m rows → overwrite windows.parquet
    # ------------------------------------------------------------------
    print(f"\nWriting corrected windows.parquet ...")
    new_15m_df = pd.DataFrame(all_15m_window_rows)
    # Align dtypes: rows_5m won't have 'strike_source' — add it as NaN
    rows_5m = rows_5m.copy()
    rows_5m["strike_source"] = None
    windows_df = pd.concat([new_15m_df, rows_5m], ignore_index=True)
    windows_df = windows_df.sort_values(
        ["symbol", "timeframe", "window_start_ts"]
    ).reset_index(drop=True)
    windows_df.to_parquet(windows_path, index=False)
    n_15m_out = len(new_15m_df)
    n_5m_out = len(rows_5m)
    sz = os.path.getsize(windows_path) / 1024
    print(f"  {len(windows_df):,} rows ({n_15m_out} 15m + {n_5m_out} 5m) → {windows_path}")
    print(f"  size: {sz:.1f} KB")

    # ------------------------------------------------------------------
    # Write corrected ticks_15m.parquet
    # ------------------------------------------------------------------
    ticks15m_path = os.path.join(OUTPUT_DIR, "ticks_15m.parquet")
    print(f"\nWriting corrected ticks_15m.parquet ...")
    ticks_df = pd.concat(tick_frames_15m, ignore_index=True)
    ticks_df.to_parquet(ticks15m_path, index=False)
    sz = os.path.getsize(ticks15m_path) / 1024
    print(f"  {len(ticks_df):,} rows → {ticks15m_path}")
    print(f"  size: {sz:.1f} KB")

    # ------------------------------------------------------------------
    # Rebuild entry_candidates_15m.parquet
    # ------------------------------------------------------------------
    print("\nRebuilding entry_candidates_15m.parquet ...")
    from research.analysis.entry_candidates import build_entry_candidates, _OUTPUT_PATH as EC_OUT
    ec = build_entry_candidates(ticks_df)
    ec.to_parquet(EC_OUT, index=False)
    sz = os.path.getsize(EC_OUT) / 1024
    print(f"  {len(ec):,} rows → {EC_OUT}")
    print(f"  size: {sz:.1f} KB")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    print("\n" + "=" * 68)
    print("VALIDATION")
    print("=" * 68)

    w15 = windows_df[windows_df["timeframe"] == "15m"].copy()

    # 1. proximity_pct == |move_pct| in ticks
    prox_diff = np.abs(ticks_df["proximity_pct"].values - np.abs(ticks_df["move_pct"].values))
    max_prox_diff = float(np.nanmax(prox_diff))
    prox_ok = max_prox_diff < 1e-9
    print(f"[{'PASS' if prox_ok else 'FAIL'}] proximity_pct == |move_pct| : max diff = {max_prox_diff:.2e}")

    # 2. time_left_sec never negative
    n_neg_time = int((ticks_df["time_left_sec"] < 0).sum())
    time_ok = n_neg_time == 0
    print(f"[{'PASS' if time_ok else 'FAIL'}] time_left_sec never negative : {n_neg_time} negative rows")

    # 3. outcome_up constant within each slug (ticks)
    grp_outcome_up = (
        ticks_df.groupby("slug")["outcome_up"].nunique(dropna=False)
    )
    violating = int((grp_outcome_up > 1).sum())
    const_ok = violating == 0
    print(f"[{'PASS' if const_ok else 'FAIL'}] outcome_up constant per slug : {violating} violating slugs")

    # 4. outcome_up in ticks matches corrected_labels
    slug_to_outcome_up = (
        ticks_df.groupby("slug")["outcome_up"].first().rename("ticks_up")
    )
    cl_up = cl["authoritative_outcome_up"].rename("auth_up")
    merged_check = slug_to_outcome_up.to_frame().join(cl_up, how="inner")
    mismatch = int(
        (merged_check["ticks_up"].fillna(-1) != merged_check["auth_up"].fillna(-1)).sum()
    )
    match_ok = mismatch == 0
    print(f"[{'PASS' if match_ok else 'FAIL'}] outcome_up matches corrected_labels.authoritative_outcome_up "
          f": {mismatch} mismatches")

    # 5. Window counts
    print(f"\n  15m windows rebuilt : {n_15m_out:,}")
    print(f"  5m windows preserved: {n_5m_out:,}")
    print(f"  Total ticks (15m)   : {len(ticks_df):,}")

    # 6. Per-symbol P(Up) base rates
    print("\n  Per-symbol P(Up) base rates (15m, corrected):")
    print(f"  {'Symbol':<8} {'P(Up)':>8}  {'n windows':>10}  {'n ticks':>10}")
    for sym in SYMBOLS:
        sym_rows = w15[w15["symbol"] == sym]
        p_up = float(sym_rows["outcome_up"].mean())
        n_win = len(sym_rows)
        n_tick = int((ticks_df["symbol"] == sym).sum())
        print(f"  {sym:<8} {p_up:>8.4f}  {n_win:>10,}  {n_tick:>10,}")
    overall_p_up = float(w15["outcome_up"].mean())
    print(f"  {'Overall':<8} {overall_p_up:>8.4f}  {len(w15):>10,}")

    # 7. Old vs new overall P(Up)
    print(f"\n  Old (corrupt) overall P(Up) : 0.4690")
    print(f"  New (corrected) overall P(Up): {overall_p_up:.4f}")
    print(f"  Expected ~0.4906 per corrected_labels.md")

    total_elapsed = time.time() - t_total
    print(f"\nRebuild complete in {total_elapsed:.1f}s")
    all_pass = prox_ok and time_ok and const_ok and match_ok
    print(f"All validation checks PASSED: {all_pass}")
    if not all_pass:
        raise RuntimeError("One or more validation checks FAILED — see output above.")


if __name__ == "__main__":
    run()
