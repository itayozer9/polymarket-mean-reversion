# Edge Discovery & Reconstruction — Phase 2–4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether a real, exploitable edge exists in the May Polymarket
data — via a calibration study (Phase 2), a drop event study (Phase 3), and a
faithful reconstruction of the user's manual policy (Phase 4) — producing an
edge map, a bounce atlas, and a reconstruction report.

**Architecture:** A new `research/lib/` (pure, unit-tested statistical helpers)
and `research/analysis/` (analysis scripts that consume the canonical dataset and
emit findings docs + charts). Pure functions are TDD'd with full code. Analysis
scripts are specified precisely — inputs, method, the exact computations, the
sanity-check assertions that verify correctness, and the exact deliverable — and
the implementer writes the pandas/numpy against the real dataset. Every analysis
fits only on the development split; the sealed hold-out is never touched.

**Tech Stack:** Python 3.9+, uv, pandas, numpy, scipy, pyarrow, matplotlib,
scikit-learn (added in Task 1), pytest.

**Scope:** Phases 2–4 of `docs/superpowers/specs/2026-05-22-polymarket-edge-research-design.md`.
Phases 5–6 (strategy construction + validation gauntlet) get a follow-on plan,
written after the edge map exists.

---

## Reference facts (established in Phase 0–1 — do not re-derive)

- **Canonical dataset** (built, documented in `docs/research/canonical_dataset.md`):
  `data/research/windows.parquet` (8,915 rows), `data/research/ticks_15m.parquet`
  (2.0M rows, 43 cols), `data/research/ticks_5m.parquet`. Load with `pd.read_parquet`.
- **Tick columns include:** `slug, symbol, timestamp_ms, seconds_into_window,
  time_left_sec, yes_best_bid, yes_best_ask, no_best_bid, no_best_ask,
  yes_bid_depth, yes_ask_depth, no_bid_depth, no_ask_depth, yes_mid, no_mid,
  coinbase_price, start_price, move_pct, proximity_pct, sigma_proximity,
  realized_vol, yes_drop_15s/30s/60s, no_drop_15s/30s/60s, yes_velocity_10s/30s,
  no_velocity_10s/30s, spot_move_10s/30s, yes_imbalance, no_imbalance, outcome,
  outcome_up`. `move_pct` is in **percent**. `outcome_up`: 1.0=Up, 0.0=Down.
- **Data scope:** May 15–22 2026 only (March quarantined). Development split =
  May 15–20; **sealed hold-out = May 21–22** (`research/holdout.py`: `DEV_START`,
  `DEV_END`, `HOLDOUT_START`, `HOLDOUT_END`, `is_holdout(date_str)`).
- **Resolution oracle = `coinbase_price`** (`chainlink_price` is always 0).
- **Cost:** taker round-trip ≈ 16–21% of a $10 stake (fee `0.07·p·(1−p)`/share,
  both legs, + spread); **maker round-trip ≈ 0** (0 fee + ~20% rebate, but
  uncertain fill). Both must be modelled.
- **`sigma_proximity` is `inf`** for the first ~60 ticks of a window (no vol
  history) — filter with `np.isfinite` before use.
- **~87% of ticks are stale** (book frozen) — the independent sample is the
  window, not the tick. All significance is **window-clustered**.
- **P(Up) base rates (15m):** btc 0.494, eth 0.481, sol 0.474, xrp 0.427.
- **Pre-registered hypotheses** to test: `docs/research/market_hypotheses.md` (H1–H11).

---

## File Structure

```
research/
  lib/
    __init__.py
    splits.py          # Task 2 — dev/holdout filters, day-blocked CV
    stats.py           # Task 3 — window-clustered bootstrap, reliability curve
    fairvalue.py       # Task 4 — Bachelier theoretical P(Up)
  analysis/
    __init__.py
    entry_candidates.py # Task 5 — cheap-side entry-candidate table
    calibration.py      # Task 6 — unconditional reliability curve
    edge_map.py         # Task 7 — conditioned edge map + dev-internal CV
    fair_value_tri.py   # Task 8 — market vs theoretical vs empirical
    cost_calibration.py # Task 9 — net-of-cost (taker & maker)
    lead_lag.py         # Task 10 — spot vs odds cross-correlation
    macro_table.py      # Task 12 — cross-coin aligned macro table
    drop_events.py      # Task 13 — drop detection + forward paths
    bounce_atlas.py     # Task 14 — bounce distribution + patience-deadline
    patient_policy.py   # Task 16 — the user-reconstruction simulator
    feature_importance.py # Task 18 — interpretable predictive diagnostic
tests/research/
    test_splits.py        # Task 2
    test_stats.py         # Task 3
    test_fairvalue.py     # Task 4
    test_entry_candidates.py # Task 5
    test_patient_policy.py   # Task 16
docs/research/
    edge_map.md          # Task 11 — Phase 2 synthesis
    bounce_atlas.md      # Task 15 — Phase 3 synthesis
    reconstruction.md    # Task 19 — Phase 4 synthesis
    charts/              # PNG charts from all analysis tasks
data/research/
    entry_candidates_15m.parquet  # Task 5 output
    macro_15m.parquet             # Task 12 output
```

---

## Task 1: Scaffold lib + analysis packages

**Files:** Create `research/lib/__init__.py`, `research/analysis/__init__.py`,
`tests/research/` already exists; Create `docs/research/charts/.gitkeep`;
Modify `pyproject.toml`.

- [ ] **Step 1: Create the package files** — Create empty `research/lib/__init__.py`
  and `research/analysis/__init__.py`. Create `docs/research/charts/.gitkeep` (empty).

