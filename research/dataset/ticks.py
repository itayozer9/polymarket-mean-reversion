"""Tick-level canonical table: one row per tick + all derived features."""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.features.core import (
    corrected_proximity_pct, realized_vol_per_sec, sigma_proximity,
    rolling_drop_pct, odds_velocity, book_imbalance, spot_move_pct,
)

_WINDOW_SEC = {"5m": 300, "15m": 900}


def build_window_ticks(slug: str, ticks: pd.DataFrame,
                       outcome: str | None) -> pd.DataFrame:
    """Attach all derived features to one window's ticks. Pure per-window —
    uses no information from outside this window except the final `outcome`,
    which is a label (never an input feature)."""
    tf = slug.split("-")[2]
    dur = _WINDOW_SEC[tf]
    df = ticks.copy().reset_index(drop=True)

    move_pct = df["move_pct"].to_numpy("f8")
    sec = df["seconds_into_window"].to_numpy("f8")
    time_left = np.clip(dur - sec, 0, None)

    df["time_left_sec"] = time_left.astype("i4")
    df["proximity_pct"] = corrected_proximity_pct(move_pct)
    rvol = realized_vol_per_sec(move_pct, window=60)
    df["realized_vol"] = rvol
    df["sigma_proximity"] = sigma_proximity(move_pct, rvol, time_left)

    for w in (15, 30, 60):
        df[f"yes_drop_{w}s"] = rolling_drop_pct(df["yes_mid"].to_numpy("f8"), w)
        df[f"no_drop_{w}s"] = rolling_drop_pct(df["no_mid"].to_numpy("f8"), w)
    for w in (10, 30):
        df[f"yes_velocity_{w}s"] = odds_velocity(df["yes_mid"].to_numpy("f8"), w)
        df[f"no_velocity_{w}s"] = odds_velocity(df["no_mid"].to_numpy("f8"), w)
        df[f"spot_move_{w}s"] = spot_move_pct(move_pct, w)

    df["yes_imbalance"] = book_imbalance(
        df["yes_bid_depth"].to_numpy("f8"), df["yes_ask_depth"].to_numpy("f8"))
    df["no_imbalance"] = book_imbalance(
        df["no_bid_depth"].to_numpy("f8"), df["no_ask_depth"].to_numpy("f8"))

    df["outcome"] = outcome
    df["outcome_up"] = (1 if outcome == "Up" else 0 if outcome == "Down" else np.nan)
    df["slug"] = slug
    return df
