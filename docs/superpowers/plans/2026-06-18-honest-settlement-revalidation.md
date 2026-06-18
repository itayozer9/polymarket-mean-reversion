# Honest-Settlement Fix + Full Re-Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the research stack settle on Polymarket's official on-chain outcome (parity-pinned to real money), then re-validate the deployed strategies + the full hypothesis sweep on honest labels.

**Architecture:** A backfiller fetches `/markets?slug=X&closed=true` for all window slugs and caches `outcomePrices`-derived UP/DOWN. `official_outcome_by_slug()` prefers that cache (falls back to the reconstructed Chainlink for missing slugs). `edge_lab.cl_outcomes()` — the single function every backtest/sweep/re-score settles through — switches to it, so the whole pipeline becomes honest with no per-tool changes.

**Tech Stack:** Python, requests + ThreadPoolExecutor (one-time batch), pandas, pyarrow, pytest, uv. Spec: `docs/superpowers/specs/2026-06-18-honest-settlement-revalidation-design.md`.

---

## File Structure

- **Create** `research/dataset/official_outcomes.py` — fetch + parse + cache official outcomes; `official_outcome_by_slug()` (official-preferred, reconstructed-fallback). One responsibility: the honest settlement label.
- **Modify** `research/analysis/edge_lab.py` — `cl_outcomes()` returns `official_outcome_by_slug()` (the single switch point); clearable lru_cache.
- **Create** `tests/research/test_official_outcomes.py` — pure parse unit tests + the load-bearing parity test vs `settlements.jsonl`.
- **Create** `docs/research/HONEST_SETTLEMENT_2026-06-18.md` — re-validation results + project-wide bias quantification.
- (Data, gitignored) `data/research/official_outcomes.parquet` — the cache.

---

## Task 1: Official-outcome fetcher + parser

**Files:** Create `research/dataset/official_outcomes.py`; Test `tests/research/test_official_outcomes.py`

- [ ] **Step 1: Write the failing (pure, no-network) parse tests**

```python
# tests/research/test_official_outcomes.py
import pytest
from research.dataset.official_outcomes import parse_official_outcome


def test_parse_up():
    assert parse_official_outcome(
        {"closed": True, "outcomes": ["Up", "Down"], "outcomePrices": ["1", "0"]}) == "UP"

def test_parse_down():
    assert parse_official_outcome(
        {"closed": True, "outcomes": ["Up", "Down"], "outcomePrices": ["0", "1"]}) == "DOWN"

def test_parse_yes_no_aliases():
    assert parse_official_outcome(
        {"closed": True, "outcomes": ["Yes", "No"], "outcomePrices": ["1", "0"]}) == "UP"
    assert parse_official_outcome(
        {"closed": True, "outcomes": ["Yes", "No"], "outcomePrices": ["0", "1"]}) == "DOWN"

def test_parse_unresolved_or_missing():
    assert parse_official_outcome({"closed": True, "outcomes": ["Up", "Down"],
                                   "outcomePrices": ["0.5", "0.5"]}) is None   # no >=0.99
    assert parse_official_outcome({"closed": False, "outcomes": ["Up", "Down"],
                                   "outcomePrices": ["1", "0"]}) is None       # not closed
    assert parse_official_outcome(None) is None
    assert parse_official_outcome({}) is None

def test_parse_json_string_fields():
    # gamma sometimes returns these as JSON strings, not lists
    assert parse_official_outcome(
        {"closed": True, "outcomes": '["Up", "Down"]', "outcomePrices": '["1", "0"]'}) == "UP"
```

- [ ] **Step 2: Run to confirm fail**

Run: `uv run pytest tests/research/test_official_outcomes.py -v`
Expected: FAIL (`ModuleNotFoundError: research.dataset.official_outcomes`).

- [ ] **Step 3: Implement the module**

