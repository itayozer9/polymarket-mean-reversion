"""Cheap-side entry-candidate table.

Frames every 15m tick from the perspective of the dip-buyer (cheap side):
whichever of YES / NO has the lower mid. One row per tick. Avoids the
YES/NO double-counting that would arise from including both sides.

Main outputs
------------
build_entry_candidates(ticks) -> pd.DataFrame   # pure function, testable
run()                                           # load → build → write parquet
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.lib.splits import add_date_col


_OUTPUT_PATH = "data/research/entry_candidates_15m.parquet"
_INPUT_PATH = "data/research/ticks_15m.parquet"


def build_entry_candidates(ticks: pd.DataFrame) -> pd.DataFrame:
    """Return a cheap-side view of `ticks`.

    For each row the cheap side = whichever of YES / NO has the lower mid
    (ties go to YES).  Every ``cheap_*`` column is pulled from the
    corresponding YES or NO column with ``np.where``.

    Parameters
    ----------
    ticks:
        Raw tick DataFrame with at least the columns specified in the plan.
        Must contain ``window_start_ts`` (Unix seconds) for the date column
        derivation.

    Returns
    -------
    DataFrame with columns:
        slug, symbol, date, timestamp_ms, seconds_into_window, time_left_sec,
        cheap_side, cheap_mid, cheap_ask, cheap_bid, cheap_spread, cheap_depth,
        proximity_pct, sigma_proximity, realized_vol,
        cheap_drop_30s, cheap_velocity_30s, spot_move_30s, cheap_imbalance,
        cheap_won (1.0 / 0.0 / NaN).
    """
    t = ticks.reset_index(drop=True)

    # Cheap side = YES when yes_mid <= no_mid (ties to YES)
    cheap_is_yes = t["yes_mid"].values <= t["no_mid"].values

    cheap_side = np.where(cheap_is_yes, "YES", "NO")
    cheap_mid = np.where(cheap_is_yes, t["yes_mid"].values, t["no_mid"].values)
    cheap_ask = np.where(cheap_is_yes, t["yes_best_ask"].values, t["no_best_ask"].values)
    cheap_bid = np.where(cheap_is_yes, t["yes_best_bid"].values, t["no_best_bid"].values)
    cheap_spread = cheap_ask - cheap_bid
    cheap_depth = np.where(cheap_is_yes, t["yes_ask_depth"].values, t["no_ask_depth"].values)
    cheap_drop_30s = np.where(cheap_is_yes, t["yes_drop_30s"].values, t["no_drop_30s"].values)
    cheap_velocity_30s = np.where(cheap_is_yes, t["yes_velocity_30s"].values, t["no_velocity_30s"].values)
    cheap_imbalance = np.where(cheap_is_yes, t["yes_imbalance"].values, t["no_imbalance"].values)

    # cheap_won: 1.0 if cheap side's outcome resolved in its favour, else 0.0; NaN if no outcome
    outcome_up = t["outcome_up"].values  # 1.0=Up, 0.0=Down, NaN=unresolved
    cheap_won_yes = np.where(outcome_up == 1.0, 1.0, np.where(outcome_up == 0.0, 0.0, np.nan))
    cheap_won_no = np.where(outcome_up == 0.0, 1.0, np.where(outcome_up == 1.0, 0.0, np.nan))
    cheap_won = np.where(cheap_is_yes, cheap_won_yes, cheap_won_no).astype(float)

    # Use 'slug' column (prefer it; fall back to 'market_slug')
    slug_col = t["slug"] if "slug" in t.columns else t["market_slug"]

    out = pd.DataFrame({
        "slug": slug_col.values,
        "symbol": t["symbol"].values,
        "timestamp_ms": t["timestamp_ms"].values,
        "window_start_ts": t["window_start_ts"].values,
        "seconds_into_window": t["seconds_into_window"].values,
        "time_left_sec": t["time_left_sec"].values,
        "cheap_side": cheap_side,
        "cheap_mid": cheap_mid,
        "cheap_ask": cheap_ask,
        "cheap_bid": cheap_bid,
        "cheap_spread": cheap_spread,
        "cheap_depth": cheap_depth,
        "proximity_pct": t["proximity_pct"].values,
        "sigma_proximity": t["sigma_proximity"].values,
        "realized_vol": t["realized_vol"].values,
        "cheap_drop_30s": cheap_drop_30s,
        "cheap_velocity_30s": cheap_velocity_30s,
        "spot_move_30s": t["spot_move_30s"].values,
        "cheap_imbalance": cheap_imbalance,
        "cheap_won": cheap_won,
    })

    # Add date column derived from window_start_ts
    out = add_date_col(out, ts_col="window_start_ts")

    return out


def run() -> None:
    """Load ticks_15m, build the entry-candidate table, write parquet."""
    print(f"Loading {_INPUT_PATH} …")
    ticks = pd.read_parquet(_INPUT_PATH)
    print(f"  {len(ticks):,} rows loaded.")

    print("Building cheap-side entry-candidate table …")
    ec = build_entry_candidates(ticks)

    total = len(ec)
    non_null_frac = ec["cheap_won"].notna().mean()
    print(f"  {total:,} rows → cheap_won non-null fraction: {non_null_frac:.4f}")

    print(f"Writing {_OUTPUT_PATH} …")
    ec.to_parquet(_OUTPUT_PATH, index=False)
    print("Done.")


if __name__ == "__main__":
    run()
