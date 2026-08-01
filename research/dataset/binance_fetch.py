"""Fetch Binance 1-second klines for the Binance(+Coinbase) composite-oracle study.

GET /api/v3/klines?interval=1s — PUBLIC REST, no key. Weight 2/request; with
limit=1000 a UTC day is ~87 requests/symbol. Polite by construction: 150 ms sleep
between requests (~800 weight/min, well under the ~1200-6000 limit), 60 s backoff
on HTTP 429/418. Geo-block / connectivity fallback hosts: api1/api2/api3.binance.com
then data-api.binance.vision (public market-data mirror).

If interval=1s is rejected for the requested historical range (error -1120 /
empty active day), the day falls back to /api/v3/aggTrades aggregated to 1-second
OHLCV — the manifest records which path produced each file.

Resumable: an existing symbol-date parquet is skipped, so rerunning after a crash
only fetches what is missing. Empty seconds (no trades on Binance) are simply
absent rows — downstream uses asof joins, and the coverage check reports gaps.

Out: data/research/binance_1s/<sym>_<YYYY-MM-DD>.parquet
     cols: ts_ms (kline openTime, UTC ms), open, high, low, close, volume
     plus _manifest.jsonl (file, rows, source, coverage, fetched_at)

Run:
  uv run python -m research.dataset.binance_fetch \
      --symbols btc,eth,sol,xrp --start 2026-05-22 --end 2026-06-09
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(REPO, "data", "research", "binance_1s")
MANIFEST = os.path.join(OUT_DIR, "_manifest.jsonl")

HOSTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]
SYM_MAP = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
SLEEP_S = 0.15          # ~6.7 req/s -> ~800 weight/min at weight 2
BACKOFF_S = 60.0        # on 429/418
DAY_MS = 86_400_000
PAGE = 1000


class BinanceBadRequest(Exception):
    """4xx with a Binance error body (carries the Binance error code)."""

    def __init__(self, status: int, code: int, msg: str):
        super().__init__(f"HTTP {status} binance code {code}: {msg}")
        self.status, self.code, self.msg = status, code, msg


_host_idx = 0  # sticky: once a host works we stay on it


def _get(path: str, params: dict, max_tries: int = 12) -> list:
    """GET with polite pacing, 429/418 backoff and host rotation on geo/conn errors.

    stdlib urllib (the repo has no requests/httpx); sequential + sleeps is the
    polite access pattern anyway.
    """
    global _host_idx
    last_err: Exception | None = None
    for _attempt in range(max_tries):
        host = HOSTS[_host_idx % len(HOSTS)]
        url = host + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"User-Agent": "mean-reversion-research/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = json.loads(e.read().decode())
            except Exception:
                body = {}
            if status in (429, 418):                     # rate limited / banned
                time.sleep(BACKOFF_S)
                continue
            if status in (451, 403):                     # geo-block -> next host
                last_err = RuntimeError(f"{host}: HTTP {status}")
                _host_idx += 1
                time.sleep(1.0)
                continue
            if 400 <= status < 500:
                raise BinanceBadRequest(status, int(body.get("code", 0)),
                                        str(body.get("msg", ""))[:200])
            last_err = RuntimeError(f"{host}: HTTP {status}")   # 5xx
            time.sleep(5.0)
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e                                  # conn refused / timeout
            _host_idx += 1
            time.sleep(1.0)
            continue
    raise RuntimeError(f"giving up after {max_tries} tries: {last_err}")


# --------------------------------------------------------------------------
# pure helpers (unit-tested)
# --------------------------------------------------------------------------

def klines_to_df(batches: list[list]) -> pd.DataFrame:
    """Flatten raw kline rows -> ts_ms, open, high, low, close, volume (sorted, deduped)."""
    cols = ["ts_ms", "open", "high", "low", "close", "volume"]
    if not batches:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(batches).iloc[:, :6]
    df.columns = cols
    df["ts_ms"] = df["ts_ms"].astype("int64")
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return (df.dropna(subset=["close"]).drop_duplicates("ts_ms")
            .sort_values("ts_ms").reset_index(drop=True))


def aggtrades_to_1s(trades: pd.DataFrame) -> pd.DataFrame:
    """Aggregate an aggTrades frame (cols T [ms], p [price], q [qty]) to 1s OHLCV
    on the kline schema (ts_ms = second openTime)."""
    cols = ["ts_ms", "open", "high", "low", "close", "volume"]
    if trades.empty:
        return pd.DataFrame(columns=cols)
    t = trades.copy()
    t["p"] = pd.to_numeric(t["p"], errors="coerce")
    t["q"] = pd.to_numeric(t["q"], errors="coerce")
    t = t.dropna(subset=["p"]).sort_values("T")
    t["ts_ms"] = (t["T"] // 1000) * 1000
    g = t.groupby("ts_ms", sort=True)
    out = pd.DataFrame({
        "open": g["p"].first(), "high": g["p"].max(),
        "low": g["p"].min(), "close": g["p"].last(), "volume": g["q"].sum(),
    }).reset_index()
    return out[cols]


def day_bounds_ms(date_str: str) -> tuple[int, int]:
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    t0 = int(d.timestamp() * 1000)
    return t0, t0 + DAY_MS


def date_range(start: str, end: str) -> list[str]:
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


# --------------------------------------------------------------------------
# fetchers
# --------------------------------------------------------------------------

def fetch_day_klines(pair: str, date_str: str) -> pd.DataFrame:
    t0, t1 = day_bounds_ms(date_str)
    rows: list[list] = []
    start = t0
    while start < t1:
        batch = _get("/api/v3/klines", dict(symbol=pair, interval="1s",
                                            startTime=start, endTime=t1 - 1, limit=PAGE))
        if not batch:
            break                       # no further klines in [start, t1)
        rows.extend(batch)
        nxt = int(batch[-1][0]) + 1000  # next second after last openTime
        if nxt <= start:                # safety against API stutter
            break
        start = nxt
        time.sleep(SLEEP_S)
    df = klines_to_df(rows)
    return df[(df["ts_ms"] >= t0) & (df["ts_ms"] < t1)].reset_index(drop=True)


def fetch_day_aggtrades(pair: str, date_str: str) -> pd.DataFrame:
    """Fallback path: page aggTrades by fromId, aggregate to 1s closes."""
    t0, t1 = day_bounds_ms(date_str)
    first = _get("/api/v3/aggTrades", dict(symbol=pair, startTime=t0,
                                           endTime=min(t0 + 3_600_000, t1) - 1, limit=PAGE))
    all_tr: list[dict] = list(first)
    while True:
        if not all_tr:
            break
        last = all_tr[-1]
        if int(last["T"]) >= t1 - 1:
            break
        time.sleep(SLEEP_S)
        batch = _get("/api/v3/aggTrades", dict(symbol=pair, fromId=int(last["a"]) + 1,
                                               limit=PAGE))
        if not batch:
            break
        all_tr.extend(batch)
        if int(batch[-1]["T"]) >= t1:
            break
    if not all_tr:
        return pd.DataFrame(columns=["ts_ms", "open", "high", "low", "close", "volume"])
    tr = pd.DataFrame(all_tr)[["T", "p", "q"]]
    tr = tr[(tr["T"] >= t0) & (tr["T"] < t1)]
    return aggtrades_to_1s(tr)


def fetch_symbol_day(sym: str, date_str: str) -> tuple[str, int, float]:
    """Fetch one symbol-date -> parquet. Returns (source, rows, coverage)."""
    pair = SYM_MAP[sym]
    try:
        df = fetch_day_klines(pair, date_str)
        source = "klines_1s"
        if df.empty:
            raise BinanceBadRequest(0, -1, "empty day from klines")
    except BinanceBadRequest as e:
        print(f"  [{sym} {date_str}] klines path failed ({e}); falling back to aggTrades",
              flush=True)
        df = fetch_day_aggtrades(pair, date_str)
        source = "aggtrades_1s"
    path = os.path.join(OUT_DIR, f"{sym}_{date_str}.parquet")
    df.to_parquet(path, index=False)
    cov = len(df) / 86_400.0
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(dict(file=os.path.basename(path), rows=int(len(df)),
                                source=source, coverage=round(cov, 4),
                                fetched_at=datetime.now(timezone.utc).isoformat())) + "\n")
    return source, len(df), cov


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="btc,eth,sol,xrp")
    ap.add_argument("--start", default="2026-05-22")
    ap.add_argument("--end", default="2026-06-09")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    syms = [s.strip().lower() for s in args.symbols.split(",") if s.strip()]
    dates = date_range(args.start, args.end)
    todo = [(s, d) for s in syms for d in dates]
    t_start = time.time()
    done = skipped = 0
    for sym, d in todo:
        path = os.path.join(OUT_DIR, f"{sym}_{d}.parquet")
        if os.path.exists(path):
            skipped += 1
            continue
        src, n, cov = fetch_symbol_day(sym, d)
        done += 1
        el = time.time() - t_start
        print(f"[{done + skipped}/{len(todo)}] {sym} {d}: {n:>6} rows "
              f"({cov * 100:5.1f}% of seconds, {src})  [{el / 60:.1f} min elapsed]",
              flush=True)
    print(f"DONE: fetched {done}, skipped {skipped} existing, "
          f"{(time.time() - t_start) / 60:.1f} min")


if __name__ == "__main__":
    main()