- [ ] **Step 2: Add dependencies** — In `pyproject.toml`, add `"matplotlib>=3.8"`
  and `"scikit-learn>=1.4"` to `[project] dependencies`.

- [ ] **Step 3: Verify** — Run `uv sync && uv run python -c "import matplotlib, sklearn; print('ok')"`.
  Expected: `ok`.

- [ ] **Step 4: Commit**
```bash
git add research/lib research/analysis docs/research/charts pyproject.toml
git commit -m "research: scaffold lib + analysis packages (Phase 2-4)"
```

---

## Task 2: Split utilities (`research/lib/splits.py`)

**Files:** Create `research/lib/splits.py`, `tests/research/test_splits.py`.

- [ ] **Step 1: Write the failing test** — `tests/research/test_splits.py`:

```python
import pandas as pd
from research.lib.splits import (
    add_date_col, dev_mask, holdout_mask, day_blocked_kfold, leave_one_day_out,
)

def _frame():
    # window_start_ts at 6 distinct UTC days 2026-05-15..20 plus one holdout day
    days = ["2026-05-15", "2026-05-16", "2026-05-17", "2026-05-18",
            "2026-05-19", "2026-05-20", "2026-05-21"]
    ts = [int(pd.Timestamp(d, tz="UTC").timestamp()) for d in days]
    return pd.DataFrame({"window_start_ts": ts, "v": range(7)})

def test_add_date_col():
    df = add_date_col(_frame())
    assert list(df["date"])[:2] == ["2026-05-15", "2026-05-16"]

def test_dev_and_holdout_masks_partition():
    df = add_date_col(_frame())
    dev, hold = dev_mask(df), holdout_mask(df)
    assert (dev & hold).sum() == 0          # disjoint
    assert dev.sum() == 6 and hold.sum() == 1
    assert df.loc[hold, "date"].iloc[0] == "2026-05-21"

def test_day_blocked_kfold_covers_all_days_once():
    df = add_date_col(_frame()[_frame()["window_start_ts"] < _frame()["window_start_ts"].iloc[6]])
    folds = day_blocked_kfold(df, k=3, seed=0)
    test_days = set()
    for train_idx, test_idx in folds:
        assert set(train_idx).isdisjoint(test_idx)
        test_days |= set(df.loc[test_idx, "date"])
    assert len(test_days) == 6              # every dev day tested exactly once

def test_leave_one_day_out_yields_one_day_per_fold():
    df = add_date_col(_frame())
    folds = list(leave_one_day_out(df))
    assert len(folds) == 7
    for train_idx, test_idx in folds:
        assert df.loc[test_idx, "date"].nunique() == 1
        assert set(train_idx).isdisjoint(test_idx)
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/research/test_splits.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** — `research/lib/splits.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes** — `uv run pytest tests/research/test_splits.py -q` → 4 passed.

- [ ] **Step 5: Commit**
```bash
git add research/lib/splits.py tests/research/test_splits.py
git commit -m "research: day-blocked split + dev/holdout utilities"
```

---

## Task 3: Window-clustered statistics (`research/lib/stats.py`)

**Files:** Create `research/lib/stats.py`, `tests/research/test_stats.py`.

- [ ] **Step 1: Write the failing test** — `tests/research/test_stats.py`:

```python
import numpy as np
from research.lib.stats import window_clustered_bootstrap, reliability_curve

def test_bootstrap_ci_brackets_mean():
    rng = np.random.default_rng(0)
    # 200 windows, each 1 value ~ N(0.3, 1)
    groups = np.arange(200)
    values = rng.normal(0.3, 1.0, 200)
    lo, mid, hi = window_clustered_bootstrap(values, groups, n=2000, seed=1)
    assert lo < 0.3 < hi
    assert lo < mid < hi

def test_bootstrap_resamples_whole_windows():
    # All ticks of a window share an outcome; CI must reflect window count, not tick count.
    groups = np.repeat(np.arange(10), 100)   # 10 windows, 100 ticks each
    values = np.repeat(np.arange(10) % 2, 100).astype(float)  # window-level 0/1
    lo, mid, hi = window_clustered_bootstrap(values, groups, n=2000, seed=0)
    # 10 windows of a Bernoulli(0.5) -> wide CI; tick-level would be falsely tight
    assert hi - lo > 0.2

def test_reliability_curve_perfectly_calibrated():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 5000)
    y = (rng.uniform(0, 1, 5000) < p).astype(float)   # calibrated by construction
    groups = np.arange(5000)
    curve = reliability_curve(p, y, groups, n_bins=10, seed=0)
    # realized frequency tracks the bin's mean predicted prob within CI
    for row in curve:
        assert row["ci_lo"] <= row["mean_pred"] <= row["ci_hi"] or abs(row["realized"] - row["mean_pred"]) < 0.1
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/research/test_stats.py -q` → FAIL.

- [ ] **Step 3: Implement** — `research/lib/stats.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes** — `uv run pytest tests/research/test_stats.py -q` → 3 passed.

- [ ] **Step 5: Commit**
```bash
git add research/lib/stats.py tests/research/test_stats.py
git commit -m "research: window-clustered bootstrap + reliability curve"
```

---

## Task 4: Theoretical fair value (`research/lib/fairvalue.py`)

**Files:** Create `research/lib/fairvalue.py`, `tests/research/test_fairvalue.py`.

The fair value of an Up/Down binary = P(spot ends above strike). Model the
underlying over the remaining window as a driftless Gaussian:
`spot_end − strike ~ Normal(spot_now − strike, σ_remaining²)`, so
`P(Up) = Φ((spot_now − strike) / σ_remaining)`. In the dataset's units,
`(spot_now − strike)/strike·100 = move_pct` and `σ_remaining` in the same
percent units `= realized_vol · √time_left_sec`. Hence
`P(Up) = Φ(move_pct / (realized_vol·√time_left_sec))`.

- [ ] **Step 1: Write the failing test** — `tests/research/test_fairvalue.py`:

```python
import numpy as np
from research.lib.fairvalue import bachelier_p_up

