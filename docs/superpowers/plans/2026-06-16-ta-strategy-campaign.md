# TA Strategy Campaign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find new paper strategies by adding a causal base-asset TA feature layer and four TA hypothesis families to the existing `hypothesis_sweep → select → verify → atlas` rigor stack, then deploy clean survivors as paper twins.

**Architecture:** A new `research/dataset/ta_features.py` computes causal TA indicators on the per-second Coinbase spot tape already in the joined frame. A lazy `_ta_frame()` in `hypothesis_sweep.py` augments `load_base()` with those columns (mirroring the existing `_psettle_frame()` pattern, so the default pipeline stays byte-identical). Four `fam_ta_*` builders + grid specs feed the unchanged select/verify/atlas gauntlet on clean data (FUTURE_START ≥ 2026-06-12) with Chainlink settlement and Jaccard dedup.

**Tech Stack:** Python, pandas, numpy, pytest, uv. Spec: `docs/superpowers/specs/2026-06-16-ta-strategy-campaign-design.md`.

---

## File Structure

- **Create** `research/dataset/ta_features.py` — causal TA indicators from `cb_spot`; `build_ta_features(df)` + a CLI to (re)build `data/research/ta_features.parquet`. One responsibility: turn the spot tape into TA columns.
- **Create** `tests/research/test_ta_features.py` — hand-computed fixtures + causality assertions.
- **Modify** `research/analysis/hypothesis_sweep.py` — add `_ta_frame()` lazy cache, four `fam_ta_*` builders, `RATIONALE` entries, `BUILDERS` registration, and TA grids in `gen_specs()`.
- **Create** `tests/research/test_ta_families.py` — builder parity (research-arithmetic) + dedup-shape tests.
- **Create** `docs/research/TA_STRATEGIES_2026-06-16.md` — campaign deliverable (every family's verdict, survivors + honest negatives).
- **Modify** `strategies.yaml` — ONLY if a survivor maps to an existing engine mode (conditional; most TA features need engine wiring → flagged in the doc, not deployed).
- **Modify** `STATE.md` — dated session entry at the end.

---

## Task 1: Causal TA feature module

**Files:**
- Create: `research/dataset/ta_features.py`
- Test: `tests/research/test_ta_features.py`

The module turns a frame with `["slug", "seconds_into_window", "cb_spot"]` into one TA row per `(slug, seconds_into_window)`. **Every indicator is causal**: grouped by `slug`, ordered by `seconds_into_window`, computed with `ewm`/`rolling` that never read a future row. No `center=True`, no shifting backward.

Columns produced (prefix `ta_`): `ta_ema_slope` (per-second slope of EMA(span=30) in bps), `ta_ma_cross` (sign of EMA(10) − EMA(60), in {−1,0,1}), `ta_rsi` (Wilder RSI, period 14, on 1s returns, 0–100), `ta_macd_hist` (EMA12−EMA26 minus its EMA9 signal), `ta_ret_30s` (return over last 30s, bps), `ta_atr` (mean abs 1s change over 14s, bps), `ta_boll_width` (rolling std(20) / rolling mean(20), in bps), `ta_z_vwap` (z-score of `cb_spot` vs rolling mean(60), in std units), `ta_regime` (string: `trend` if `abs(ta_z_vwap) >= 1.5` and `sign(ta_ema_slope)==sign(ta_z_vwap)`, `highvol` if `ta_boll_width >= 8` bps, else `range`).

- [ ] **Step 1: Write the failing test**

```python
# tests/research/test_ta_features.py
import numpy as np
import pandas as pd
import pytest
from research.dataset.ta_features import build_ta_features


def _window(slug, prices):
    return pd.DataFrame({
        "slug": slug,
        "seconds_into_window": np.arange(len(prices)),
        "cb_spot": np.asarray(prices, dtype="f8"),
    })


def test_rising_series_is_trend_up():
    # strictly rising spot -> positive EMA slope, RSI high, regime trend
    df = _window("btc-1", 100.0 + np.arange(120) * 0.5)
    out = build_ta_features(df).set_index("seconds_into_window")
    last = out.loc[119]
    assert last["ta_ema_slope"] > 0
    assert last["ta_ma_cross"] == 1
    assert last["ta_rsi"] > 70
    assert last["ta_regime"] == "trend"


def test_flat_series_is_range():
    df = _window("btc-2", np.full(120, 100.0))
    out = build_ta_features(df).set_index("seconds_into_window")
    last = out.loc[119]
    assert abs(last["ta_ema_slope"]) < 1e-6
    assert abs(last["ta_z_vwap"]) < 1e-6
    assert last["ta_boll_width"] < 1.0
    assert last["ta_regime"] == "range"


def test_features_are_causal():
    # a feature value at sec t must NOT change when later rows are appended
    full = _window("btc-3", 100.0 + np.sin(np.arange(120) / 5.0))
    truncated = full.iloc[:60].copy()
    a = build_ta_features(full).set_index("seconds_into_window").loc[59]
    b = build_ta_features(truncated).set_index("seconds_into_window").loc[59]
    for col in ["ta_ema_slope", "ta_rsi", "ta_macd_hist", "ta_z_vwap", "ta_atr"]:
        assert a[col] == pytest.approx(b[col], rel=1e-9, nan_ok=True), col


def test_per_slug_isolation():
    # two slugs in one frame must not bleed across the groupby boundary
    df = pd.concat([
        _window("a", 100.0 + np.arange(80) * 0.5),     # rising
        _window("b", 100.0 - np.arange(80) * 0.5),     # falling
    ], ignore_index=True)
    out = build_ta_features(df)
    a = out[out["slug"] == "a"].set_index("seconds_into_window").loc[79]
    b = out[out["slug"] == "b"].set_index("seconds_into_window").loc[79]
    assert a["ta_ma_cross"] == 1 and b["ta_ma_cross"] == -1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/research/test_ta_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research.dataset.ta_features'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# research/dataset/ta_features.py
"""Causal base-asset TA features from the per-second Coinbase spot tape.

Input: a frame with ["slug", "seconds_into_window", "cb_spot"] (the joined/slim
base frame already carries cb_spot). Output: one TA row per (slug,
seconds_into_window). EVERY indicator is causal — grouped by slug, ordered by
seconds_into_window, computed with ewm/rolling that never read a future row.
This project has been burned by look-ahead twice; causality is enforced here once.

Run:  uv run python -m research.dataset.ta_features   # rebuilds the parquet
Out:  data/research/ta_features.parquet
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

OUT = os.path.join("data", "research", "ta_features", "ta_features.parquet")
TA_COLS = [
    "ta_ema_slope", "ta_ma_cross", "ta_rsi", "ta_macd_hist", "ta_ret_30s",
    "ta_atr", "ta_boll_width", "ta_z_vwap", "ta_regime",
]


def _rsi(ret: pd.Series, period: int = 14) -> pd.Series:
    up = ret.clip(lower=0.0)
    dn = (-ret).clip(lower=0.0)
    roll_up = up.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()
    roll_dn = dn.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()
    rs = roll_up / roll_dn.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.fillna(50.0)


def _features_one(g: pd.DataFrame) -> pd.DataFrame:
    s = g["cb_spot"].astype("f8")
    bps = 1e4 / s.clip(lower=1e-9)                 # 1 price unit -> bps of price
    ema30 = s.ewm(span=30, adjust=False, min_periods=1).mean()
    ema10 = s.ewm(span=10, adjust=False, min_periods=1).mean()
    ema60 = s.ewm(span=60, adjust=False, min_periods=1).mean()
    ema12 = s.ewm(span=12, adjust=False, min_periods=1).mean()
    ema26 = s.ewm(span=26, adjust=False, min_periods=1).mean()
    macd = ema12 - ema26
    macd_sig = macd.ewm(span=9, adjust=False, min_periods=1).mean()
    ret = s.diff().fillna(0.0)
    mean60 = s.rolling(60, min_periods=1).mean()
    std60 = s.rolling(60, min_periods=1).std().fillna(0.0)
    mean20 = s.rolling(20, min_periods=1).mean()
    std20 = s.rolling(20, min_periods=1).std().fillna(0.0)

    ema_slope = (ema30.diff().fillna(0.0)) * bps
    ma_cross = np.sign((ema10 - ema60).to_numpy()).astype("i8")
    z_vwap = ((s - mean60) / std60.replace(0.0, np.nan)).fillna(0.0)
    boll_width = ((std20 / mean20.clip(lower=1e-9)) * 1e4)            # bps
    atr = ret.abs().rolling(14, min_periods=1).mean() * bps
    ret_30s = (s - s.shift(30)).fillna(0.0) * bps

    out = pd.DataFrame({
        "slug": g["slug"].to_numpy(),
        "seconds_into_window": g["seconds_into_window"].to_numpy(),
        "ta_ema_slope": ema_slope.to_numpy(),
        "ta_ma_cross": ma_cross,
        "ta_rsi": _rsi(ret).to_numpy(),
        "ta_macd_hist": (macd - macd_sig).to_numpy(),
        "ta_ret_30s": ret_30s.to_numpy(),
        "ta_atr": atr.to_numpy(),
        "ta_boll_width": boll_width.to_numpy(),
        "ta_z_vwap": z_vwap.to_numpy(),
    })
    trend = (out["ta_z_vwap"].abs() >= 1.5) & (
        np.sign(out["ta_ema_slope"]) == np.sign(out["ta_z_vwap"]))
    highvol = out["ta_boll_width"] >= 8.0
    out["ta_regime"] = np.where(trend, "trend",
                                np.where(highvol, "highvol", "range"))
    return out


def build_ta_features(df: pd.DataFrame) -> pd.DataFrame:
    """Causal TA columns, one row per (slug, seconds_into_window)."""
    need = ["slug", "seconds_into_window", "cb_spot"]
    g = (df[need].dropna(subset=["cb_spot"])
         .drop_duplicates(["slug", "seconds_into_window"])
         .sort_values(["slug", "seconds_into_window"]))
    parts = [_features_one(grp) for _, grp in g.groupby("slug", sort=False)]
    if not parts:
        return pd.DataFrame(columns=need[:2] + TA_COLS)
    return pd.concat(parts, ignore_index=True)


def main() -> str:
    from research.analysis.edge_lab import load_base
    out = build_ta_features(load_base())
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out.to_parquet(OUT, index=False)
    print(f"wrote {len(out):,} rows -> {OUT}")
    return OUT


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/research/test_ta_features.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add research/dataset/ta_features.py tests/research/test_ta_features.py
git commit -m "feat(ta): causal base-asset TA feature module"
```

---

## Task 2: Wire a lazy TA-augmented base frame into the sweep

**Files:**
- Modify: `research/analysis/hypothesis_sweep.py` (near the `_PSETTLE`/`_psettle_frame` block, ~line 281)
- Test: `tests/research/test_ta_families.py`

Mirror the `_psettle_frame()` pattern: a module-level lazy cache that augments `load_base()` with TA columns ONCE, applying the campaign future-override so TA candidates carry the same split labels as everything else. Default (non-TA) families are untouched, so the existing pipeline stays byte-identical.

- [ ] **Step 1: Write the failing test**

```python
# tests/research/test_ta_families.py
import numpy as np
import pandas as pd
import pytest
import research.analysis.hypothesis_sweep as hs


def _fake_base():
    # two windows, each 120s; one trending up (fav up), one flat
    rows = []
    for slug, base, drift in [("btc-up", 100.0, 0.5), ("btc-flat", 100.0, 0.0)]:
        for sec in range(120):
            spot = base + drift * sec
            rows.append({
                "slug": slug, "symbol": "btc", "date": "2026-06-14",
                "split": "future", "window_start_ts": 1, "seconds_into_window": sec,
                "time_left_sec": 900 - sec * 7, "cb_spot": spot,
                "yes_mid": 0.6, "yes_best_ask": 0.62, "yes_best_bid": 0.58,
                "dist_strike_bps": 20.0, "abs_dist_bps": 20.0, "consistent": True,
                "fav_ask": 0.62, "realized_vol": 0.5,
            })
    return pd.DataFrame(rows)


def test_ta_frame_has_ta_columns(monkeypatch):
    monkeypatch.setattr(hs, "load_base", lambda: _fake_base())
    hs._TA["df"] = None
    f = hs._ta_frame()
    for col in ["ta_ema_slope", "ta_rsi", "ta_regime", "ta_z_vwap"]:
        assert col in f.columns
    assert len(f) == len(_fake_base())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/research/test_ta_families.py::test_ta_frame_has_ta_columns -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_TA'`.

- [ ] **Step 3: Add the lazy cache (place directly after the `_PSETTLE = {"df": None}` line)**

```python
# TA family: base frame augmented with causal TA features, lazy.
_TA = {"df": None}


def _ta_frame():
    """load_base() left-joined with causal base-asset TA columns
    (research.dataset.ta_features). Built once; the campaign future-override is
    applied so TA candidates share the same split labels as every other family.
    The default (non-TA) pipeline never calls this, so it stays byte-identical."""
    if _TA["df"] is None:
        from research.dataset.ta_features import build_ta_features
        b = load_base()
        ta = build_ta_features(b)
        merged = b.merge(ta, on=["slug", "seconds_into_window"], how="left")
        _TA["df"] = apply_future_override(merged)
    return _TA["df"]
```

Also extend `set_future_override` to clear this cache. Change its body from:

```python
    FUTURE_START = date
    _PSETTLE["df"] = None
```

to:

```python
    FUTURE_START = date
    _PSETTLE["df"] = None
    _TA["df"] = None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/research/test_ta_families.py::test_ta_frame_has_ta_columns -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add research/analysis/hypothesis_sweep.py tests/research/test_ta_families.py
git commit -m "feat(ta): lazy TA-augmented base frame in hypothesis_sweep"
```

---

## Task 3: Four TA family builders + grids

**Files:**
- Modify: `research/analysis/hypothesis_sweep.py` (builders near the family block; `RATIONALE` ~line 440; `BUILDERS` ~line 418; `gen_specs` ~line 459)
- Test: `tests/research/test_ta_families.py`

First, **review overlap** with `research/analysis/divergence_backtest.py`, `cross_coin_leadlag.py`, and `e5_late_momentum_continuation.py` (and the existing `momentum`/`vol` families) so the divergence/regime builders extend rather than duplicate. Note any overlap in the deliverable doc (Task 4).

All four builders ignore `b` and read `_ta_frame()` (like `fam_psettle`). Contract: return `(cand_df, buy_yes_array)`, index-aligned.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/research/test_ta_families.py

def _patch_ta(monkeypatch):
    monkeypatch.setattr(hs, "load_base", lambda: _fake_base())
    hs._TA["df"] = None


def test_fam_ta_directional_buys_up_in_uptrend(monkeypatch):
    _patch_ta(monkeypatch)
    p = {"t_lo": 1, "t_hi": 900, "slope_min": 0.0, "ask_lo": 0.05, "ask_hi": 0.95}
    c, by = hs.fam_ta_directional(hs._ta_frame(), p)
    assert len(c) > 0
    # in the rising window every qualifying tick should buy UP (yes)
    up_rows = c["slug"] == "btc-up"
    assert by[up_rows.to_numpy()].all()


def test_fam_ta_filter_subsets_a_base_edge(monkeypatch):
    _patch_ta(monkeypatch)
    base_p = {"t_lo": 1, "t_hi": 900, "dist_min": 5, "ask_lo": 0.5, "ask_hi": 0.95}
    full, _ = hs.fam_det(hs._ta_frame(), base_p)
    p = {**base_p, "regime": "range"}
    c, by = hs.fam_ta_filter(hs._ta_frame(), p)
    assert len(c) <= len(full)            # a filter can only remove rows
    assert (c["ta_regime"] == "range").all()
    assert len(by) == len(c)


def test_fam_ta_regime_keeps_only_band(monkeypatch):
    _patch_ta(monkeypatch)
    p = {"t_lo": 1, "t_hi": 900, "dist_min": 5, "ask_lo": 0.5, "ask_hi": 0.95,
         "atr_lo": 0.0, "atr_hi": 1e9}
    c, by = hs.fam_ta_regime(hs._ta_frame(), p)
    assert len(c) == len(by)
    assert (c["ta_atr"] >= 0.0).all()


def test_fam_ta_divergence_buys_trend_side_when_book_lags(monkeypatch):
    _patch_ta(monkeypatch)
    p = {"t_lo": 1, "t_hi": 900, "slope_min": 0.0, "ask_lo": 0.05, "ask_hi": 0.95,
         "ret_min": 0.0}
    c, by = hs.fam_ta_divergence(hs._ta_frame(), p)
    assert len(c) == len(by)
    if len(c):
        assert by.dtype == bool


def test_all_ta_families_registered():
    for fam in ["ta_directional", "ta_filter", "ta_regime", "ta_divergence"]:
        assert fam in hs.BUILDERS
        assert fam in hs.RATIONALE


def test_gen_specs_includes_ta(monkeypatch):
    specs = hs.gen_specs()
    fams = {s["family"] for s in specs}
    assert {"ta_directional", "ta_filter", "ta_regime", "ta_divergence"} <= fams
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/research/test_ta_families.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'fam_ta_directional'`.

- [ ] **Step 3: Add the four builders (place after `fam_psettle`, before `_btc_ref`)**

```python
def fam_ta_directional(b, p):
    """TA DIRECTIONAL (honest control — book already prices spot; expected to
    fail): base-asset trend says up -> buy UP. Trend = EMA slope sign with a
    minimum magnitude (bps/sec). Tests the rejected family on clean data."""
    c = _ta_frame()
    m = ((c["time_left_sec"] >= p["t_lo"]) & (c["time_left_sec"] <= p["t_hi"])
         & (c["ta_ema_slope"].abs() >= p["slope_min"]))
    up = c["ta_ema_slope"] > 0
    by = up.to_numpy()
    ask = np.where(by, c["yes_best_ask"].to_numpy("f8"),
                   1.0 - c["yes_best_bid"].to_numpy("f8"))
    keep = (m.to_numpy() & np.isfinite(ask)
            & (ask >= p["ask_lo"]) & (ask <= p["ask_hi"]))
    return c[keep], by[keep]


def fam_ta_filter(b, p):
    """TA FILTER on the proven determinism edge: only take the favourite when the
    base-asset regime label matches (e.g. 'range' = no trend to fight). Tests
    whether a TA gate LIFTS an edge we already know is real."""
    c, by = fam_det(_ta_frame(), p)
    keep = (c["ta_regime"] == p["regime"]).to_numpy()
    return c[keep], by[keep]


def fam_ta_regime(b, p):
    """TA REGIME selection: run the determinism edge only inside an ATR band
    (bps). Thesis: the book lags spot more in higher-vol regimes -> bigger
    overshoots to harvest."""
    c, by = fam_det(_ta_frame(), p)
    atr = c["ta_atr"].to_numpy("f8")
    keep = np.isfinite(atr) & (atr >= p["atr_lo"]) & (atr < p["atr_hi"])
    return c[keep], by[keep]


def fam_ta_divergence(b, p):
    """TA DIVERGENCE (most aligned with 'rent on slow book repricing'): the base
    asset has moved (recent 30s return + EMA slope agree, magnitude >= ret_min
    bps) but the book hasn't repriced toward it. Buy the side the move implies."""
    c = _ta_frame()
    m = ((c["time_left_sec"] >= p["t_lo"]) & (c["time_left_sec"] <= p["t_hi"])
         & (c["ta_ema_slope"].abs() >= p["slope_min"])
         & (c["ta_ret_30s"].abs() >= p["ret_min"])
         & (np.sign(c["ta_ema_slope"]) == np.sign(c["ta_ret_30s"])))
    up = c["ta_ret_30s"] > 0
    by = up.to_numpy()
    ask = np.where(by, c["yes_best_ask"].to_numpy("f8"),
                   1.0 - c["yes_best_bid"].to_numpy("f8"))
    keep = (m.to_numpy() & np.isfinite(ask)
            & (ask >= p["ask_lo"]) & (ask <= p["ask_hi"]))
    return c[keep], by[keep]
```

- [ ] **Step 4: Register the builders. In the `BUILDERS = {` dict (~line 418), add after the `"psettle": fam_psettle,` entry:**

```python
    "ta_directional": fam_ta_directional, "ta_filter": fam_ta_filter,
    "ta_regime": fam_ta_regime, "ta_divergence": fam_ta_divergence,
```

- [ ] **Step 5: Add RATIONALE entries. In the `RATIONALE = {` dict (~line 440), add:**

```python
    "ta_directional": "base-asset TA trend says up -> buy UP (honest control; "
                      "book already prices spot, expected to fail)",
    "ta_filter": "TA regime gate on determinism: take the favourite only when "
                 "the base-asset regime label matches (does TA lift a real edge?)",
    "ta_regime": "ATR-band selection on determinism: harvest only where the "
                 "book lags spot more (higher base-asset vol = bigger overshoot)",
    "ta_divergence": "base asset moved (EMA slope + 30s return agree) but the "
                     "book hasn't repriced; buy the move-implied side",
```

- [ ] **Step 6: Add TA grids in `gen_specs()` (after the PSETTLE block, before the `# normalize tuple axes` comment ~line 646)**

```python
    # TA DIRECTIONAL — honest control across slope thresholds + ask bands
    specs += list(_grid(
        "ta_directional",
        t=[(1, 120), (120, 420), (420, 780)],
        slope_min=[0.0, 0.5, 1.0, 2.0],
        ask=[(0.05, 0.95), (0.30, 0.70), (0.40, 0.60)]))

    # TA FILTER — determinism gated by base-asset regime
    specs += list(_grid(
        "ta_filter",
        t=[(1, 120), (60, 300), (120, 420)],
        dist_min=[5, 8, 12],
        ask=[(0.55, 0.90), (0.70, 0.95)],
        regime=["range", "trend", "highvol"]))

    # TA REGIME — determinism inside an ATR band (bps)
    specs += list(_grid(
        "ta_regime",
        t=[(1, 120), (60, 300), (120, 420)],
        dist_min=[5, 8],
        ask=[(0.55, 0.90)],
        atr=[(0.0, 1.0), (1.0, 3.0), (3.0, 1e9), (1.0, 1e9)]))

    # TA DIVERGENCE — base moved, book lags; buy the move side
    specs += list(_grid(
        "ta_divergence",
        t=[(1, 120), (60, 300), (120, 420), (240, 600)],
        slope_min=[0.0, 0.5, 1.0],
        ret_min=[3.0, 5.0, 10.0, 20.0],
        ask=[(0.30, 0.55), (0.30, 0.70), (0.45, 0.65)]))
```

Note: `ta_regime` and `ta_filter` carry `atr=` / no extra tuple — the `atr` axis is split into `atr_lo`/`atr_hi` by the existing normalizer ONLY for the `v`/`t`/`ask`/`h` keys. Add an `atr` case to the normalizer. In the `for i, s in enumerate(specs):` loop (~line 648), after the `if "v" in p:` block add:

```python
        if "atr" in p:
            p["atr_lo"], p["atr_hi"] = p.pop("atr")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/research/test_ta_families.py -v`
Expected: all passed (8 tests).

- [ ] **Step 8: Run the full research test subset to confirm no regression to the existing pipeline**

Run: `uv run pytest tests/research/ -q`
Expected: all passed (no change to existing family counts that aren't TA).

- [ ] **Step 9: Commit**

```bash
git add research/analysis/hypothesis_sweep.py tests/research/test_ta_families.py
git commit -m "feat(ta): four TA hypothesis families (directional/filter/regime/divergence)"
```

---

## Task 4: Run the campaign on clean data + write the deliverable

**Files:**
- Create: `docs/research/TA_STRATEGIES_2026-06-16.md`
- (No source changes — this is the research run + analysis.)

The pipeline reveals the future block ONCE. Pre-register the run in `docs/research/test_ledger.md` before executing (one line: "TA CAMPAIGN 2026-06-16, FUTURE_START=2026-06-12, families ta_directional/filter/regime/divergence, Chainlink-settled, dedup vs all deployed edges").

- [ ] **Step 1: Rebuild the TA parquet against current clean data**

Run: `uv run python -m research.dataset.ta_features`
Expected: `wrote N rows -> data/research/ta_features/ta_features.parquet` (N ≈ slim row count).

- [ ] **Step 2: Run the sweep with the clean-data future override**

Confirm `hypothesis_sweep` sets `FUTURE_START` for this campaign (via `set_future_override("2026-06-12")` in its `main`, or an env/CLI knob if one exists — grep `set_future_override` usage first). Then:

Run: `uv run python -m research.analysis.hypothesis_sweep`
Expected: writes `data/research/hypotheses/results.jsonl` + `specs.jsonl`; TA families appear in the JSONL (`grep ta_ data/research/hypotheses/specs.jsonl | head`).

- [ ] **Step 3: Select (future-blind gates)**

Run: `uv run python -m research.analysis.hypothesis_select`
Expected: a shortlist file; inspect which TA specs (if any) pass n≥40, dev_n≥12, cpcv≥80%, full_ci_lo>0, latency 5s&10s EV>0, cap_10≥0.5.

- [ ] **Step 4: Verify under the live fill model**

Run: `uv run python -m research.analysis.hypothesis_verify --fill-model live`
Expected: per-candidate pre_verdict; record which TA candidates are `deploy_paper_candidate` or better with live-model future EV not negative.

- [ ] **Step 5: Dedup vs deployed edges + atlas placement**

Run: `uv run python -m research.analysis.edge_atlas` (and the Jaccard check used in prior campaigns — grep `jaccard` in `research/analysis/`). For each TA survivor, compute Jaccard of its decision set vs every live/paper edge (det_lwd, det_d12_dual, fav_disagree, early_disagree, psettle_ud, oracle_fade). Reject any with Jaccard ≥ ~0.3 (duplicate).

- [ ] **Step 6: Write the deliverable doc**

Create `docs/research/TA_STRATEGIES_2026-06-16.md` with: campaign pre-registration line; a table of EVERY TA family's verdict (n, dev_ev, future_ev under live model, CI, cpcv, Jaccard-vs-nearest, verdict); the overlap notes from Task 3's review of `divergence_backtest.py`/`cross_coin_leadlag.py`/`e5_late_momentum_continuation.py`; the honest negatives (expected: `ta_directional` fails); and for each survivor whether it maps to an existing engine mode or needs new engine wiring.

- [ ] **Step 7: Commit**

```bash
git add docs/research/TA_STRATEGIES_2026-06-16.md docs/research/test_ledger.md data/research/ta_features/ta_features.parquet
git commit -m "research(ta): TA strategy campaign results + verdicts"
```

---

## Task 5: Deploy survivors as paper twins (conditional) + close out

**Files:**
- Modify: `strategies.yaml` (ONLY if a survivor maps to an existing engine mode)
- Modify: `STATE.md`

Most TA features (EMA slope, RSI, ATR regime) are NOT in the live tick dtype, so most survivors are FLAG-for-engine-wiring, not deploy. A survivor is directly deployable only if its gate uses fields already on the live tick (e.g. a `realized_vol` band expressible via the existing `vol`/`consistent` knobs). Deploy only those.

- [ ] **Step 1: For each directly-deployable survivor, add a `live:false` paper twin to `strategies.yaml`**

Follow the existing paper-twin block shape (copy an existing `live:false` entry, e.g. `det_d12_wide_v1`, and change `id`, `mode`, params). Use a `ta_*_v1` id. Do NOT set `live:true`.

- [ ] **Step 2: Validate the YAML parses BEFORE any restart**

Run: `uv run python -c "from mean_reversion_live.engine.registry import load_strategies; print(len(load_strategies()))"`
Expected: prints the new strategy count without raising.

- [ ] **Step 3: Run the full suite green**

Run: `uv run pytest -q`
Expected: all passed.

- [ ] **Step 4: Safe-window restart of run_combined ONLY (if anything was deployed)**

Per CLAUDE.md / mean-rev-restart conventions: `./scripts/stop_all.sh && ./scripts/start_all.sh` (executor + existing books untouched). Then verify heartbeat fresh + `strategies_loaded` includes the new twin. If NOTHING was deployed, skip this step.

- [ ] **Step 5: Append a dated entry to `STATE.md`**

Summarize: families tested, survivors (with verdict numbers), honest negatives, what was deployed as a paper twin vs flagged for engine wiring, and the forward-monitoring gate (≥7 clean days realized EV/fill CI-lower > 0 before any live talk).

- [ ] **Step 6: Commit**

```bash
git add strategies.yaml STATE.md
git commit -m "feat(ta): deploy TA survivors as paper twins + state log"
```

---

## Self-Review notes

- **Spec coverage:** Component 1 → Task 1; Component 2 (4 roles) → Task 3; Component 3 (rigor) → Task 4; Component 4 (deploy/flag) → Task 5; Component 5 (tests/doc) → Tasks 1/3 tests + Task 4 doc. All covered.
- **Causality risk** (spec §Risks) → Task 1 `test_features_are_causal` + `test_per_slug_isolation`.
- **Dedup risk** → Task 4 Step 5.
- **Engine-wiring-needed** → Task 5 flag-don't-deploy rule.
- **Type consistency:** `_ta_frame()`, `_TA`, `TA_COLS`, `build_ta_features`, and the four `fam_ta_*` names are used identically across Tasks 1–3. Builder params (`slope_min`, `regime`, `atr_lo/atr_hi`, `ret_min`) match between the builders (Step 3) and the grids (Step 6), and the `atr` normalizer case is added in Task 3 Step 6.
```