```python
# research/dataset/official_outcomes.py
"""Official Polymarket on-chain outcomes for crypto Up/Down windows — the real-money
settlement truth, replacing the optimistically-biased reconstructed Chainlink label.

The reconstructed label (resettle_chainlink: cl_end>=cl_start from as-of prices) disagrees
with the official resolution on ~17% of clean windows, ~4:1 optimistic near-strike (verified
vs data/live/settlements.jsonl, 2026-06-18). This module fetches the official outcome the
executor already books live (Gamma /markets?slug=X&closed=true -> outcomePrices) for every
window slug, caches it, and exposes official_outcome_by_slug() preferring official over recon.

Run:  uv run python -m research.dataset.official_outcomes        # backfill all joined slugs
Out:  data/research/official_outcomes.parquet  (slug, official_up in {1.0,0.0,NaN})
"""
from __future__ import annotations
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from mean_reversion_live.config import get_settings

OUT = os.path.join("data", "research", "official_outcomes.parquet")


def parse_official_outcome(doc) -> str | None:
    """Winning side ("UP"/"DOWN") from a resolved Gamma market doc, else None.
    Mirrors scripts/live_executor.py:gamma_resolution parse exactly (the real-money path)."""
    if not doc or not doc.get("closed"):
        return None
    prices = doc.get("outcomePrices")
    prices = json.loads(prices) if isinstance(prices, str) else (prices or [])
    outs = doc.get("outcomes")
    outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
    if len(prices) < 2 or len(outs) < 2:
        return None
    try:
        fp = [float(p) for p in prices]
    except (TypeError, ValueError):
        return None
    win_idx = next((i for i, p in enumerate(fp) if p >= 0.99), None)
    if win_idx is None:
        return None
    o = str(outs[win_idx]).lower()
    if o in ("up", "yes"):
        return "UP"
    if o in ("down", "no"):
        return "DOWN"
    return None


def fetch_official_outcome(slug: str, base: str | None = None, timeout: int = 8) -> str | None:
    """GET the resolved market for `slug` (closed=true is required — default omits resolved
    markets) and parse its outcome. Returns None on any network/parse failure (caller retries)."""
    import requests
    base = base or get_settings().gamma_base_url
    try:
        r = requests.get(f"{base}/markets", params={"slug": slug, "closed": "true"}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    doc = (data[0] if isinstance(data, list) else data) if data else None
    return parse_official_outcome(doc)


def _to_up(o: str | None) -> float:
    return 1.0 if o == "UP" else 0.0 if o == "DOWN" else float("nan")


def build_official_outcomes(slugs, out: str = OUT, max_workers: int = 16) -> pd.DataFrame:
    """Fetch+cache official outcomes for `slugs`. Incremental: slugs already in the cache with a
    NON-null outcome are skipped; null/missing are re-fetched (they may have since resolved)."""
    cached: dict[str, float] = {}
    if os.path.exists(out):
        c = pd.read_parquet(out)
        cached = dict(zip(c["slug"], c["official_up"]))
    todo = [s for s in dict.fromkeys(slugs) if not (s in cached and pd.notna(cached[s]))]
    fetched: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_official_outcome, s): s for s in todo}
        for f in as_completed(futs):
            fetched[futs[f]] = _to_up(f.result())
    merged = {**cached, **fetched}
    df = pd.DataFrame([{"slug": s, "official_up": v} for s, v in merged.items()])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_parquet(out, index=False)
    return df


def official_outcome_by_slug(out: str = OUT) -> pd.DataFrame:
    """slug -> cl_up (1/0), preferring the OFFICIAL outcome; falls back to the reconstructed
    Chainlink (resettle_chainlink) for slugs with no official outcome (unresolved / not cached).
    Prints the coverage so a low official-coverage range is visible, not silent."""
    from research.analysis.resettle_chainlink import chainlink_outcome_by_slug
    recon = chainlink_outcome_by_slug()                       # [slug, cl_up]
    if not os.path.exists(out):
        print("[official_outcomes] no cache yet -> using reconstructed Chainlink (run the backfill)")
        return recon
    off = pd.read_parquet(out)                                # [slug, official_up]
    m = recon.merge(off, on="slug", how="left")
    has_off = m["official_up"].notna()
    print(f"[official_outcomes] official coverage {has_off.mean()*100:.1f}% "
          f"({int(has_off.sum())}/{len(m)} slugs); rest fall back to reconstructed")
    m["cl_up"] = np.where(has_off, m["official_up"], m["cl_up"]).astype(int)
    return m[["slug", "cl_up"]]


def main() -> str:
    from research.analysis.edge_lab import JOINED
    slugs = pd.read_parquet(JOINED, columns=["slug"])["slug"].dropna().unique().tolist()
    print(f"[official_outcomes] backfilling {len(slugs):,} slugs ...")
    df = build_official_outcomes(slugs)
    cov = df["official_up"].notna().mean() * 100
    print(f"[official_outcomes] wrote {len(df):,} -> {OUT}  (resolved {cov:.1f}%)")
    return OUT


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to confirm pass**

Run: `uv run pytest tests/research/test_official_outcomes.py -v`
Expected: 5 passed (the parse tests; the parity test is added in Task 3).

- [ ] **Step 5: Commit**

```bash
git add research/dataset/official_outcomes.py tests/research/test_official_outcomes.py
git commit -m "feat(settle): official on-chain outcome fetcher + parser"
```

---

## Task 2: Switch `cl_outcomes()` to official settlement

**Files:** Modify `research/analysis/edge_lab.py`; Test `tests/research/test_official_outcomes.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_cl_outcomes_uses_official(monkeypatch, tmp_path):
    import pandas as pd
    import research.analysis.edge_lab as el
    import research.dataset.official_outcomes as oo
    # recon says UP(1) for both; official cache overrides slugA to DOWN(0), leaves slugB missing
    monkeypatch.setattr(oo, "chainlink_outcome_by_slug",
                        lambda: pd.DataFrame({"slug": ["A", "B"], "cl_up": [1, 1]}))
    cache = tmp_path / "off.parquet"
    pd.DataFrame({"slug": ["A"], "official_up": [0.0]}).to_parquet(cache, index=False)
    monkeypatch.setattr(oo, "OUT", str(cache))
    el.cl_outcomes.cache_clear()
    out = el.cl_outcomes().set_index("slug")["cl_up"].to_dict()
    assert out["A"] == 0     # official overrides recon
    assert out["B"] == 1     # missing official -> reconstructed fallback
    el.cl_outcomes.cache_clear()