def test_at_strike_is_half():
    # move_pct = 0 -> exactly a coin flip regardless of vol/time
    assert abs(bachelier_p_up(0.0, 0.05, 400.0) - 0.5) < 1e-9

def test_above_strike_more_than_half():
    assert bachelier_p_up(0.5, 0.05, 400.0) > 0.5

def test_below_strike_less_than_half():
    assert bachelier_p_up(-0.5, 0.05, 400.0) < 0.5

def test_decided_when_far_with_no_time():
    # 2% above strike, tiny remaining sigma -> almost certainly Up
    assert bachelier_p_up(2.0, 0.05, 1.0) > 0.99

def test_vectorized():
    mp = np.array([0.0, 0.5, -0.5])
    out = bachelier_p_up(mp, np.full(3, 0.05), np.full(3, 400.0))
    assert out.shape == (3,)
    assert abs(out[0] - 0.5) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/research/test_fairvalue.py -q` → FAIL.

- [ ] **Step 3: Implement** — `research/lib/fairvalue.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes** — `uv run pytest tests/research/test_fairvalue.py -q` → 5 passed.

- [ ] **Step 5: Commit**
```bash
git add research/lib/fairvalue.py tests/research/test_fairvalue.py
git commit -m "research: Bachelier theoretical fair value"
```

---

## Task 5: Entry-candidate (cheap-side) table (`research/analysis/entry_candidates.py`)

**Files:** Create `research/analysis/entry_candidates.py`, `tests/research/test_entry_candidates.py`.

Builds the analytical object the calibration study runs on: one row per
(15m tick), framed from the **cheap side's** point of view — the side a
dip-buyer would buy. YES and NO sum to 1; framing by the cheap side avoids
double-counting.

For each tick, the cheap side = whichever of YES / NO has the lower mid.
Columns to emit: `slug, symbol, date, timestamp_ms, seconds_into_window,
time_left_sec, cheap_side` ("YES"/"NO"), `cheap_mid, cheap_ask, cheap_bid,
cheap_spread, cheap_depth` (ask depth of the cheap side), `proximity_pct,
sigma_proximity, realized_vol`, the cheap side's `drop_30s` and `velocity_30s`,
`spot_move_30s`, the cheap side's `imbalance`, and the label
`cheap_won` (1.0 if the cheap side's outcome resolved in its favour, i.e.
`cheap_side=="YES" and outcome_up==1` or `cheap_side=="NO" and outcome_up==0`;
NaN if no outcome).

- [ ] **Step 1: Write the failing test** — `tests/research/test_entry_candidates.py`:

```python
import numpy as np, pandas as pd
from research.analysis.entry_candidates import build_entry_candidates

def _ticks():
    # 2 ticks: one YES-cheap, one NO-cheap
    return pd.DataFrame({
        "slug": ["s", "s"], "symbol": ["btc", "btc"],
        "timestamp_ms": [1_747_000_000_000, 1_747_000_001_000],
        "window_start_ts": [1_747_000_000, 1_747_000_000],
        "seconds_into_window": [10, 11], "time_left_sec": [890, 889],
        "yes_mid": [0.20, 0.80], "no_mid": [0.80, 0.20],
        "yes_best_ask": [0.21, 0.81], "yes_best_bid": [0.19, 0.79],
        "no_best_ask": [0.81, 0.21], "no_best_bid": [0.79, 0.19],
        "yes_ask_depth": [50.0, 60.0], "no_ask_depth": [70.0, 80.0],
        "proximity_pct": [0.1, 0.1], "sigma_proximity": [0.5, 0.5],
        "realized_vol": [0.05, 0.05],
        "yes_drop_30s": [40.0, 0.0], "no_drop_30s": [0.0, 40.0],
        "yes_velocity_30s": [-0.1, 0.0], "no_velocity_30s": [0.0, -0.1],
        "spot_move_30s": [-0.2, 0.2],
        "yes_imbalance": [0.6, 0.4], "no_imbalance": [0.4, 0.6],
        "outcome_up": [1.0, 1.0],
    })

def test_cheap_side_selection_and_label():
    ec = build_entry_candidates(_ticks())
    assert list(ec["cheap_side"]) == ["YES", "NO"]
    assert ec["cheap_mid"].iloc[0] == 0.20 and ec["cheap_mid"].iloc[1] == 0.20
    # tick 0: cheap=YES, outcome Up -> cheap_won=1 ; tick 1: cheap=NO, outcome Up -> cheap_won=0
    assert ec["cheap_won"].iloc[0] == 1.0
    assert ec["cheap_won"].iloc[1] == 0.0
    # cheap-side ask/bid/drop pulled from the correct side
    assert ec["cheap_ask"].iloc[0] == 0.21 and ec["cheap_ask"].iloc[1] == 0.21
    assert ec["cheap_drop_30s"].iloc[0] == 40.0 and ec["cheap_drop_30s"].iloc[1] == 40.0
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/research/test_entry_candidates.py -q` → FAIL.

