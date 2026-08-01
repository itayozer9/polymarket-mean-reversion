"""Fetch Binance USD-M futures derivative inputs for the external-inputs study
(docs/research/EXTERNAL_INPUTS_2026-06-11.md):

  1. funding rates   — GET fapi.binance.com /fapi/v1/fundingRate (8h cadence,
                       settled prints; long retention)
  2. open interest   — GET fapi.binance.com /futures/data/openInterestHist
                       period=5m. NOTE: Binance retains only ~30 days of this
                       series — fetched 2026-06-11 the window 05-22..06-10 is
                       (just) inside retention; the first ~2h of 05-22 may be
                       beyond the edge (documented in the manifest coverage).

PUBLIC REST, no key. Politeness copied from research/dataset/binance_fetch.py:
150 ms sleep between requests, 60 s backoff on 429/418, stdlib urllib only.
fapi.binance.com has no public mirror hosts, so there is no host rotation —
just retries.

Liquidation/forced-order history is NOT public (the fapi forceOrders endpoints
require auth and only cover the caller's own orders) — the cascade proxy is
built downstream from the existing 1s klines (volume+range z-spikes), see
research/analysis/external_inputs.py.

Resumable: existing parquets are skipped (funding: one file per symbol covering
the full range; OI: one file per symbol-date), so rerunning after a crash only
fetches what is missing.

Out: data/research/binance_deriv/funding_<sym>.parquet
         cols: funding_time_ms, funding_rate, mark_price
     data/research/binance_deriv/oi_<sym>_<YYYY-MM-DD>.parquet
         cols: ts_ms, sum_open_interest, sum_open_interest_value
     plus _manifest.jsonl (file, rows, fetched_at)

Run:
  uv run python -m research.dataset.binance_deriv_fetch \
      --symbols btc,eth,sol,xrp --start 2026-05-22 --end 2026-06-10
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from research.dataset.binance_fetch import SYM_MAP, date_range, day_bounds_ms

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(REPO, "data", "research", "binance_deriv")
MANIFEST = os.path.join(OUT_DIR, "_manifest.jsonl")

FAPI = "https://fapi.binance.com"
SLEEP_S = 0.15
BACKOFF_S = 60.0
PAGE_FUNDING = 1000
PAGE_OI = 500           # documented max for openInterestHist


def _get(path: str, params: dict, max_tries: int = 10) -> list:
    """GET with polite pacing and 429/418 backoff (binance_fetch pattern)."""
    last_err: Exception | None = None
    for _ in range(max_tries):
        url = FAPI + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"User-Agent": "mean-reversion-research/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 418):
                time.sleep(BACKOFF_S)
                continue
            last_err = RuntimeError(f"HTTP {e.code}: {e.read().decode()[:200]}")
            if 400 <= e.code < 500:
                raise last_err
            time.sleep(5.0)
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            time.sleep(2.0)
            continue
    raise RuntimeError(f"giving up after {max_tries} tries: {last_err}")


# --------------------------------------------------------------------------
# pure helpers (unit-tested in tests/research/test_external_inputs.py)
# --------------------------------------------------------------------------

def funding_to_df(rows: list[dict]) -> pd.DataFrame:
    """Raw /fapi/v1/fundingRate rows -> funding_time_ms, funding_rate, mark_price
    (sorted, deduped on funding_time_ms)."""
    cols = ["funding_time_ms", "funding_rate", "mark_price"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "funding_time_ms": df["fundingTime"].astype("int64"),
        "funding_rate": pd.to_numeric(df["fundingRate"], errors="coerce"),
        "mark_price": pd.to_numeric(df.get("markPrice"), errors="coerce"),
    })
    return (out.dropna(subset=["funding_rate"])
            .drop_duplicates("funding_time_ms")
            .sort_values("funding_time_ms").reset_index(drop=True))


def oi_to_df(rows: list[dict]) -> pd.DataFrame:
    """Raw /futures/data/openInterestHist rows -> ts_ms, sum_open_interest,
    sum_open_interest_value (sorted, deduped on ts_ms)."""
    cols = ["ts_ms", "sum_open_interest", "sum_open_interest_value"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "ts_ms": df["timestamp"].astype("int64"),
        "sum_open_interest": pd.to_numeric(df["sumOpenInterest"], errors="coerce"),
        "sum_open_interest_value": pd.to_numeric(
            df["sumOpenInterestValue"], errors="coerce"),
    })
    return (out.dropna(subset=["sum_open_interest"])
            .drop_duplicates("ts_ms").sort_values("ts_ms").reset_index(drop=True))


def _log(path: str, rows: int) -> None:
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(dict(file=os.path.basename(path), rows=int(rows),
                                fetched_at=datetime.now(timezone.utc).isoformat()))
                + "\n")


# --------------------------------------------------------------------------
# fetchers
# --------------------------------------------------------------------------

def fetch_funding(sym: str, start: str, end: str) -> int:
    """One parquet per symbol covering [start, end] (8h cadence -> tiny)."""
    path = os.path.join(OUT_DIR, f"funding_{sym}.parquet")
    if os.path.exists(path):
        return -1
    t0, _ = day_bounds_ms(start)
    _, t1 = day_bounds_ms(end)
    rows: list[dict] = []
    cur = t0
    while cur < t1:
        batch = _get("/fapi/v1/fundingRate",
                     dict(symbol=SYM_MAP[sym], startTime=cur, endTime=t1 - 1,
                          limit=PAGE_FUNDING))
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1]["fundingTime"]) + 1
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(SLEEP_S)
    df = funding_to_df(rows)
    df.to_parquet(path, index=False)
    _log(path, len(df))
    return len(df)


def fetch_oi_day(sym: str, date_str: str) -> int:
    """One parquet per symbol-date of 5m OI points (<=288/day)."""
    path = os.path.join(OUT_DIR, f"oi_{sym}_{date_str}.parquet")
    if os.path.exists(path):
        return -1
    t0, t1 = day_bounds_ms(date_str)
    rows: list[dict] = []
    cur = t0
    while cur < t1:
        batch = _get("/futures/data/openInterestHist",
                     dict(symbol=SYM_MAP[sym], period="5m", startTime=cur,
                          endTime=t1 - 1, limit=PAGE_OI))
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1]["timestamp"]) + 1
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(SLEEP_S)
    df = oi_to_df(rows)
    df = df[(df["ts_ms"] >= t0) & (df["ts_ms"] < t1)].reset_index(drop=True)
    df.to_parquet(path, index=False)
    _log(path, len(df))
    return len(df)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="btc,eth,sol,xrp")
    ap.add_argument("--start", default="2026-05-22")
    ap.add_argument("--end", default="2026-06-10")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    syms = [s.strip().lower() for s in args.symbols.split(",") if s.strip()]
    t_start = time.time()
    for sym in syms:
        n = fetch_funding(sym, args.start, args.end)
        print(f"funding {sym}: {'skip (exists)' if n < 0 else f'{n} rows'}",
              flush=True)
    done = skipped = 0
    dates = date_range(args.start, args.end)
    for sym in syms:
        for d in dates:
            n = fetch_oi_day(sym, d)
            if n < 0:
                skipped += 1
                continue
            done += 1
            print(f"oi {sym} {d}: {n} rows  "
                  f"[{(time.time() - t_start) / 60:.1f} min]", flush=True)
    print(f"DONE: fetched {done} OI day-files, skipped {skipped} existing, "
          f"{(time.time() - t_start) / 60:.1f} min")


if __name__ == "__main__":
    main()
