"""Macro / derivatives signal collector — an ADDITIVE research stream for FUTURE
backtests: do IV regime / funding / open-interest / crowd-positioning predict the
15-minute crypto direction? Writes per-symbol-per-date gzip CSV under
data/live_macro/<symbol>_<date>.csv.gz (same shape as the other research feeds).

Signals (all public REST, no auth):
  - dvol            : Deribit implied-vol index (BTC/ETH only) — expected-move regime
  - funding_rate    : Binance perp funding — directional crowding / leverage pressure
  - mark/index/basis: Binance perp mark vs index (basis_bps) — premium/discount
  - open_interest   : Binance perp OI — positioning build-up / squeeze risk
  - ls_account_ratio: Binance global long/short account ratio — crowd positioning
  - taker_buy_ratio : Binance taker buy/sell volume ratio — aggressive flow

Runs as its OWN process (nohup), independent of Claude AND of the trading bot, so a
collector hiccup can never touch trading. Slow signals -> poll every 60s. Resilient:
any failed fetch records blank; the loop never raises. Stops on data/KILL or
data/live_macro/STOP.

Run: nohup uv run python -m mean_reversion_live.collectors.macro_collector > logs/macro_collector.log 2>&1 &
"""
from __future__ import annotations
import asyncio
import datetime as dt
import gzip
import math
import time
from pathlib import Path
from typing import List

import aiohttp
import structlog

log = structlog.get_logger(__name__)

MACRO_COLUMNS: List[str] = [
    "timestamp_ms", "symbol", "funding_rate", "mark_price", "index_price",
    "basis_bps", "open_interest", "dvol", "ls_account_ratio", "taker_buy_ratio",
]
BINANCE = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
DVOL_CUR = {"btc": "BTC", "eth": "ETH"}   # Deribit has a vol index only for BTC/ETH
FAPI = "https://fapi.binance.com"
DERIBIT = "https://www.deribit.com/api/v2"
NAN = float("nan")


async def _gj(session, url):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
        r.raise_for_status()
        return await r.json()


async def _funding(session, bsym):
    try:
        d = await _gj(session, f"{FAPI}/fapi/v1/premiumIndex?symbol={bsym}")
        fr, mk, ix = float(d["lastFundingRate"]), float(d["markPrice"]), float(d["indexPrice"])
        return fr, mk, ix, ((mk / ix - 1.0) * 1e4 if ix else NAN)
    except Exception:
        return NAN, NAN, NAN, NAN


async def _oi(session, bsym):
    try:
        d = await _gj(session, f"{FAPI}/fapi/v1/openInterest?symbol={bsym}")
        return float(d["openInterest"])
    except Exception:
        return NAN


async def _dvol(session, sym):
    cur = DVOL_CUR.get(sym)
    if not cur:
        return NAN
    try:
        now = int(time.time() * 1000)
        d = await _gj(session, f"{DERIBIT}/public/get_volatility_index_data?currency={cur}"
                               f"&start_timestamp={now - 3600000}&end_timestamp={now}&resolution=60")
        rows = d.get("result", {}).get("data", [])
        return float(rows[-1][4]) if rows else NAN
    except Exception:
        return NAN


async def _ratios(session, bsym):
    ls = tk = NAN
    try:
        d = await _gj(session, f"{FAPI}/futures/data/globalLongShortAccountRatio?symbol={bsym}&period=5m&limit=1")
        if d:
            ls = float(d[-1]["longShortRatio"])
    except Exception:
        pass
    try:
        d = await _gj(session, f"{FAPI}/futures/data/takerlongshortRatio?symbol={bsym}&period=5m&limit=1")
        if d:
            tk = float(d[-1]["buySellRatio"])
    except Exception:
        pass
    return ls, tk


def _fmt(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def _write_row(base: Path, row: dict) -> None:
    ds = dt.datetime.fromtimestamp(row["timestamp_ms"] / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")
    path = base / f"{row['symbol']}_{ds}.csv.gz"
    new = not path.exists() or path.stat().st_size == 0
    with gzip.open(path, "at") as f:
        if new:
            f.write(",".join(MACRO_COLUMNS) + "\n")
        f.write(",".join(_fmt(row.get(c)) for c in MACRO_COLUMNS) + "\n")


async def macro_loop(base_dir, symbols, stop_event, interval: int = 60) -> None:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession(headers={"User-Agent": "polymarket-research/1.0"}) as session:
        while not stop_event.is_set():
            t0 = time.time()
            n = 0
            try:
                ms = int(time.time() * 1000)
                for sym in symbols:
                    bsym = BINANCE.get(sym.lower())
                    if not bsym:
                        continue
                    fr, mk, ix, basis = await _funding(session, bsym)
                    oi = await _oi(session, bsym)
                    dvol = await _dvol(session, sym.lower())
                    ls, tk = await _ratios(session, bsym)
                    _write_row(base, {
                        "timestamp_ms": ms, "symbol": sym.lower(), "funding_rate": fr,
                        "mark_price": mk, "index_price": ix, "basis_bps": basis,
                        "open_interest": oi, "dvol": dvol,
                        "ls_account_ratio": ls, "taker_buy_ratio": tk})
                    n += 1
                log.info("macro_poll", n=n, elapsed_s=round(time.time() - t0, 1))
            except Exception as e:
                log.warning("macro_poll_failed", err=str(e))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    log.info("macro_loop_stopped")


def _main() -> None:
    repo = Path(__file__).resolve().parents[3]
    base = repo / "data" / "live_macro"
    syms = ["btc", "eth", "sol", "xrp"]

    async def _run():
        stop = asyncio.Event()

        async def _watch():
            while not stop.is_set():
                if (repo / "data" / "KILL").exists() or (base / "STOP").exists():
                    log.warning("macro_kill_sentinel"); stop.set(); break
                await asyncio.sleep(2)

        await asyncio.gather(macro_loop(base, syms, stop, interval=60), _watch())

    log.info("macro_collector_started", out=str(base))
    asyncio.run(_run())


if __name__ == "__main__":
    _main()