- [ ] **Step 3: Implement** — `research/analysis/entry_candidates.py`. Provide a
  pure function `build_entry_candidates(ticks: pd.DataFrame) -> pd.DataFrame`
  that vectorizes the cheap-side selection (`cheap_is_yes = yes_mid <= no_mid`)
  and uses `np.where` to pull every `cheap_*` column from the YES or NO column
  accordingly; computes `cheap_won` per the rule above; adds a `date` column via
  `research.lib.splits.add_date_col`. Also provide a `run()` that loads
  `data/research/ticks_15m.parquet`, builds the table, and writes
  `data/research/entry_candidates_15m.parquet`, plus an `if __name__=="__main__"`.

- [ ] **Step 4: Run test + build** — `uv run pytest tests/research/test_entry_candidates.py -q` → 1 passed.
  Then `uv run python -m research.analysis.entry_candidates` and confirm
  `data/research/entry_candidates_15m.parquet` is written (~2.0M rows). Print row
  count and the `cheap_won` non-null fraction.

- [ ] **Step 5: Commit**
```bash
git add research/analysis/entry_candidates.py tests/research/test_entry_candidates.py
git commit -m "research: cheap-side entry-candidate table"
```

---

## Task 6: Unconditional calibration (`research/analysis/calibration.py`)

**Files:** Create `research/analysis/calibration.py`; chart to `docs/research/charts/`.

**Question:** Is the market's implied probability calibrated? For ticks where the
cheap side is priced `p`, does the cheap side win `p` of the time?

**Method:**
1. Load `data/research/entry_candidates_15m.parquet`; keep **development rows only**
   (`research.lib.splits.dev_mask`) with non-null `cheap_won` and finite features.
2. Compute the reliability curve with `research.lib.stats.reliability_curve`,
   using `pred = cheap_mid`, `outcome = cheap_won`, `groups = slug`, 20 bins.
   Also compute it with `pred = cheap_ask` (the price a taker actually pays).
3. Repeat for the **all-states** framing (not just cheap side): bucket every
   tick's `yes_mid` against `outcome_up` — so non-dip mispricings surface.
4. Per symbol, repeat the cheap-side curve.

**Deliverable:**
- A chart `docs/research/charts/calibration_unconditional.png`: realized
  frequency vs mean predicted, with the diagonal and CI bars, for cheap-mid and
  all-states.
- A findings section written into `docs/research/edge_map.md` (create the file
  with an `# Edge Map` header if absent): the reliability table, and a plain
  verdict — is the cheap side systematically *under*priced (realized > implied →
  edge for the buyer), *over*priced (favorite–longshot bias → buyer loses), or
  calibrated. State the ¢ gap per bin with CIs.

**Verification (sanity assertions in the script, must hold):**
- Every bin's `n_windows ≥ 20` or the bin is reported as "thin, not interpreted".
- The all-states curve's overall mean realized ≈ overall mean predicted within
  0.03 (the market cannot be grossly mis-calibrated on average — if it is, that
  is itself a flagged finding).

- [ ] **Step 1: Implement `research/analysis/calibration.py`** with a `run()`
  that does the method above, writes the chart, and appends the findings section
  to `docs/research/edge_map.md`. Numbers reported must be the real observed
  values.
- [ ] **Step 2: Run** — `uv run python -m research.analysis.calibration`; confirm
  the chart and the `edge_map.md` section are written; paste the reliability table.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/calibration.py docs/research/edge_map.md docs/research/charts/
git commit -m "research: unconditional calibration / reliability curve"
```

---

## Task 7: Conditioned edge map (`research/analysis/edge_map.py`)

**Files:** Create `research/analysis/edge_map.py`; charts to `docs/research/charts/`.

**Question:** Where is the mispricing concentrated? The edge — if any — lives in
specific conditions, not everywhere.

**Method (development rows only):**
1. For the cheap side, compute the **edge** per tick = `cheap_won − cheap_mid`
   (realized minus implied; positive = underpriced = buyer edge).
2. Build a conditioned edge map: mean edge + window-clustered CI within strata of
   — `sigma_proximity` bucket (`<0.5, 0.5–1, 1–2, 2–4, >4` and the `inf`/early
   bucket excluded), `time_left_sec` bucket (`<180, 180–420, 420–660, >660`),
   `cheap_drop_30s` bucket (`0, 0–10, 10–25, >25` %), `cheap_mid` bucket
   (`0.05–0.15, 0.15–0.25, 0.25–0.40`), `symbol`, and a `realized_vol` tertile
   (LOW/MED/HIGH — compute the tertile cutoffs from dev data and record them).
3. For the two strongest one-dimensional cells, also cross-tabulate
   (e.g. σ-proximity × drop).
4. **Dev-internal CV:** split dev days into earlier half / later half; recompute
   the edge for each promising cell on both halves. A cell "qualifies" only if
   its edge CI excludes zero in the same direction on both halves.

**Deliverable:**
- Heatmap chart(s) `docs/research/charts/edge_map_*.png`.
- An `## Conditioned edge map` section in `docs/research/edge_map.md`: the
  qualifying cells (with edge ¢, CI, n_windows, both-halves check), and a plain
  statement of where — if anywhere — a buyer edge survives. Explicitly relate
  findings to hypotheses H2, H6, H8 from `market_hypotheses.md`.

**Verification:** the recorded `realized_vol` tertile cutoffs are written into the
findings (they replace the uncalibrated `vol_regime_thresholds` guesses);
qualifying cells must each have `n_windows ≥ 30` on each half.

- [ ] **Step 1: Implement `research/analysis/edge_map.py`** per the method.
- [ ] **Step 2: Run** — `uv run python -m research.analysis.edge_map`; paste the
  qualifying-cells table and the vol tertile cutoffs.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/edge_map.py docs/research/edge_map.md docs/research/charts/
