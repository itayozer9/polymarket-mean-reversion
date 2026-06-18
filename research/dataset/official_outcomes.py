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
from research.analysis.resettle_chainlink import chainlink_outcome_by_slug

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


def fetch_official_outcome(slug: str, base: str | None = None, timeout: int = 8,
                           retries: int = 4) -> str | None:
    """GET the resolved market for `slug` (closed=true is required — default omits resolved
    markets) and parse its outcome. Retries with backoff on transient failures (esp. HTTP 429
    rate-limits, which silently null-out a high-concurrency batch); returns None only when the
    market is genuinely unresolved or every retry failed."""
    import time
    import requests
    base = base or get_settings().gamma_base_url
    for attempt in range(retries):
        try:
            r = requests.get(f"{base}/markets", params={"slug": slug, "closed": "true"},
                             timeout=timeout)
            r.raise_for_status()
            data = r.json()
            doc = (data[0] if isinstance(data, list) else data) if data else None
            return parse_official_outcome(doc)
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(0.5 * (2 ** attempt))   # 0.5, 1, 2s backoff
    return None


def _to_up(o: str | None) -> float:
    return 1.0 if o == "UP" else 0.0 if o == "DOWN" else float("nan")


def build_official_outcomes(slugs, out: str = OUT, max_workers: int = 6) -> pd.DataFrame:
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


def official_outcome_by_slug(out: str | None = None) -> pd.DataFrame:
    """slug -> cl_up (1/0), preferring the OFFICIAL outcome; falls back to the reconstructed
    Chainlink (resettle_chainlink) for slugs with no official outcome (unresolved / not cached).
    Prints the coverage so a low official-coverage range is visible, not silent."""
    out = OUT if out is None else out
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
