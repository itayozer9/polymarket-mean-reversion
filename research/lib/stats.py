"""Window-clustered statistics. The resampling unit is the window (group), never
the tick — Phase 0 found ~87% of ticks are stale, so ticks are not independent.
"""
from __future__ import annotations
import numpy as np


def window_clustered_bootstrap(values, groups, n: int = 5000, seed: int = 0):
    """Cluster bootstrap of the mean of `values`, resampling whole `groups`.

    values, groups: 1-D arrays of equal length. Returns (p5, p50, p95) of the
    bootstrap distribution of the mean.
    """
    values = np.asarray(values, dtype="f8")
    groups = np.asarray(groups)
    uniq = np.unique(groups)
    # Pre-index rows per group for fast resampling.
    idx_by_group = {g: np.where(groups == g)[0] for g in uniq}
    rng = np.random.default_rng(seed)
    means = np.empty(n, dtype="f8")
    for b in range(n):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        rows = np.concatenate([idx_by_group[g] for g in pick])
        means[b] = values[rows].mean()
    return tuple(float(x) for x in np.percentile(means, [5, 50, 95]))


def reliability_curve(pred, outcome, groups, n_bins: int = 10, seed: int = 0):
    """Bucket `pred` into n_bins equal-width bins on [0,1]; per bin return the
    mean predicted prob, the realized outcome frequency, the window-clustered
    95% CI of that realized frequency, and counts.

    Returns a list of dicts: bin_lo, bin_hi, n_ticks, n_windows, mean_pred,
    realized, ci_lo, ci_hi.
    """
    pred = np.asarray(pred, dtype="f8")
    outcome = np.asarray(outcome, dtype="f8")
    groups = np.asarray(groups)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        m = (pred >= lo) & (pred < hi if i < n_bins - 1 else pred <= hi)
        if m.sum() == 0:
            continue
        ci_lo, _, ci_hi = window_clustered_bootstrap(outcome[m], groups[m], seed=seed)
        out.append({
            "bin_lo": float(lo), "bin_hi": float(hi),
            "n_ticks": int(m.sum()), "n_windows": int(np.unique(groups[m]).size),
            "mean_pred": float(pred[m].mean()),
            "realized": float(outcome[m].mean()),
            "ci_lo": ci_lo, "ci_hi": ci_hi,
        })
    return out
