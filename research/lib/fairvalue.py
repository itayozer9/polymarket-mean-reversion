"""Theoretical fair value of an Up/Down binary: P(spot ends above strike),
modelling the underlying as a driftless Gaussian over the remaining window.
"""
from __future__ import annotations
import numpy as np
from scipy.stats import norm


def bachelier_p_up(move_pct, realized_vol, time_left_sec):
    """P(Up) = Φ(move_pct / (realized_vol · √time_left_sec)).

    All inputs scalar or broadcastable arrays. `move_pct` is the spot's signed
    distance from strike in percent; `realized_vol` is per-second vol in the same
    percent units. When σ_remaining is 0 (no time / no vol), returns 0/0.5/1 by
    the sign of move_pct.
    """
    move = np.asarray(move_pct, dtype="f8")
    vol = np.asarray(realized_vol, dtype="f8")
    tl = np.clip(np.asarray(time_left_sec, dtype="f8"), 0.0, None)
    sigma = vol * np.sqrt(tl)
    out = np.where(move > 0, 1.0, np.where(move < 0, 0.0, 0.5))
    valid = sigma > 0
    out = np.where(valid, norm.cdf(np.divide(move, sigma, where=valid, out=np.zeros_like(move))), out)
    return out if out.ndim else float(out)