```

- [ ] **Step 2: Run to confirm fail**

Run: `uv run pytest tests/research/test_official_outcomes.py::test_cl_outcomes_uses_official -v`
Expected: FAIL (cl_outcomes still returns raw reconstructed; slug "A" == 1).

- [ ] **Step 3: Modify `edge_lab.py`**

Change the import (near line 33) from:
```python
from research.analysis.resettle_chainlink import chainlink_outcome_by_slug
```
to:
```python
from research.analysis.resettle_chainlink import chainlink_outcome_by_slug
from research.dataset.official_outcomes import official_outcome_by_slug
```

Change `cl_outcomes()` (near line 78) body from:
```python
    return chainlink_outcome_by_slug()
```
to:
```python
    return official_outcome_by_slug()   # official on-chain outcome; recon fallback for gaps
```
(Keep the `@functools.lru_cache(maxsize=1)` decorator and update the docstring to "slug -> cl_up (official Polymarket on-chain outcome; reconstructed Chainlink fallback).")

- [ ] **Step 4: Run to confirm pass**

Run: `uv run pytest tests/research/test_official_outcomes.py::test_cl_outcomes_uses_official -v`
Expected: PASS.

- [ ] **Step 5: Regression — research suite still imports/runs**

Run: `uv run pytest tests/research/ -q`
Expected: passes (note any test that asserted a specific reconstructed win/loss and now shifts — if a failure is purely a label-value change to the official truth, update its expected value to official; if it's a real break, fix it. Report any such change.)

- [ ] **Step 6: Commit**

```bash
git add research/analysis/edge_lab.py tests/research/test_official_outcomes.py
git commit -m "feat(settle): cl_outcomes settles on official outcome (recon fallback)"
```

---

## Task 3: Parity test vs real-money `settlements.jsonl`

**Files:** Test `tests/research/test_official_outcomes.py`

This is the load-bearing test: the official label MUST match what real money was actually paid. It fetches live (project convention: real network OK, no mocking; ~unique traded slugs, fast).

- [ ] **Step 1: Write the test** (append)

```python
def test_official_matches_real_money_settlements():
    """official outcome must equal the booked outcome for every traded slug (real-money truth)."""
    import json, os, pandas as pd
    from research.dataset.official_outcomes import fetch_official_outcome
    path = "data/live/settlements.jsonl"
    if not os.path.exists(path):
        import pytest; pytest.skip("no settlements.jsonl")
    rows = [json.loads(l) for l in open(path) if l.strip()]
    s = pd.DataFrame(rows)
    s = s[s.get("backfill") != True] if "backfill" in s.columns else s
    s = s.dropna(subset=["slug", "outcome"])
    booked = {sl: str(g["outcome"].iloc[0]).upper() for sl, g in s.groupby("slug")}
    mism = []
    for slug, want in booked.items():
        got = fetch_official_outcome(slug)
        if got is None:
            continue                      # transient/unresolved at fetch time — skip, don't fail
        if got != ("UP" if want in ("UP", "YES") else "DOWN"):
            mism.append((slug, got, want))
    assert not mism, f"official-vs-booked mismatches ({len(mism)}): {mism[:5]}"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/research/test_official_outcomes.py::test_official_matches_real_money_settlements -v`
Expected: PASS (0 mismatches — the official fetch reproduces the real-money book). If it fails, the parse or the closed=true query is wrong — STOP and fix before trusting any re-validation.

- [ ] **Step 3: Commit**

```bash
git add tests/research/test_official_outcomes.py
git commit -m "test(settle): parity-pin official outcome to real-money settlements.jsonl"
```

---

## Task 4: Backfill official outcomes (data step)

**Files:** none (produces the gitignored cache)

- [ ] **Step 1: Run the backfill** (~15 min, 10,249 slugs, bounded concurrency)

Run: `uv run python -m research.dataset.official_outcomes`
Expected: `wrote N -> data/research/official_outcomes.parquet (resolved XX.X%)`. Resolved % should be high (most windows are settled). If low, investigate before re-validating.

- [ ] **Step 2: Sanity-check coverage + bias**

Run:
```bash
uv run python -c "
import pandas as pd, numpy as np
from research.analysis.resettle_chainlink import chainlink_outcome_by_slug
off=pd.read_parquet('data/research/official_outcomes.parquet')
recon=chainlink_outcome_by_slug()
m=recon.merge(off,on='slug',how='inner').dropna(subset=['official_up'])
disc=(m['cl_up']!=m['official_up'].astype(int))
print('resolved slugs:', off['official_up'].notna().sum(), '/', len(off))
print('recon-vs-official disagreement:', round(disc.mean()*100,2), '%')
"
```
Expected: a few % disagreement overall (higher near-strike). Records the project-wide label bias.

---

## Task 5: Re-validate on honest labels

**Files:** Create `docs/research/HONEST_SETTLEMENT_2026-06-18.md`

- [ ] **Step 1: Re-score the deployed strategies on official labels**

Run: `uv run python -m research.analysis.rejudge_clean --stake 5 --seed 0`
(cl_outcomes now official, so this is honest.) Capture every `VERDICT` line + `det_lwd_live`'s clean_future EV/CI.

- [ ] **Step 2: Full sweep on honest labels** (heavy; run in background)

Run: `uv run python -m research.analysis.hypothesis_sweep --future-start 2026-06-12`
then `uv run python -m research.analysis.hypothesis_select`
then `uv run python -m research.analysis.hypothesis_verify --fill-model live --extended-known --future-start 2026-06-12`
Capture: any spec with clean_future CI-lo > 0 on official settlement AND max_jaccard < 0.5 (a real, non-duplicate survivor). Expect few/none — that is the answer.

- [ ] **Step 3: Write `docs/research/HONEST_SETTLEMENT_2026-06-18.md`**

Contents: the recon-vs-official bias %, the deployed-strategy honest verdicts (esp. det_lwd CI), the full-sweep survivors (or the honest negative that none clear CI-lo>0), and the project-wide implication (how optimistic past EV was).

- [ ] **Step 4: Commit**

```bash
git add docs/research/HONEST_SETTLEMENT_2026-06-18.md docs/research/test_ledger.md
git commit -m "research(settle): honest-settlement re-validation results"
```

---

## Task 6: det_lwd posture decision + close-out (present-first)

**Files:** Modify `STATE.md` (+ `strategies.yaml` only if user demotes det_lwd)

- [ ] **Step 1: Present** det_lwd_live's honest clean CI + the sweep survivors (if any) to the user; get the keep-live-$5 / demote decision (real money, present-first).
- [ ] **Step 2: If demote** — set `det_lwd_live` `live: false` in `strategies.yaml`, validate `load_strategies` parses, full suite green, safe `run_combined` restart (executor untouched).
- [ ] **Step 3: Append a dated `STATE.md`** entry: the settlement-label bug, the fix, the honest verdicts, det_lwd decision, and the project-wide implication.
- [ ] **Step 4: Commit** `git add STATE.md strategies.yaml && git commit -m "settle: honest re-validation close-out + det_lwd decision"`

---

## Self-Review notes

- **Spec coverage:** Component 1 → Task 1; Component 2 → Task 2; Component 3 → Task 3; backfill → Task 4; re-validation (deployed + full sweep + bias) → Task 5; det_lwd decision → Task 6. All covered.
- **Type consistency:** `parse_official_outcome`, `fetch_official_outcome`, `build_official_outcomes`, `official_outcome_by_slug`, cache column `official_up` (float 1/0/NaN), `cl_up` (int) used identically across tasks. `cl_outcomes()` keeps its `[slug, cl_up]` contract so every downstream caller is unchanged.
- **Load-bearing test** = Task 3 parity vs settlements.jsonl; if it fails, do not trust Task 5.
- **No silent fallback** — `official_outcome_by_slug` prints coverage; Task 4 Step 2 quantifies bias.
