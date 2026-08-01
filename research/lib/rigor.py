"""Rigor library — overfitting quantification, leak-free CV, risk-adjusted metrics.

This is the honesty core of the 2026-06 improvement program. References:
  - Bailey & López de Prado (2014), "The Deflated Sharpe Ratio".
  - Bailey, Borwein, López de Prado, Zhu (2017), "The Probability of Backtest
    Overfitting" (PBO via Combinatorially-Symmetric Cross-Validation, CSCV).
  - López de Prado (2018), "Advances in Financial Machine Learning"
    (purged & embargoed combinatorial CV).

CRITICAL discipline: the resampling / CV unit is a WHOLE UTC DAY (or a contiguous
block of days) — never a single trade. Trades within a day, and across the 4
coins on one macro move, are correlated; treating trades as i.i.d. inflates n and
fabricates significance. For per-trade dispersion use the window-clustered
bootstrap in research/lib/stats.py. For Sharpe/PSR/DSR pass DAILY pnl.

All functions are pure (numpy/pandas in, scalars/dicts out) and unit-testable.
"""
from __future__ import annotations
import itertools
import numpy as np
import pandas as pd
from scipy.stats import norm


# ---------------------------------------------------------------------------
# moments
# ---------------------------------------------------------------------------
def _clean(x):
    x = np.asarray(x, dtype="f8")
    return x[np.isfinite(x)]


def _skew(x) -> float:
    x = _clean(x)
    if x.size < 3:
        return 0.0
    m, s = x.mean(), x.std(ddof=0)
    return float(((x - m) ** 3).mean() / s ** 3) if s > 0 else 0.0


def _kurt(x) -> float:
    """Full (Pearson) kurtosis; Normal == 3.0."""
    x = _clean(x)
    if x.size < 4:
        return 3.0
    m, s = x.mean(), x.std(ddof=0)
    return float(((x - m) ** 4).mean() / s ** 4) if s > 0 else 3.0


# ---------------------------------------------------------------------------
# risk-adjusted return metrics  (pass DAILY pnl as the observation unit)
# ---------------------------------------------------------------------------
def sharpe(returns, periods_per_year: float | None = None) -> float:
    r = _clean(returns)
    if r.size < 2 or r.std(ddof=1) == 0:
        return float("nan")
    sr = r.mean() / r.std(ddof=1)
    if periods_per_year:
        sr *= np.sqrt(periods_per_year)
    return float(sr)


def sortino(returns, periods_per_year: float | None = None, mar: float = 0.0) -> float:
    r = _clean(returns)
    downside = r[r < mar] - mar
    dd = np.sqrt((downside ** 2).mean()) if downside.size else 0.0
    if dd == 0:
        return float("nan")
    s = (r.mean() - mar) / dd
    if periods_per_year:
        s *= np.sqrt(periods_per_year)
    return float(s)


def calmar(daily_pnl) -> float:
    """Total PnL / |max drawdown| of the cumulative daily series."""
    r = _clean(daily_pnl)
    if r.size == 0:
        return float("nan")
    mdd = abs(max_drawdown(np.cumsum(r)))
    return float(r.sum() / mdd) if mdd > 0 else float("inf")