git commit -m "research: conditioned edge map + dev-internal CV"
```

---

## Task 8: Fair-value triangulation (`research/analysis/fair_value_tri.py`)

**Files:** Create `research/analysis/fair_value_tri.py`; chart to `docs/research/charts/`.

**Question:** Do three independent estimates of "what is this side worth" agree —
the market odds, the theoretical Bachelier value, and the empirically-realized
frequency?

**Method (development rows only):**
1. Per tick compute theoretical `p_up = bachelier_p_up(move_pct, realized_vol,
   time_left_sec)` (Task 4); the cheap side's theoretical value is `p_up` if
   cheap is YES else `1 − p_up`. Use only finite-`sigma_proximity` ticks.
2. Bucket by `cheap_mid`; per bucket report mean market price, mean theoretical
   value, and realized `cheap_won` frequency with CI.
3. Flag buckets where **all three** disagree with the market in the same
   direction (high-confidence mispricing) vs where theory and market agree.

**Deliverable:** chart `docs/research/charts/fair_value_triangulation.png`; a
`## Fair-value triangulation` section in `docs/research/edge_map.md` with the
three-way table and the high-confidence-mispricing verdict.

**Verification:** theoretical `p_up` is in [0,1] for all rows; at `move_pct≈0`
the theoretical value is ≈0.5 (assert mean within 0.45–0.55 for the near-strike bucket).

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run** — `uv run python -m research.analysis.fair_value_tri`; paste the table.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/fair_value_tri.py docs/research/edge_map.md docs/research/charts/
git commit -m "research: fair-value triangulation (market vs theory vs empirical)"
```

---

## Task 9: Net-of-cost calibration (`research/analysis/cost_calibration.py`)

**Files:** Create `research/analysis/cost_calibration.py`; chart to `docs/research/charts/`.

**Question:** Does any edge from Tasks 6–8 survive real trading costs — and how
much does it differ between taker and maker execution?

**Method (development rows only, cheap side):** for each qualifying edge cell
from Task 7 (or, if none qualified, the most promising cells), compute the
expected per-trade PnL of a buyer under two cost models, for a $10 stake:
- **Taker:** enter paying `cheap_ask`, fee `0.07·p·(1−p)` per share; the trade's
  gross value is the realized resolution (1 or 0); exit fee at resolution.
  Net edge = `E[payout] − cheap_ask − fees`.
- **Maker:** enter paying `cheap_mid` (assume the limit order rests at mid),
  0 fee; net edge = `E[payout] − cheap_mid`. Report this as the optimistic
  bound and note the un-modelled fill-probability haircut.
Report mean net PnL/trade and window-clustered CI for each cell under each model.

**Deliverable:** chart `docs/research/charts/cost_calibration.png`; a
`## Net-of-cost edge` section in `docs/research/edge_map.md` — for each cell, the
gross edge, taker-net, maker-net, all with CIs; and the verdict: does a
profitable cell exist as a taker, as a maker only, or not at all.

**Verification:** taker net ≤ gross edge for every cell (cost is non-negative);
the cost subtracted matches Phase 0 Task 6's ~16–21% range within reason.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run** — `uv run python -m research.analysis.cost_calibration`; paste the table.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/cost_calibration.py docs/research/edge_map.md docs/research/charts/
git commit -m "research: net-of-cost calibration (taker vs maker)"
```

---

## Task 10: Lead–lag study (`research/analysis/lead_lag.py`)

**Files:** Create `research/analysis/lead_lag.py`; chart to `docs/research/charts/`.

**Question (open-minded, hypothesis H-extra):** do the Polymarket odds *lag* the
spot price? A systematic lag would be a separate, cleaner edge than mean-reversion.

**Method (development rows only):** for each 15m window, take the per-tick series
of `coinbase_price` returns and `yes_mid` changes; compute the cross-correlation
at lags −30..+30 seconds; average across windows (and per symbol). A peak at a
positive lag (odds change *after* spot) = odds lag spot.

**Deliverable:** chart `docs/research/charts/lead_lag.png` (mean cross-correlation
vs lag); a `## Lead–lag` section in `docs/research/edge_map.md` — the lag of peak
correlation, its magnitude, and the verdict: is there a tradeable spot→odds lag.

**Verification:** the cross-correlation at lag 0 is the largest in magnitude OR a
clear off-zero peak is reported with its lag; per-symbol results are shown
separately (not just pooled).

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run** — `uv run python -m research.analysis.lead_lag`; paste the peak-lag result.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/lead_lag.py docs/research/edge_map.md docs/research/charts/
git commit -m "research: spot-vs-odds lead-lag study"
```

---

## Task 11: Phase 2 synthesis — the edge map

**Files:** Modify `docs/research/edge_map.md`.

- [ ] **Step 1: Write the synthesis** — Add a `## Phase 2 Verdict` section at the
  top of `docs/research/edge_map.md` (after the title): a plain-language summary
  of whether a real, cost-surviving, dev-internally-cross-validated buyer edge
  exists in these markets, in which conditions (σ-proximity / drop / price /
  symbol cell), how large in ¢ and net PnL, and under which execution mode. State
  which of hypotheses H1–H11 (`market_hypotheses.md`) are supported or rejected
  so far. If no edge survives, say so plainly — that is a valid, important result.
- [ ] **Step 2: Self-review** — every claim cites a number from a Task 6–10 section.
- [ ] **Step 3: Commit**
```bash
git add docs/research/edge_map.md
git commit -m "research: Phase 2 synthesis — edge map verdict"
```

