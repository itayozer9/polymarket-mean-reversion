"""Train/test split utilities. The split unit is always a whole UTC day —
never an individual tick — so a window's outcome cannot leak across splits.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.holdout import DEV_START, DEV_END, HOLDOUT_START, HOLDOUT_END


def add_date_col(df: pd.DataFrame, ts_col: str = "window_start_ts") -> pd.DataFrame:
    """Return a copy with a 'date' column (UTC YYYY-MM-DD) derived from ts_col
    (Unix seconds)."""
    out = df.copy()
    out["date"] = pd.to_datetime(out[ts_col], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    return out


def dev_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask: rows in the development date range [DEV_START, DEV_END]."""
    d = df["date"] if "date" in df.columns else add_date_col(df)["date"]
    return (d >= DEV_START) & (d <= DEV_END)


def holdout_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask: rows in the sealed hold-out [HOLDOUT_START, HOLDOUT_END]."""
    d = df["date"] if "date" in df.columns else add_date_col(df)["date"]
    return (d >= HOLDOUT_START) & (d <= HOLDOUT_END)


def day_blocked_kfold(df: pd.DataFrame, k: int = 5, seed: int = 0):
    """Yield (train_idx, test_idx) for k folds. Whole days are randomly assigned
    to folds; every day is in the test set of exactly one fold. Returns a list."""
    days = sorted(df["date"].unique())
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(days))
    fold_of = {days[perm[i]]: i % k for i in range(len(days))}
    folds = []
    for f in range(k):
        test_days = {d for d, fd in fold_of.items() if fd == f}
        if not test_days:
            continue
        test_idx = df.index[df["date"].isin(test_days)].to_numpy()
        train_idx = df.index[~df["date"].isin(test_days)].to_numpy()
        folds.append((train_idx, test_idx))
    return folds


def leave_one_day_out(df: pd.DataFrame):
    """Yield (train_idx, test_idx) with each test set being exactly one day."""
    for d in sorted(df["date"].unique()):
        test_idx = df.index[df["date"] == d].to_numpy()
        train_idx = df.index[df["date"] != d].to_numpy()
        yield train_idx, test_idx