def probabilistic_sharpe_ratio(returns, sr_benchmark: float = 0.0) -> float:
    """PSR = P(true per-period SR > sr_benchmark), correcting for skew, fat tails
    and sample length (Bailey-LdP). Returns a probability in [0,1]."""
    r = _clean(returns)
    n = r.size
    if n < 3 or r.std(ddof=1) == 0:
        return float("nan")
    sr = r.mean() / r.std(ddof=1)
    sk, ku = _skew(r), _kurt(r)
    denom = 1.0 - sk * sr + (ku - 1.0) / 4.0 * sr ** 2
    if denom <= 0:
        return float("nan")
    z = (sr - sr_benchmark) * np.sqrt(n - 1) / np.sqrt(denom)
    return float(norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """Expected maximum per-period SR across n_trials independent strategies whose
    per-period SRs ~ N(0, sr_std^2). Gumbel approximation (Bailey-LdP)."""
    if n_trials < 1:
        return 0.0
    if n_trials == 1:
        return 0.0
    g = np.euler_gamma
    return float(sr_std * ((1 - g) * norm.ppf(1 - 1.0 / n_trials)
                           + g * norm.ppf(1 - 1.0 / (n_trials * np.e))))


def deflated_sharpe_ratio(returns, n_trials: int, sr_std: float | None = None) -> dict:
    """DSR = PSR with the benchmark set to the expected-max SR achievable by luck
    across n_trials. sr_std = std of per-period SR ACROSS the trials; if unknown,
    a conservative default 1/sqrt(n-1) (the SR estimator's own s.e.) is used.
    Returns {sr, sr0, dsr, n_obs, skew, kurt}."""
    r = _clean(returns)
    n = r.size
    if n < 3 or r.std(ddof=1) == 0:
        return {"sr": float("nan"), "sr0": float("nan"), "dsr": float("nan"),
                "n_obs": int(n), "skew": float("nan"), "kurt": float("nan")}
    sr = r.mean() / r.std(ddof=1)
    if sr_std is None:
        sr_std = 1.0 / np.sqrt(n - 1)
    sr0 = expected_max_sharpe(n_trials, sr_std)
    return {"sr": float(sr), "sr0": float(sr0),
            "dsr": probabilistic_sharpe_ratio(r, sr_benchmark=sr0),
            "n_obs": int(n), "skew": _skew(r), "kurt": _kurt(r)}


def min_track_record_length(returns, sr_benchmark: float = 0.0,
                            prob: float = 0.95) -> float:
    """Min # of observations (days) needed for PSR(sr_benchmark) >= prob, given the
    observed SR/skew/kurtosis. inf if the observed SR <= benchmark."""
    r = _clean(returns)
    if r.size < 3 or r.std(ddof=1) == 0:
        return float("inf")
    sr = r.mean() / r.std(ddof=1)
    if sr <= sr_benchmark:
        return float("inf")
    sk, ku = _skew(r), _kurt(r)
    z = norm.ppf(prob)
    return float(1.0 + (1.0 - sk * sr + (ku - 1.0) / 4.0 * sr ** 2)
                 * (z / (sr - sr_benchmark)) ** 2)


# ---------------------------------------------------------------------------
# drawdown & worst-path
# ---------------------------------------------------------------------------
def max_drawdown(cum_pnl) -> float:
    c = np.asarray(cum_pnl, dtype="f8")
    if c.size == 0:
        return 0.0
    peak = np.maximum.accumulate(c)
    return float((c - peak).min())


def longest_losing_streak(daily_pnl) -> int:
    r = np.asarray(daily_pnl, dtype="f8")
    best = cur = 0
    for v in r:
        cur = cur + 1 if v < 0 else 0
        best = max(best, cur)
    return int(best)


def daily_pnl_from_ledger(ledger: pd.DataFrame, date_col: str = "date",
                          pnl_col: str = "pnl") -> pd.Series:
    """Sum pnl per calendar day, reindexed over the full date span (gaps -> 0)."""
    s = ledger.groupby(date_col)[pnl_col].sum().sort_index()
    full = pd.date_range(s.index.min(), s.index.max(), freq="D").strftime("%Y-%m-%d")
    return s.reindex(full, fill_value=0.0)


def block_bootstrap_worstpath(daily_pnl, n: int = 5000, block: int = 2,
                              seed: int = 0) -> dict:
    """Circular block bootstrap of a DAILY pnl series. Returns CI dicts for total
    PnL, max drawdown, and longest losing streak — the realistic worst-case the
    user would actually face. block>1 preserves short-run autocorrelation."""
    r = np.asarray(daily_pnl, dtype="f8")
    T = r.size
    if T < 2:
        return {}
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(T / block))
    totals = np.empty(n); mdds = np.empty(n); streaks = np.empty(n)
    for b in range(n):
        starts = rng.integers(0, T, size=nb)
        path = np.concatenate([np.take(r, range(s, s + block), mode="wrap") for s in starts])[:T]
        totals[b] = path.sum()
        mdds[b] = max_drawdown(np.cumsum(path))
        streaks[b] = longest_losing_streak(path)

    def ci(a):
        return {"p5": float(np.percentile(a, 5)), "p50": float(np.percentile(a, 50)),
                "p95": float(np.percentile(a, 95))}
    return {"total": ci(totals), "max_drawdown": ci(mdds),
            "longest_losing_streak": ci(streaks), "n_days": int(T)}