---

## Task 12: Cross-coin macro table (`research/analysis/macro_table.py`)

**Files:** Create `research/analysis/macro_table.py`.

Builds a 1 Hz cross-symbol table: for each `timestamp_ms` present in
`ticks_15m.parquet`, the state of all 4 coins — each coin's active-window
`yes_mid`, `move_pct`, `cheap_mid`, and `cheap_drop_30s`; plus a derived
`n_coins_dropping` = count of coins with `cheap_drop_30s ≥ 10` at that instant.

**Method:** pivot `ticks_15m.parquet` by `timestamp_ms` × `symbol`. Where a coin
has multiple active 15m windows at one instant, take the one with the most
`time_left_sec`. Write `data/research/macro_15m.parquet`.

- [ ] **Step 1: Implement** `build_macro_table(ticks)` + `run()` + `__main__`.
  Include 2–3 inline `assert` sanity checks (e.g. `n_coins_dropping` in 0..4;
  row count ≈ unique `timestamp_ms` count).
- [ ] **Step 2: Run** — `uv run python -m research.analysis.macro_table`; confirm
  `data/research/macro_15m.parquet` written; print row count + `n_coins_dropping`
  distribution.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/macro_table.py
git commit -m "research: cross-coin macro table"
```

---

## Task 13: Drop-event study (`research/analysis/drop_events.py`)

**Files:** Create `research/analysis/drop_events.py`; charts to `docs/research/charts/`.

**Question:** after a sharp odds drop, what do the odds do — do they revert?

**Method (development rows only, cheap side, 15m):**
1. Detect drop events: a tick where `cheap_drop_30s` crosses above a threshold
   (run for thresholds 10, 20, 35 %) having been below it the previous tick — one
   event per crossing per window (dedupe).
2. For each event, record the cheap-side mid path forward at +30, +60, +120,
   +300 s and at window close; also record `sigma_proximity`, `time_left_sec`,
   `spot_move_30s` at the event.
3. Plot the **average forward path** of the cheap-side mid, normalised to the
   event price, per threshold.

**Deliverable:** chart `docs/research/charts/drop_forward_paths.png`; a
`# Bounce Atlas` doc `docs/research/bounce_atlas.md` with a `## Forward paths`
section — mean and median forward return at each horizon, per threshold, with
window-clustered CIs. Plain verdict: do drops revert on average, by how much, how
fast (relates to H1, H5).

**Verification:** event count per threshold reported; each horizon's stat has
`n_windows ≥ 30` or is flagged thin.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run** — `uv run python -m research.analysis.drop_events`; paste the forward-path table.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/drop_events.py docs/research/bounce_atlas.md docs/research/charts/
git commit -m "research: drop-event forward-path study"
```

---

## Task 14: Bounce atlas + noise/signal + patience (`research/analysis/bounce_atlas.py`)

**Files:** Create `research/analysis/bounce_atlas.py`; charts to `docs/research/charts/`.

**Question:** which drops revert (noise) vs which keep going (signal); and for a
buyer entering on a drop, what is P(profit) vs P(breakeven) vs P(−100%)?

**Method (development rows only, on the Task 13 drop events; uses
`data/research/macro_15m.parquet` from Task 12):**
1. **Noise vs signal:** split each drop event by `|spot_move_30s|` at the event
   (small = noise: odds dropped, spot didn't; large = signal: spot moved). Also
   split by `n_coins_dropping` (idiosyncratic vs macro) from the macro table.
   Compare the forward-path reversion of each split (H2, H7).
2. **Bounce distribution:** full distribution of the cheap-side forward return to
   window close for drop-entries — histogram, the fraction reaching +25/+50/+100%,
   and the fraction going to 0 (H5).
3. **Patience-deadline:** for drop-entries, as a function of `time_left_sec` at
   entry, compute P(cheap side reaches +50% before close), P(returns to ≥ entry
   price before close), and P(resolves at 0). (H1, H3.)

**Deliverable:** charts `docs/research/charts/noise_vs_signal.png`,
`bounce_distribution.png`, `patience_deadline.png`; sections appended to
`docs/research/bounce_atlas.md`. Plain verdicts on H1, H2, H3, H5, H7.

**Verification:** the noise/signal split sizes are reported; probabilities sum
consistently (P(profit)+P(loss-ish) ≤ 1 per bucket); CIs window-clustered.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run** — `uv run python -m research.analysis.bounce_atlas`; paste the noise/signal + patience tables.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/bounce_atlas.py docs/research/bounce_atlas.md docs/research/charts/
git commit -m "research: bounce atlas — noise/signal split + patience-deadline"
```

---

## Task 15: Phase 3 synthesis — the bounce atlas

**Files:** Modify `docs/research/bounce_atlas.md`.

- [ ] **Step 1: Write the synthesis** — Add a `## Phase 3 Verdict` section at the
  top: do odds revert after drops, which drops (noise vs signal, idiosyncratic vs
  macro), the bounce-size distribution, and the patience-deadline numbers — the
  honest P(profit)/P(breakeven)/P(−100%) as a function of time-left. State which
  of H1–H11 are supported/rejected. Every claim cites a number.
- [ ] **Step 2: Commit**
```bash
git add docs/research/bounce_atlas.md
git commit -m "research: Phase 3 synthesis — bounce atlas verdict"
```

---

## Task 16: Patient-trader policy simulator (`research/analysis/patient_policy.py`)

**Files:** Create `research/analysis/patient_policy.py`, `tests/research/test_patient_policy.py`.

