"""Pure per-tick feature functions for the canonical research dataset.

Inputs are 1-D numpy arrays for one window's ticks in chronological order.
`move_pct` is in PERCENT (the raw CSV convention: (spot-strike)/strike*100).
"""
from __future__ import annotations
import numpy as np


def corrected_proximity_pct(move_pct: np.ndarray) -> np.ndarray:
    """Absolute distance of spot from strike, in PERCENT.

    This is the fix for the inert arb `proximity_pct_from_move` (which divided
    by 100, producing a fraction the percent-scaled threshold could never bind).
    """
    return np.abs(np.asarray(move_pct, dtype="f8"))


def realized_vol_per_sec(move_pct: np.ndarray, window: int = 60) -> np.ndarray:
    """Per-second volatility of the underlying, in percent units.

    Estimated as the trailing-window standard deviation of tick-to-tick changes
    in move_pct (ticks are ~1s apart). Returns 0.0 where there is no history.
    """
    mp = np.asarray(move_pct, dtype="f8")
    n = len(mp)
    out = np.zeros(n, dtype="f8")
    if n < 2:
        return out
    diffs = np.diff(mp, prepend=mp[0])
    for i in range(n):
        lo = max(0, i - window + 1)
        seg = diffs[lo:i + 1]
        if seg.size >= 2:
            out[i] = float(np.std(seg))
    return out


def sigma_proximity(move_pct: np.ndarray, vol_per_sec: np.ndarray,
                    time_left_sec: np.ndarray) -> np.ndarray:
    """Distance of spot from strike measured in standard-deviations of the
    underlying's expected remaining move.

    sigma_remaining = vol_per_sec * sqrt(time_left_sec)
    sigma_proximity = |move_pct| / sigma_remaining

    Small (<~1) => still a coin-flip (an odds dip is likely noise).
    Large (>~3) => effectively decided (the cheap side is cheap for real).
    Returns np.inf where sigma_remaining is 0 (no time / no vol).
    """
    mp = np.abs(np.asarray(move_pct, dtype="f8"))
    vps = np.asarray(vol_per_sec, dtype="f8")
    tl = np.clip(np.asarray(time_left_sec, dtype="f8"), 0.0, None)
    sigma_remaining = vps * np.sqrt(tl)
    out = np.full(len(mp), np.inf, dtype="f8")
    mask = sigma_remaining > 0
    out[mask] = mp[mask] / sigma_remaining[mask]
    return out
