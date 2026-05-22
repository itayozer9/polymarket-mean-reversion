# Edge Research — Phase 0 (Audit) + Phase 1 (Canonical Dataset) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a trustworthy data foundation — audit the data and the
simulator for correctness bugs, then build one clean, reproducible canonical
research dataset spanning all 22 days that every later analysis runs off.

**Architecture:** A new top-level `research/` package, fully separate from the
live bot (`src/mean_reversion_live/`) and from the old throwaway pipeline
(`scripts/analysis/`). Phase 0 produces an audit findings document. Phase 1
produces tested feature/loader libraries and Parquet dataset tables. Pure
functions are unit-tested with real CSV fixtures (no mocking). Audit tasks
produce a findings document with concrete pass/concern thresholds.

**Tech Stack:** Python 3.9+, uv, pandas, numpy, scipy, pyarrow (Parquet),
pytest, WebFetch (for Polymarket fee docs).

**Scope:** This plan is Phases 0–1 of the spec
`docs/superpowers/specs/2026-05-22-polymarket-edge-research-design.md`.
Phases 2–4 (calibration study, event study, user reconstruction) and Phases 5–6
(strategy construction, validation gauntlet) get their own plans, written at the
post-Phase-0 and post-edge-map checkpoints respectively — they depend on findings
that do not exist yet. Cross-coin / macro alignment (the "are all coins dropping
together" signal) is also deferred to the Phase 2–4 plan as an analysis input —
`data/live_macro/` already supplies it for May and the March equivalent is cheap
to build there.

**Reference facts established by reading the codebase (do not re-derive):**
- Tick CSVs: 23 columns. Header order: `timestamp_ms, market_slug, symbol,
  window_start_ts, window_end_ts, seconds_into_window, yes_best_bid, yes_best_ask,
  yes_bid_depth, yes_ask_depth, no_best_bid, no_best_ask, no_bid_depth,
  no_ask_depth, chainlink_price, coinbase_price, start_price, move_pct, yes_mid,
  no_mid, spread_yes, spread_no, total_mid`.
- `move_pct = (spot - start_price) / start_price * 100` — already in **percent**.
- `start_price` = the strike. Historical files in `data/historical/<sym>_<date>.csv.gz`,
  live files in `data/live/<sym>_<date>.csv.gz`, same naming/schema.
- `data/outcomes.csv` columns: `timestamp_ms, market_slug, symbol, window_start_ts,
  window_end_ts, start_price, end_price, outcome, move_pct`. `outcome` ∈ {Up, Down}.
- Window slugs: `<sym>-updown-<tf>-<window_start_ts>`, tf ∈ {5m, 15m}.
- **Known bug to confirm in Task 5:** `scripts/mean_reversion/features.py::
  proximity_pct_from_move` returns `|move_pct|/100` (a fraction) but
  `signals.py::entry_signal` compares it to `proximity_max_pct` (intended as a
  percent). The proximity filter never fires.
- **Pre-Phase-0 manual code audit:** `docs/research/interim_code_audit.md` records
  a hand review of the decision + validation logic — 10 findings (proximity bug,
  Bonferroni-wrong-axis, IID-trade significance, no walk-forward, optimistic
  sweep fills, a latent live realized-vol buffer bug, market-ordered portfolio
  state, uncalibrated vol thresholds; plus a clean no-look-ahead verdict).
  Tasks 5/7/8 should confirm the simulator-correctness items with runnable checks
  and cite this doc; the validation-method items feed the report's diagnosis.

---

## File Structure

```
research/
  __init__.py
  holdout.py                 # sealed date ranges (created Task 9)
  data/
    __init__.py
    loader.py                # Task 2 — load ticks + outcomes from this repo's layout
  features/
    __init__.py
    core.py                  # Task 10-11 — corrected/new pure feature functions
  dataset/
    __init__.py
    windows.py               # Task 12 — window-level table builder
    ticks.py                 # Task 13 — tick-level canonical table builder
  audit/
    __init__.py
    quality.py               # Task 3 — tick data quality checks
    outcomes.py              # Task 4 — outcome correctness checks
    proximity_bug.py         # Task 5 — reproduce the proximity unit bug
    leakage.py               # Task 7 — look-ahead audit helper
    reconcile.py             # Task 8 — sim vs live-paper reconciliation
  build_dataset.py           # Task 14 — top-level dataset build entrypoint

tests/research/
  conftest.py                # fixture paths
  fixtures/                  # tiny real CSV slices committed for tests
  test_loader.py
  test_features_core.py
  test_dataset_windows.py
  test_dataset_ticks.py

docs/research/
  phase0_audit.md            # Phase 0 findings (appended by Tasks 3-8)
  phase0_verdict.md          # Task 9 — synthesis + holdout decision
  canonical_dataset.md       # Task 14 — dataset schema + summary

data/research/               # build output (gitignored)
  windows.parquet
  ticks_15m.parquet
  ticks_5m.parquet
```

---

## Task 1: Scaffold the research package

**Files:**
- Create: `research/__init__.py`, `research/data/__init__.py`,
  `research/features/__init__.py`, `research/dataset/__init__.py`,
  `research/audit/__init__.py`, `tests/research/__init__.py`
- Create: `docs/research/phase0_audit.md`
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Create empty package files**

Create each `__init__.py` listed above as an empty file.

- [ ] **Step 2: Add pyarrow dependency and make `research` importable in tests**

In `pyproject.toml`, add `"pyarrow>=15.0"` to `[project] dependencies`. In
`[tool.pytest.ini_options]` add a line: `pythonpath = ["."]` (so `import research`
works in tests without installing the package).

- [ ] **Step 3: Create the audit findings document skeleton**

`docs/research/phase0_audit.md`:

```markdown
# Phase 0 — Data & Simulator Audit

Findings from auditing the data and the imported decision logic before any
strategy work. Each section is appended by its task. A "CONCERN" or "BUG" tag
means later phases must account for it.

## Task 3 — Tick data quality
_pending_

## Task 4 — Outcome correctness
_pending_

## Task 5 — Proximity filter bug
_pending_

## Task 6 — Fee & cost realism
_pending_

## Task 7 — Look-ahead / leakage audit
_pending_

## Task 8 — Sim vs live-paper reconciliation
_pending_
```

- [ ] **Step 4: Gitignore the build output**

Append to `.gitignore`: `data/research/`

- [ ] **Step 5: Verify the environment**

Run: `uv sync && uv run python -c "import pyarrow, research; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add research tests/research docs/research pyproject.toml .gitignore
git commit -m "research: scaffold edge-research package (Phase 0-1)"
```

---

## Task 2: Research data loader

**Files:**
- Create: `research/data/loader.py`
- Create: `tests/research/conftest.py`, `tests/research/test_loader.py`
- Create: `tests/research/fixtures/` (small real CSV slices)

The loader knows this repo's layout (`data/historical/` + `data/live/`), unlike
the arb `loaders.py` which hardcodes `data_v2`. It loads all 23 columns.

- [ ] **Step 1: Build the test fixture**

Create a tiny real fixture so tests use real data, not mocks. Run:

```bash
mkdir -p tests/research/fixtures
# one 15m window's worth of rows for btc from a real file
uv run python - <<'EOF'
import pandas as pd
df = pd.read_csv("data/live/btc_2026-05-20.csv.gz")
df15 = df[df.market_slug.str.contains("updown-15m")]
slug = df15.market_slug.iloc[0]
one = df15[df15.market_slug == slug]
one.to_csv("tests/research/fixtures/btc_oneliner_15m.csv", index=False)
print("fixture rows:", len(one), "slug:", slug)
EOF
```

- [ ] **Step 2: Write the failing test**

`tests/research/conftest.py`:

```python
import pathlib
import pytest

@pytest.fixture
def fixtures_dir():
    return pathlib.Path(__file__).parent / "fixtures"
```

`tests/research/test_loader.py`:

```python
import numpy as np
from research.data.loader import load_tick_csv, ALL_TICK_COLS

def test_load_tick_csv_has_all_columns(fixtures_dir):
    df = load_tick_csv(fixtures_dir / "btc_oneliner_15m.csv")
    for col in ALL_TICK_COLS:
        assert col in df.columns, f"missing {col}"
    assert len(df) > 0
    # ticks are sorted by seconds_into_window
    assert df["seconds_into_window"].is_monotonic_increasing
    # spot columns are present and non-null on most rows
    assert df["coinbase_price"].notna().mean() > 0.5
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/research/test_loader.py -q`
Expected: FAIL — `ModuleNotFoundError: research.data.loader`.

- [ ] **Step 4: Implement the loader**

`research/data/loader.py`:

```python
"""Load Polymarket tick CSVs + outcomes from this repo's data layout.

Unlike polymarket-arb's loaders.py (which hardcodes data_v2/), this knows about
data/historical/ and data/live/ and keeps ALL 23 columns including spot prices.
"""
from __future__ import annotations
import glob
import io
import os
import re
import subprocess
from datetime import datetime, date
from typing import Iterator, Optional

import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HISTORICAL_DIR = os.path.join(REPO_ROOT, "data", "historical")
LIVE_DIR = os.path.join(REPO_ROOT, "data", "live")
OUTCOMES_FILE = os.path.join(REPO_ROOT, "data", "outcomes.csv")

ALL_TICK_COLS = [
    "timestamp_ms", "market_slug", "symbol", "window_start_ts", "window_end_ts",
    "seconds_into_window", "yes_best_bid", "yes_best_ask", "yes_bid_depth",
    "yes_ask_depth", "no_best_bid", "no_best_ask", "no_bid_depth", "no_ask_depth",
    "chainlink_price", "coinbase_price", "start_price", "move_pct", "yes_mid",
    "no_mid", "spread_yes", "spread_no", "total_mid",
]


def _read_csv_gz_tolerant(path: str) -> pd.DataFrame:
    """Read a .csv.gz even if the gzip trailer is corrupt (truncated EOD write)."""
    try:
        return pd.read_csv(path)
    except Exception:
        proc = subprocess.Popen(["gunzip", "-c", path], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        data, _ = proc.communicate()
        return pd.read_csv(io.BytesIO(data), on_bad_lines="skip")


def load_tick_csv(path) -> pd.DataFrame:
    """Load one tick CSV (.csv or .csv.gz), sorted within each window."""
    path = str(path)
    if path.endswith(".gz"):
        df = _read_csv_gz_tolerant(path)
    else:
        df = pd.read_csv(path)
    df = df.sort_values(["window_start_ts", "seconds_into_window"], kind="mergesort")
    return df.reset_index(drop=True)


def _file_date(path: str) -> Optional[date]:
    m = re.search(r"_(\d{4}-\d{2}-\d{2})(?:_raw)?\.csv\.gz$", path)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def list_tick_files(symbol: str, date_start: str, date_end: str) -> list[str]:
    """All tick files for symbol in [date_start, date_end], historical + live,
    chronologically ordered. Skips *_raw.csv.gz duplicates."""
    d0 = datetime.strptime(date_start, "%Y-%m-%d").date()
    d1 = datetime.strptime(date_end, "%Y-%m-%d").date()
    found: dict[date, str] = {}
    for d in (HISTORICAL_DIR, LIVE_DIR):
        for p in glob.glob(os.path.join(d, f"{symbol}_*.csv.gz")):
            if p.endswith("_raw.csv.gz"):
                continue
            fd = _file_date(p)
            if fd is not None and d0 <= fd <= d1:
                found[fd] = p  # live overrides historical if both exist
    return [found[k] for k in sorted(found)]


def iter_windows(symbol: str, timeframe: str, date_start: str, date_end: str
                 ) -> Iterator[tuple[str, pd.DataFrame]]:
    """Yield (slug, ticks_df) per market window, chronologically."""
    prefix = f"{symbol}-updown-{timeframe}-"
    for f in list_tick_files(symbol, date_start, date_end):
        df = load_tick_csv(f)
        df = df[df["market_slug"].astype(str).str.startswith(prefix)]
        for slug, g in df.groupby("market_slug", sort=True):
            yield str(slug), g.reset_index(drop=True)


def load_outcomes() -> pd.DataFrame:
    """Return the outcomes table indexed by market_slug."""
    df = pd.read_csv(OUTCOMES_FILE)
    return df.drop_duplicates("market_slug").set_index("market_slug")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/research/test_loader.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add research/data/loader.py tests/research/
git commit -m "research: data loader for historical + live tick CSVs"
```

---

## Task 3: Tick data quality audit

**Files:**
- Create: `research/audit/quality.py`
- Modify: `docs/research/phase0_audit.md`

Goal: quantify where the tick data is trustworthy. This is an investigation —
the script computes concrete metrics; the findings go into the audit doc.

- [ ] **Step 1: Implement the quality audit script**

`research/audit/quality.py` — for every (symbol, timeframe) over the full range
`2026-03-04`..`2026-05-22`, compute per-day:
- window count, tick count, mean ticks/window;
- **gap rate**: fraction of consecutive ticks within a window whose
  `seconds_into_window` differs by >2 (sampling gaps);
- **stale rate**: fraction of ticks where `yes_best_bid/ask/no_best_bid/ask` are
  all identical to the previous tick (frozen book);
- **crossed-book rate**: fraction where `yes_best_bid > yes_best_ask` or
  `no_best_bid > no_best_ask`;
- **mid-sum error**: mean `|yes_mid + no_mid - 1.0|` (should be ~0);
- **depth profile**: median `yes_ask_depth` and `no_ask_depth` (USD) — tells us
  whether $10 fills are realistic.

Print a per-day table and a per-(symbol,timeframe) summary. Provide a
`run()` function and an `if __name__ == "__main__": run()` guard.

- [ ] **Step 2: Run the audit**

Run: `uv run python -m research.audit.quality`
Expected: a table covering March + May with no crash.

- [ ] **Step 3: Record findings**

Replace the `## Task 3` section of `docs/research/phase0_audit.md` with the
summary table and a written verdict. Tag any day/symbol with gap rate >10%,
stale rate >25%, crossed-book rate >1%, or mid-sum error >0.02 as **CONCERN**
with the specific numbers. Explicitly state whether March 4–13 (smaller files)
is lower quality than March 14–17. Also record the **structural data limitation**
as a **CONCERN**: the schema carries only top-of-book (best bid/ask + the depth
at that one level) — there is no deeper book, so walk-the-book slippage and true
capacity can only be approximated, never measured exactly.

- [ ] **Step 4: Commit**

```bash
git add research/audit/quality.py docs/research/phase0_audit.md
git commit -m "research: tick data quality audit (Phase 0 Task 3)"
```

---

## Task 4: Outcome correctness audit

**Files:**
- Create: `research/audit/outcomes.py`
- Modify: `docs/research/phase0_audit.md`

- [ ] **Step 1: Implement the outcome audit script**

`research/audit/outcomes.py`:
- Load `data/outcomes.csv`. For each row, the implied resolution is
  `Up if end_price > start_price else Down`. Assert it matches the `outcome`
  column; count mismatches.
- For a sample of windows, recompute from the **last tick** of that window in
  the tick data: the final `move_pct` sign should agree with `outcome`
  (`move_pct > 0` → Up). Count agreement.
- **Resolution oracle.** For each window, take the last tick's `chainlink_price`
  and `coinbase_price`; for each feed compute whether `feed_price > strike`
  agrees with the recorded `outcome`. Report the agreement rate per feed — the
  feed with the higher rate is the one Polymarket settles on. Every later
  fair-value calculation must use that feed.
- Compute **coverage**: how many (symbol, timeframe, window) appear in the tick
  data but have **no** row in `outcomes.csv`, and vice versa.
- Report base rates: overall P(Up) per symbol/timeframe (used later as the
  no-skill baseline).

- [ ] **Step 2: Run the audit**

Run: `uv run python -m research.audit.outcomes`
Expected: prints mismatch counts, coverage counts, base rates.

- [ ] **Step 3: Record findings**

Write the `## Task 4` section: mismatch rate (tag **BUG** if >0.5%), coverage
gaps (tag **CONCERN** if >5% of windows lack outcomes), the **resolution oracle**
(which feed settles the markets, with its agreement rate), and the per-symbol
P(Up) base rates. These base rates are referenced by every later calibration.

- [ ] **Step 4: Commit**

```bash
git add research/audit/outcomes.py docs/research/phase0_audit.md
git commit -m "research: outcome correctness audit (Phase 0 Task 4)"
```

---

## Task 5: Confirm and document the proximity-filter bug

**Files:**
- Create: `research/audit/proximity_bug.py`
- Modify: `docs/research/phase0_audit.md`

The plan author found this by reading `features.py`/`signals.py`. This task
proves it with a runnable reproduction so it is not taken on faith.

- [ ] **Step 1: Write the reproduction script**

`research/audit/proximity_bug.py` — using the imported arb code:

```python
"""Reproduce the proximity-filter unit-mismatch bug.

features.proximity_pct_from_move(move_pct) returns |move_pct|/100 (a fraction).
signals.entry_signal rejects a tick only when `proximity > proximity_max_pct`.
With proximity_max_pct intended as a percent (e.g. 0.5 == "0.5%"), the filter
can never reject a realistic tick.
"""
from research.data.loader import iter_windows
import sys, os
sys.path.insert(0, os.environ.get("POLYMARKET_ARB_PATH", os.path.expanduser("~/dev/polymarket-arb")))
from scripts.mean_reversion import features as feat


def run():
    # Largest |move_pct| seen across a sample of real BTC 15m windows.
    worst = 0.0
    n = 0
    for slug, g in iter_windows("btc", "15m", "2026-03-14", "2026-05-21"):
        worst = max(worst, g["move_pct"].abs().max())
        n += 1
        if n >= 2000:
            break
    prox_at_worst = abs(worst) / 100.0  # what features.py computes
    print(f"windows sampled: {n}")
    print(f"largest |move_pct| observed: {worst:.4f}%")
    print(f"feature 'proximity' at that extreme: {prox_at_worst:.6f}")
    for thr in (0.2, 0.5, 1.5, 3.0, 100.0):
        fires = prox_at_worst > thr
        print(f"  proximity_max_pct={thr}: filter ever rejects? {fires}")
    print("VERDICT: proximity filter is inert for all realistic configs"
          if prox_at_worst <= 0.2 else "VERDICT: re-examine")

if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run the reproduction**

Run: `uv run python -m research.audit.proximity_bug`
Expected: the largest `|move_pct|` is well under 20%, so `proximity` (≤~0.2)
never exceeds even `proximity_max_pct=0.2` → filter inert. Confirms the bug.

- [ ] **Step 3: Record findings**

Write the `## Task 5` section, tagged **BUG**: state the mechanism, the
reproduction numbers, and the implication — *every backtest and every live
config ran with no effective proximity filter, so the user's core "near the
strike" rule was never tested.* State the fix that Phase 1 will use: the
canonical dataset will carry a **corrected** proximity in percent
(`|move_pct|`) and a σ-proximity feature (Task 10); the broken arb function is
left untouched (changing it would break the live bot's replay-parity test —
out of scope here).

- [ ] **Step 4: Commit**

```bash
git add research/audit/proximity_bug.py docs/research/phase0_audit.md
git commit -m "research: confirm proximity-filter bug (Phase 0 Task 5)"
```

---

## Task 6: Fee & cost-realism audit

**Files:**
- Create: `research/audit/cost_notes.md` (working notes)
- Modify: `docs/research/phase0_audit.md`

- [ ] **Step 1: Verify Polymarket's real fee structure**

Use WebFetch on `https://docs.polymarket.com/trading/fees` (the URL cited in
`config.py::FillParams`). Determine: does Polymarket charge taker fees on crypto
up/down markets in 2026, and is the formula `shares × rate × p × (1-p)` with a
crypto rate of 0.07? Save the answer verbatim to `research/audit/cost_notes.md`.
If the page is unreachable, note that and proceed with the documented formula.

- [ ] **Step 2: Quantify the real round-trip cost from the data**

Write a short inline script (or extend `cost_notes.md`) computing, over a sample
of real BTC/ETH 15m ticks in the entry-relevant odds band 0.05–0.35:
- median **spread** the side would cross (`yes_best_ask - yes_best_bid`), in ¢
  and as a % of the mid;
- the modelled **fee** per round trip at $10 notional using
  `0.07 × p × (1-p) × shares` for entry and exit;
- the **total** round-trip cost (spread + fees) as a % of the $10 stake.

- [ ] **Step 3: Record findings**

Write the `## Task 6` section: the verified fee status, the median spread cost,
the fee cost, and the combined round-trip cost in the entry band. State the
break-even win rate this cost implies for a +50% profit-target trade. Tag
**CONCERN** if total round-trip cost exceeds 8% of stake. This number is the
hurdle every strategy in Phase 5 must clear. Note explicitly that cost *beyond*
the best level (walking the book on orders larger than top-of-book depth) cannot
be measured from this data and is an assumption — cross-reference the Task 3
top-of-book limitation.

- [ ] **Step 4: Commit**

```bash
git add research/audit/cost_notes.md docs/research/phase0_audit.md
git commit -m "research: fee & cost realism audit (Phase 0 Task 6)"
```

---

## Task 7: Look-ahead / leakage audit

**Files:**
- Create: `research/audit/leakage.py` (a checklist runner + assertions)
- Modify: `docs/research/phase0_audit.md`

- [ ] **Step 1: Write the leakage assertions**

`research/audit/leakage.py` — encode the audit as runnable assertions where
possible, plus a written checklist for the parts that require code review:
- **Assertion A:** in `simulate.py`, entry fills use the tick reached *after*
  the reaction delay (`armed_until_idx = i + delay_ticks`, `delay_ticks >= 1`)
  — confirm by importing and checking `simulate` arms with `delay_ticks>=1`.
- **Assertion B:** `features.rolling_max_drop` only looks at `price[lo:i+1]`
  (indices ≤ i) — confirm no `i+1:` slice. Inspect the source string at runtime
  (`inspect.getsource`) and assert `"i + 1]"` appears and `"i+1:"` / `"i + 1:"`
  do not.
- **Checklist (written verdict in the doc):** does `_close_position` use only
  the current tick's bid? does `forced_resolution` use the true `outcome` only
  at/after `window_duration_sec - 2`? does feature precompute use any
  window-global stat (e.g. a full-window max) that a live tick would not know?

- [ ] **Step 2: Run the assertions**

Run: `uv run python -m research.audit.leakage`
Expected: assertions A and B pass; the script prints the checklist items for
manual verdict.

- [ ] **Step 3: Record findings**

Write the `## Task 7` section: the assertion results plus a written verdict on
each checklist item, tagging any look-ahead found as **BUG**. If clean, state
"no look-ahead leakage found in the decision path."

- [ ] **Step 4: Commit**

```bash
git add research/audit/leakage.py docs/research/phase0_audit.md
git commit -m "research: look-ahead leakage audit (Phase 0 Task 7)"
```

---

## Task 8: Sim vs live-paper reconciliation

**Files:**
- Create: `research/audit/reconcile.py`
- Modify: `docs/research/phase0_audit.md`

Proves the simulator's fills match what the book actually offered live.

- [ ] **Step 1: Implement the reconciliation script**

`research/audit/reconcile.py` — for a sample of ~100 recorded trades from
`data/jsonl/<sid>/trades.jsonl` (use `cfg_21c8c00165b3` and `v2_gold_03_down_all`):
- for each trade, locate the window's ticks in `data/live/` by slug;
- find the tick at `entry_ts_ms`; assert the recorded `entry_price` is within
  one tick of an `ask` actually quoted at/around that timestamp (±2s);
- same for `exit_ts_ms` vs the `bid`;
- report the fraction of trades whose fills are reproducible, and the mean
  absolute price discrepancy.

- [ ] **Step 2: Run the reconciliation**

Run: `uv run python -m research.audit.reconcile`
Expected: prints reproducible-fraction and mean discrepancy.

- [ ] **Step 3: Record findings**

Write the `## Task 8` section: reproducible fraction, mean discrepancy. Tag
**CONCERN** if <90% reproducible or mean discrepancy >0.01. State whether the
bot's paper losses are genuine strategy failure (fills honest) or partly a
fill artifact.

- [ ] **Step 4: Commit**

```bash
git add research/audit/reconcile.py docs/research/phase0_audit.md
git commit -m "research: sim vs live-paper reconciliation (Phase 0 Task 8)"
```

---

## Task 9: Phase 0 synthesis + sealed-holdout decision

**Files:**
- Create: `docs/research/phase0_verdict.md`
- Create: `research/holdout.py`

- [ ] **Step 1: Write the verdict document**

`docs/research/phase0_verdict.md` — synthesize all Task 3–8 findings into:
- a bullet list of every **BUG** and **CONCERN** with its impact on later phases;
- a "data trust map": which (symbol, timeframe, date-range) cells are usable;
- a go/no-go statement for proceeding to Phase 1.

- [ ] **Step 2: Define the sealed hold-out**

`research/holdout.py`:

```python
"""Sealed hold-out boundary. Phase 2+ analyses MUST exclude these dates from
all fitting and selection. Opened exactly once, at the end of Phase 6.

Recommended split (confirm with user at the Phase 0 checkpoint):
- DEVELOPMENT: 2026-03-04 .. 2026-05-17  (March regime + first 3 live days)
- SEALED HOLD-OUT: 2026-05-18 .. 2026-05-22  (final 5 live days)
"""
DEV_START = "2026-03-04"
DEV_END = "2026-05-17"
HOLDOUT_START = "2026-05-18"
HOLDOUT_END = "2026-05-22"

def is_holdout(date_str: str) -> bool:
    return HOLDOUT_START <= date_str <= HOLDOUT_END
```

- [ ] **Step 3: Checkpoint with the user**

STOP. Present `phase0_verdict.md` and the recommended hold-out split to the user.
The split is a one-way door — do not proceed to Phase 1's dataset build until the
user confirms or adjusts the boundary in `research/holdout.py`.

- [ ] **Step 4: Commit**

```bash
git add docs/research/phase0_verdict.md research/holdout.py
git commit -m "research: Phase 0 verdict + sealed hold-out boundary"
```

---

## Task 10: Feature library — proximity & volatility

**Files:**
- Create: `research/features/core.py`
- Create: `tests/research/test_features_core.py`

All functions are pure, numpy-in/numpy-out, no I/O. `move_pct` is in **percent**.

- [ ] **Step 1: Write the failing tests**

`tests/research/test_features_core.py`:

```python
import numpy as np
from research.features.core import (
    corrected_proximity_pct, realized_vol_per_sec, sigma_proximity,
)

def test_corrected_proximity_is_percent():
    # move_pct already in percent; proximity must equal |move_pct|
    mp = np.array([0.0, 0.5, -1.2, 3.0], dtype="f8")
    out = corrected_proximity_pct(mp)
    assert np.allclose(out, [0.0, 0.5, 1.2, 3.0])

def test_realized_vol_per_sec_zero_for_flat():
    mp = np.zeros(120, dtype="f8")
    out = realized_vol_per_sec(mp, window=60)
    assert np.allclose(out, 0.0)

def test_realized_vol_per_sec_positive_for_moving():
    rng = np.random.default_rng(0)
    mp = np.cumsum(rng.normal(0, 0.05, 300))
    out = realized_vol_per_sec(mp, window=60)
    assert out[-1] > 0.0

def test_sigma_proximity_small_when_far_in_time():
    # 0.5% from strike, but lots of time and vol -> few sigmas away
    mp = np.full(10, 0.5, dtype="f8")
    vol_per_sec = np.full(10, 0.05, dtype="f8")   # 0.05%/s
    time_left = np.full(10, 400, dtype="f8")       # 400s left
    out = sigma_proximity(mp, vol_per_sec, time_left)
    # sigma_remaining = 0.05*sqrt(400) = 1.0%  -> 0.5/1.0 = 0.5 sigma
    assert np.allclose(out, 0.5, atol=1e-6)

def test_sigma_proximity_large_when_little_time():
    mp = np.full(10, 0.5, dtype="f8")
    vol_per_sec = np.full(10, 0.05, dtype="f8")
    time_left = np.full(10, 4, dtype="f8")         # 4s left
    out = sigma_proximity(mp, vol_per_sec, time_left)
    # sigma_remaining = 0.05*2 = 0.1% -> 0.5/0.1 = 5 sigma (decided)
    assert np.allclose(out, 5.0, atol=1e-6)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research/test_features_core.py -q`
Expected: FAIL — `ModuleNotFoundError: research.features.core`.

- [ ] **Step 3: Implement the features**

`research/features/core.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research/test_features_core.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add research/features/core.py tests/research/test_features_core.py
git commit -m "research: proximity + volatility features (corrected proximity, sigma-proximity)"
```

---

## Task 11: Feature library — odds drops, velocity, microstructure

**Files:**
- Modify: `research/features/core.py`
- Modify: `tests/research/test_features_core.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/research/test_features_core.py`:

```python
from research.features.core import (
    rolling_drop_pct, odds_velocity, book_imbalance, spot_move_pct,
)

def test_rolling_drop_pct_detects_drop():
    # mid rises to 0.40 then falls to 0.20 -> 50% drop from window peak
    mid = np.array([0.30, 0.40, 0.35, 0.20], dtype="f8")
    out = rolling_drop_pct(mid, window_sec=10)
    assert np.isclose(out[-1], 50.0, atol=1e-6)
    assert out[0] == 0.0

def test_odds_velocity_sign():
    mid = np.array([0.30, 0.28, 0.25, 0.25], dtype="f8")
    out = odds_velocity(mid, window_sec=2)
    assert out[2] < 0.0   # falling
    assert out[0] == 0.0

def test_book_imbalance_bounds():
    bid = np.array([100.0, 0.0, 50.0], dtype="f8")
    ask = np.array([100.0, 0.0, 0.0], dtype="f8")
    out = book_imbalance(bid, ask)
    assert np.isclose(out[0], 0.5)
    assert np.isclose(out[1], 0.5)   # both zero -> neutral
    assert np.isclose(out[2], 1.0)

def test_spot_move_pct_signed_change():
    # move_pct is the spot's signed distance from strike, in percent.
    # spot_move_pct is the change in that over the trailing window.
    mp = np.array([0.00, 0.10, 0.30, 0.25], dtype="f8")
    out = spot_move_pct(mp, window_sec=2)
    assert out[0] == 0.0
    assert np.isclose(out[2], 0.30)   # spot moved +0.30% over 2 ticks
    assert np.isclose(out[3], 0.15)   # 0.25 - 0.10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research/test_features_core.py -q`
Expected: FAIL — names not defined.

- [ ] **Step 3: Implement the features**

Append to `research/features/core.py`:

```python
def rolling_drop_pct(mid: np.ndarray, window_sec: int) -> np.ndarray:
    """Percent drop from the trailing-window peak of `mid` to the current value.

    out[i] = (max(mid[i-window_sec:i+1]) - mid[i]) / max(...) * 100, in [0,100].
    0.0 where no history or peak is 0.
    """
    m = np.asarray(mid, dtype="f8")
    n = len(m)
    out = np.zeros(n, dtype="f8")
    for i in range(n):
        lo = max(0, i - window_sec)
        peak = m[lo:i + 1].max()
        if peak > 0:
            out[i] = (peak - m[i]) / peak * 100.0
    return out


def odds_velocity(mid: np.ndarray, window_sec: int) -> np.ndarray:
    """Change in `mid` over the trailing `window_sec` ticks (signed, per window).

    out[i] = mid[i] - mid[i-window_sec]; 0.0 for the first `window_sec` ticks.
    """
    m = np.asarray(mid, dtype="f8")
    n = len(m)
    out = np.zeros(n, dtype="f8")
    for i in range(window_sec, n):
        out[i] = m[i] - m[i - window_sec]
    return out


def book_imbalance(bid_depth: np.ndarray, ask_depth: np.ndarray) -> np.ndarray:
    """bid / (bid + ask) per tick, in [0,1]. 0.5 when both sides are 0."""
    b = np.asarray(bid_depth, dtype="f8")
    a = np.asarray(ask_depth, dtype="f8")
    total = b + a
    out = np.full(len(b), 0.5, dtype="f8")
    mask = total > 0
    out[mask] = b[mask] / total[mask]
    return out


def spot_move_pct(move_pct: np.ndarray, window_sec: int) -> np.ndarray:
    """Signed change in the underlying's distance-from-strike over the trailing
    `window_sec` ticks. The spot-side counterpart of `odds_velocity`: pairing the
    two separates a noise-drop (odds fell, spot did not) from a signal-drop (odds
    fell because spot genuinely moved).

    out[i] = move_pct[i] - move_pct[i-window_sec]; 0.0 for the first window.
    """
    m = np.asarray(move_pct, dtype="f8")
    n = len(m)
    out = np.zeros(n, dtype="f8")
    for i in range(window_sec, n):
        out[i] = m[i] - m[i - window_sec]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research/test_features_core.py -q`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add research/features/core.py tests/research/test_features_core.py
git commit -m "research: odds-drop, velocity, spot-move, and book-imbalance features"
```

---

## Task 12: Window-level canonical table builder

**Files:**
- Create: `research/dataset/windows.py`
- Create: `tests/research/test_dataset_windows.py`

One row per market window: identity, strike, outcome, and per-window summary
stats. ~65k rows total.

- [ ] **Step 1: Write the failing test**

`tests/research/test_dataset_windows.py`:

```python
import pandas as pd
from research.dataset.windows import build_window_row
from research.data.loader import load_tick_csv

def test_build_window_row_fields(fixtures_dir):
    ticks = load_tick_csv(fixtures_dir / "btc_oneliner_15m.csv")
    slug = ticks["market_slug"].iloc[0]
    row = build_window_row(slug, ticks, outcome="Up", end_price=70000.0)
    assert row["slug"] == slug
    assert row["symbol"] == "btc"
    assert row["timeframe"] == "15m"
    assert row["n_ticks"] == len(ticks)
    assert row["outcome"] == "Up"
    assert row["outcome_up"] == 1
    assert 0.0 <= row["min_yes_mid"] <= 1.0
    assert row["strike"] == ticks["start_price"].iloc[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/research/test_dataset_windows.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the builder**

`research/dataset/windows.py`:

```python
"""Window-level canonical table: one row per market window."""
from __future__ import annotations
import pandas as pd


def _parse_slug(slug: str) -> tuple[str, str, int]:
    # <sym>-updown-<tf>-<window_start_ts>
    parts = slug.split("-")
    return parts[0], parts[2], int(parts[3])


def build_window_row(slug: str, ticks: pd.DataFrame,
                     outcome: str | None, end_price: float | None) -> dict:
    """Summarize one window's ticks into a single canonical row."""
    sym, tf, wstart = _parse_slug(slug)
    return {
        "slug": slug,
        "symbol": sym,
        "timeframe": tf,
        "window_start_ts": wstart,
        "window_end_ts": int(ticks["window_end_ts"].iloc[0]),
        "strike": float(ticks["start_price"].iloc[0]),
        "n_ticks": int(len(ticks)),
        "first_sec": int(ticks["seconds_into_window"].min()),
        "last_sec": int(ticks["seconds_into_window"].max()),
        "outcome": outcome,
        "outcome_up": (1 if outcome == "Up" else 0 if outcome == "Down" else None),
        "end_price": end_price,
        "min_yes_mid": float(ticks["yes_mid"].min()),
        "max_yes_mid": float(ticks["yes_mid"].max()),
        "min_no_mid": float(ticks["no_mid"].min()),
        "max_no_mid": float(ticks["no_mid"].max()),
        "max_abs_move_pct": float(ticks["move_pct"].abs().max()),
        "median_yes_ask_depth": float(ticks["yes_ask_depth"].median()),
        "median_no_ask_depth": float(ticks["no_ask_depth"].median()),
    }


def build_windows_table(symbol: str, timeframe: str, date_start: str,
                        date_end: str) -> pd.DataFrame:
    """Build the window table for one (symbol, timeframe) over a date range."""
    from research.data.loader import iter_windows, load_outcomes
    outcomes = load_outcomes()
    rows = []
    for slug, ticks in iter_windows(symbol, timeframe, date_start, date_end):
        if slug in outcomes.index:
            oc = outcomes.loc[slug]
            outcome, end_price = str(oc["outcome"]), float(oc["end_price"])
        else:
            outcome, end_price = None, None
        rows.append(build_window_row(slug, ticks, outcome, end_price))
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/research/test_dataset_windows.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research/dataset/windows.py tests/research/test_dataset_windows.py
git commit -m "research: window-level canonical table builder"
```

---

## Task 13: Tick-level canonical table builder

**Files:**
- Create: `research/dataset/ticks.py`
- Create: `tests/research/test_dataset_ticks.py`

One row per tick with all derived features attached. The table every Phase 2–4
analysis consumes.

- [ ] **Step 1: Write the failing test**

`tests/research/test_dataset_ticks.py`:

```python
import numpy as np
from research.dataset.ticks import build_window_ticks
from research.data.loader import load_tick_csv

def test_build_window_ticks_adds_features(fixtures_dir):
    raw = load_tick_csv(fixtures_dir / "btc_oneliner_15m.csv")
    slug = raw["market_slug"].iloc[0]
    out = build_window_ticks(slug, raw, outcome="Up")
    for col in ["proximity_pct", "sigma_proximity", "time_left_sec",
                "yes_drop_30s", "no_drop_30s", "yes_velocity_10s",
                "spot_move_30s", "realized_vol", "yes_imbalance", "outcome_up"]:
        assert col in out.columns, f"missing {col}"
    assert len(out) == len(raw)
    # time_left decreases monotonically
    assert out["time_left_sec"].is_monotonic_decreasing
    # no future leak: outcome_up is constant within the window
    assert out["outcome_up"].nunique() == 1
    # proximity_pct equals |move_pct| (the bug fix)
    assert np.allclose(out["proximity_pct"], raw["move_pct"].abs())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/research/test_dataset_ticks.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the builder**

`research/dataset/ticks.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/research/test_dataset_ticks.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research/dataset/ticks.py tests/research/test_dataset_ticks.py
git commit -m "research: tick-level canonical table builder with features"
```

---

## Task 14: Build the full dataset + summary

**Files:**
- Create: `research/build_dataset.py`
- Create: `docs/research/canonical_dataset.md`

- [ ] **Step 1: Implement the build entrypoint**

`research/build_dataset.py` — for every symbol in `[btc, eth, sol, xrp]` and
every timeframe in `[15m, 5m]`, over `2026-03-04`..`2026-05-22`:
- build the window table (Task 12) and concat → `data/research/windows.parquet`;
- build per-window tick tables (Task 13), concat per timeframe →
  `data/research/ticks_15m.parquet`, `data/research/ticks_5m.parquet`;
- print progress per (symbol, timeframe): windows, ticks, elapsed.
Provide `run()` and `if __name__ == "__main__"`.

- [ ] **Step 2: Run the build**

Run: `uv run python -m research.build_dataset`
Expected: completes with no crash; three Parquet files written under
`data/research/`.

- [ ] **Step 3: Validate the built dataset**

Run an inline check asserting:
- `windows.parquet` row count is within 5% of `data/outcomes.csv` 15m+5m rows;
- in `ticks_15m.parquet`: `proximity_pct` equals `move_pct.abs()` everywhere;
  `time_left_sec` is never negative; `outcome_up` is constant within each `slug`;
  `sigma_proximity` is finite for >95% of ticks with `time_left_sec > 5`.
If any assertion fails, fix the relevant builder and re-run before continuing.

- [ ] **Step 4: Write the dataset summary doc**

`docs/research/canonical_dataset.md` — document the schema of all three Parquet
files (every column, dtype, meaning), the row counts per symbol/timeframe/regime,
and how to load them. This is the reference every later plan cites.

- [ ] **Step 5: Commit**

```bash
git add research/build_dataset.py docs/research/canonical_dataset.md
git commit -m "research: build canonical dataset (Phase 1 complete)"
```

---

## Self-Review (completed by plan author)

**Spec coverage (Phases 0–1):** Phase 0 audit items all mapped — outcome
correctness + resolution-oracle determination (T4), cost realism / fee + spread
(T6), sim-vs-reality (T8), look-ahead (T7), data-quality incl. stale/crossed
books, March-early, and the top-of-book-only structural limitation (T3, T6),
executability / depth (T3 depth profile + window-table depth columns). Proximity
bug given its own task (T5). Phase 1 canonical dataset (T10–14) includes
σ-proximity, corrected proximity, odds drops/velocity/imbalance, realized vol,
and spot-move — so the noise-vs-signal label has its direct ingredient (odds
velocity vs spot move) ready for Phase 2. Sealed hold-out defined (T9).
Cross-coin / macro state and Phases 2–6 deferred to follow-on plans — stated in
Scope.

**Placeholder scan:** no TBD/TODO. Audit tasks specify exact metrics and
pass/concern thresholds rather than pre-judging findings — this is the correct
form for an investigation, not a placeholder.

**Type consistency:** `load_tick_csv`, `iter_windows`, `load_outcomes`,
`build_window_row`, `build_window_ticks` signatures consistent across tasks.
`move_pct` treated as percent everywhere. Feature function names
(`corrected_proximity_pct`, `sigma_proximity`, `realized_vol_per_sec`,
`rolling_drop_pct`, `odds_velocity`, `book_imbalance`, `spot_move_pct`) match
between definition (T10–11) and use (T13).