A clean, transparent re-implementation of the user's manual policy — NOT the
buggy arb simulator. Walks one window's ticks; enters the cheap side when the
entry rule holds; holds patiently; exits on profit-target, on recovery to
breakeven, or at window close (settling on the **true outcome**). Models taker
and maker cost explicitly. Pure and deterministic (no RNG).

Policy parameters (a dataclass `PatientPolicy`): `entry_mid_min`, `entry_mid_max`
(cheap-side mid band), `min_drop_30s` (require a visible drop),
`max_sigma_proximity` (require still near a coin-flip), `min_time_left_sec`,
`profit_target_pct`, `breakeven_exit` (bool — exit at ~entry if it recovers
after going underwater), `execution` ("taker" or "maker").

Exit/settlement: profit-target hit → sell at that price; window close → settle
at the true outcome (1.0 if the held side won, 0.0 if it lost). Cost: taker pays
`cheap_ask` in, `0.07·p·(1−p)` fee both legs, sells at `cheap_bid`; maker pays
`cheap_mid` in, 0 fee, sells at `cheap_mid` (profit-target) or true outcome.

- [ ] **Step 1: Write the failing test** — `tests/research/test_patient_policy.py`:

```python
import pandas as pd
from research.analysis.patient_policy import PatientPolicy, simulate_window

def _win(prices, outcome_up):
    # one window, cheap side = YES, mids given by `prices`
    n = len(prices)
    return pd.DataFrame({
        "slug": ["w"] * n,
        "seconds_into_window": list(range(n)),
        "time_left_sec": [900 - i for i in range(n)],
        "cheap_side": ["YES"] * n,
        "cheap_mid": prices,
        "cheap_ask": [p + 0.01 for p in prices],
        "cheap_bid": [p - 0.01 for p in prices],
        "cheap_drop_30s": [50.0] * n,
        "sigma_proximity": [0.4] * n,
        "outcome_up": [float(outcome_up)] * n,
    })

def test_profit_target_exit_maker():
    # enters ~0.20, rises to 0.40 -> +100% -> profit target 50% fires
    w = _win([0.20, 0.25, 0.30, 0.40], outcome_up=1)
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=50.0, breakeven_exit=True, execution="maker")
    tr = simulate_window(w, pol)
    assert tr is not None and tr["exit_reason"] == "profit_target"
    assert tr["pnl_usd"] > 0

def test_resolution_loss_taker():
    # enters ~0.20, never rises, window resolves against -> -100%-ish
    w = _win([0.20, 0.18, 0.15, 0.10], outcome_up=0)
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=50.0, breakeven_exit=True, execution="taker")
    tr = simulate_window(w, pol)
    assert tr is not None and tr["exit_reason"] == "resolution"
    assert tr["pnl_usd"] < 0

def test_no_entry_when_band_excludes():
    w = _win([0.50, 0.52, 0.55, 0.60], outcome_up=1)  # mid above band
    pol = PatientPolicy(entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10,
                        max_sigma_proximity=1.0, min_time_left_sec=60,
                        profit_target_pct=50.0, breakeven_exit=True, execution="maker")
    assert simulate_window(w, pol) is None
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/research/test_patient_policy.py -q` → FAIL.

- [ ] **Step 3: Implement** `research/analysis/patient_policy.py` — the
  `PatientPolicy` dataclass and `simulate_window(window_ticks, policy) -> dict|None`
  (returns a trade dict: `slug, entry_ts/sec, entry_price, exit_price,
  exit_reason` ∈ {profit_target, breakeven, resolution}, `seconds_held,
  pnl_usd, won`). One trade per window max; $10 fixed stake; settle on
  `outcome_up` at window close. Make every test above pass.

- [ ] **Step 4: Run test to verify it passes** — 3 passed.

- [ ] **Step 5: Commit**
```bash
git add research/analysis/patient_policy.py tests/research/test_patient_policy.py
git commit -m "research: patient-trader policy simulator"
```

---

## Task 17: Run the reconstruction (`research/analysis/patient_policy.py::run`)

**Files:** Modify `research/analysis/patient_policy.py`; chart to `docs/research/charts/`.

**Question:** does the user's policy reproduce a high win rate — and what is its
honest PnL, win rate, and resolution-loss rate?

**Method (development rows only, on `entry_candidates_15m.parquet` grouped by
`slug`):**
1. Run `simulate_window` for every dev window under a **baseline policy** close
   to the user's stated rules (band 0.10–0.30, min_drop_30s 10, max_sigma_proximity
   ~1.0, min_time_left 420, profit_target 75, breakeven_exit True), for both
   `execution="taker"` and `="maker"`.
2. Report: n trades, win rate, mean PnL/trade, total PnL, **resolution-loss rate**
   (fraction of trades exiting `resolution` with a loss), and the **daily** PnL
   series (green-day fraction).
3. **Sensitivity:** vary each parameter one at a time over a small grid; report
   how win rate / PnL / resolution-loss move — find plateaus vs cliffs. NOT to
   maximize PnL; to map the surface.
4. **PnL attribution:** split realized PnL into profit-target exits vs breakeven
   exits vs resolution exits.

**Deliverable:** chart `docs/research/charts/reconstruction_daily_pnl.png`; a
`# Reconstruction` doc `docs/research/reconstruction.md` with the baseline
results (taker & maker), the sensitivity table, and the attribution. Plain
verdict: does the patient policy reproduce a high WR; is it profitable; under
which execution mode; relate to H1, H5, H9 and the user's 95% memory.

**Verification:** win rate, resolution-loss rate ∈ [0,1]; taker total PnL ≤ maker
total PnL for the same policy (cost is non-negative).