# ---------------------------------------------------------------------------
# leak-free cross-validation over DAYS
# ---------------------------------------------------------------------------
def walk_forward_splits(days, min_train: int = 4, test: int = 1,
                        expanding: bool = True):
    """Chronological train-past/test-future. Yields (train_days, test_days).
    expanding=True grows the train window; False uses a sliding window of
    `min_train` days. This is the PRIMARY realism gate."""
    days = list(days)
    i = min_train
    while i < len(days):
        train = days[:i] if expanding else days[max(0, i - min_train):i]
        test_days = days[i:i + test]
        if not test_days:
            break
        yield train, test_days
        i += test


def combinatorial_purged_cv(days, n_groups: int = 6, k_test: int = 2,
                            embargo_days: int = 1):
    """Combinatorial Purged CV (López de Prado). Partition the ordered `days` into
    `n_groups` contiguous groups; for every combination of `k_test` groups as
    TEST (scattered through time), TRAIN = the rest MINUS an embargo of
    `embargo_days` on each side of every test day (purge to kill leakage from
    autocorrelation / hold-to-resolution horizon). Yields (train_days, test_days).

    With 13 days, n_groups=6,k_test=2 -> C(6,2)=15 scattered-test folds; each test
    is ~2 contiguous days, train is the remaining ~9-10 non-adjacent days. This is
    the ADDITIVE robustness estimator — a complement to walk_forward, never a
    replacement (it trains future->past in some folds)."""
    days = list(days)
    D = len(days)
    groups = [list(g) for g in np.array_split(np.arange(D), n_groups)]
    for combo in itertools.combinations(range(n_groups), k_test):
        test_pos = sorted(p for g in combo for p in groups[g])
        test_set = set(test_pos)
        purged = set()
        for p in test_pos:
            for e in range(-embargo_days, embargo_days + 1):
                purged.add(p + e)
        train_pos = [p for p in range(D) if p not in test_set and p not in purged]
        if not train_pos or not test_pos:
            continue
        yield [days[p] for p in train_pos], [days[p] for p in test_pos]


# ---------------------------------------------------------------------------
# PBO via CSCV
# ---------------------------------------------------------------------------
def pbo_cscv(perf_matrix, n_splits: int = 10) -> dict:
    """Probability of Backtest Overfitting via Combinatorially-Symmetric CV.

    perf_matrix: shape (T, N) — T time-blocks (rows) x N candidate configs (cols),
    each cell a per-block performance (e.g. mean daily PnL). Splits the T rows into
    n_splits even groups; for every way to choose n_splits/2 groups as IS (rest =
    OOS), pick the best-IS config and record its OOS performance RANK as a logit.
    PBO = fraction of splits where the IS-winner lands below the OOS median
    (logit < 0) — i.e. how often "best in-sample" is actually below-median
    out-of-sample. Returns {pbo, n_combos, median_logit}."""
    M = np.asarray(perf_matrix, dtype="f8")
    T, N = M.shape
    if N < 2 or T < n_splits:
        return {"pbo": float("nan"), "n_combos": 0, "median_logit": float("nan")}
    if n_splits % 2:
        n_splits -= 1
    idx = [list(g) for g in np.array_split(np.arange(T), n_splits)]
    half = n_splits // 2
    logits = []
    for combo in itertools.combinations(range(n_splits), half):
        is_rows = np.concatenate([idx[g] for g in combo])
        oos_rows = np.concatenate([idx[g] for g in range(n_splits) if g not in combo])
        is_perf = np.nanmean(M[is_rows], axis=0)
        oos_perf = np.nanmean(M[oos_rows], axis=0)
        n_star = int(np.nanargmax(is_perf))
        # relative OOS rank of the IS-best config in (0,1)
        rank = (oos_perf < oos_perf[n_star]).sum() / max(N - 1, 1)
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(np.log(rank / (1 - rank)))
    logits = np.asarray(logits)
    return {"pbo": float((logits < 0).mean()), "n_combos": int(logits.size),
            "median_logit": float(np.median(logits))}