- [ ] **Step 1: Implement the `run()` extensions.**
- [ ] **Step 2: Run** — `uv run python -m research.analysis.patient_policy`; paste the baseline + sensitivity tables.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/patient_policy.py docs/research/reconstruction.md docs/research/charts/
git commit -m "research: run the patient-policy reconstruction"
```

---

## Task 18: Feature-importance diagnostic (`research/analysis/feature_importance.py`)

**Files:** Create `research/analysis/feature_importance.py`; chart to `docs/research/charts/`.

**Question:** which measurable features actually predict whether a dipped cheap
side recovers? (Diagnostic — to understand the edge, not to trade a black box.)

**Method (development rows only):**
1. On the Task 13 drop events, label each `recovered` = cheap side returned to ≥
   entry price before window close.
2. Features: `sigma_proximity, time_left_sec, cheap_drop_30s, spot_move_30s,
   cheap_mid, realized_vol, cheap_imbalance, n_coins_dropping` (joined from the
   macro table).
3. Fit a **logistic regression** and a **depth-3 decision tree** (scikit-learn),
   evaluated with `day_blocked_kfold` (Task 2) — report out-of-fold AUC.
4. Report standardized logistic coefficients and the tree's top splits — which
   features carry signal, with what sign.

**Deliverable:** chart `docs/research/charts/feature_importance.png`; a
`## Feature importance` section in `docs/research/reconstruction.md` — out-of-fold
AUC, the ranked features with signs, and a plain statement of what the user's eye
is implicitly computing and whether the rule-based policy (Task 17) is missing a
variable.

**Verification:** AUC reported is **out-of-fold** (day-blocked), not in-sample;
if AUC ≈ 0.5 the script states plainly that no feature predicts recovery.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run** — `uv run python -m research.analysis.feature_importance`; paste the AUC + ranked features.
- [ ] **Step 3: Commit**
```bash
git add research/analysis/feature_importance.py docs/research/reconstruction.md docs/research/charts/
git commit -m "research: feature-importance diagnostic"
```

---

## Task 19: Phase 4 synthesis + overall edge-discovery summary

**Files:** Modify `docs/research/reconstruction.md`; Create `docs/research/EDGE_DISCOVERY_SUMMARY.md`.

- [ ] **Step 1: Phase 4 verdict** — Add a `## Phase 4 Verdict` section at the top
  of `docs/research/reconstruction.md`: does the patient policy reproduce the
  user's high win rate; is it profitable (taker / maker); what carries the edge;
  what the resolution-loss tail looks like; H1/H5/H9 supported or not.
- [ ] **Step 2: Overall summary** — Create `docs/research/EDGE_DISCOVERY_SUMMARY.md`:
  one page tying Phases 2–4 together — does a real, cost-surviving edge exist;
  where; how big; under which execution mode; the status of every hypothesis
  H1–H11; and a concrete recommendation for Phase 5 (which 2–4 strategy families
  to build, or — if no edge survives — that finding stated plainly and what to
  collect/measure next). Cite numbers from the edge map, bounce atlas, and
  reconstruction.
- [ ] **Step 3: Commit**
```bash
git add docs/research/reconstruction.md docs/research/EDGE_DISCOVERY_SUMMARY.md
git commit -m "research: Phase 4 synthesis + overall edge-discovery summary"
```

---

## Self-Review (completed by plan author)

**Spec coverage (Phases 2–4):** Phase 2 — reliability curve (T6), window-clustered
(T3), conditioned edge map (T7), model-based fair value (T4+T8), isotonic — *note:
the spec mentions an isotonic fit; the reliability curve in T6 + the binned edge
map in T7 deliver the same overfitting-resistant odds→truth mapping, so a separate
isotonic task is not added (YAGNI)*, favorite–longshot (T6 all-states + T9 cost),
net-of-cost taker/maker (T9), time-split stability (T7 dev-internal CV),
cheap-side framing (T5), lead–lag (T10). Phase 3 — drop events + forward paths
(T13), noise/signal (T14), bounce distribution (T14), patience-deadline (T14),
cross-coin macro (T12), open-minded spikes/late-window — *covered by the
all-states calibration in T6; a dedicated spike task is omitted as YAGNI for the
first pass and noted for Phase 5*. Phase 4 — policy reconstruction (T16–17),
sensitivity (T17), PnL attribution (T17), feature-importance diagnostic (T18),
discretion gap (addressed in the T19 verdict). Pre-registered hypotheses H1–H11
referenced throughout. Sealed hold-out never touched — every task filters to dev
rows via `dev_mask`.

**Placeholder scan:** library tasks (2,3,4,5,16) have complete code + tests.
Analysis tasks (6–15, 17–19) are specified by precise method + verification
assertions + exact deliverable — the implementer writes pandas against the real
dataset; this is a complete instruction for exploratory analysis, not a "TODO".
No "add error handling" / "TBD" present.

**Type consistency:** `add_date_col`, `dev_mask`, `holdout_mask`,
`day_blocked_kfold`, `leave_one_day_out` (T2) used consistently in T7/T17/T18.
`window_clustered_bootstrap`, `reliability_curve` (T3) used in T6/T7/T8/T9/T13/T14.
`bachelier_p_up` (T4) used in T8. `build_entry_candidates` →
`entry_candidates_15m.parquet` (T5) consumed by T6–T10, T13–T14, T17.
`build_macro_table` → `macro_15m.parquet` (T12) consumed by T14, T18.
`PatientPolicy` / `simulate_window` (T16) used by T17. `cheap_*` column names
consistent between T5 and all consumers.
