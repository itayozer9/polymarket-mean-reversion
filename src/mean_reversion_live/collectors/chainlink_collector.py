"""Chainlink on-chain oracle price-feed collector (Polygon).

ADDITIVE, separate stream. The 23-column tick CSV's `chainlink_price` column is
left at its hardcoded 0.0 — this module does NOT change that. It writes a
parallel research stream under `data/live_chainlink/<symbol>_<date>.csv.gz`.

Why this stream
---------------
Polymarket's crypto Up/Down markets resolve against **Chainlink** USD price
feeds, not Coinbase. The resolution rule compares the Chainlink price at window
open vs window close. The bot has never captured the actual oracle value — the
research question is the oracle's *update cadence and lag*: Chainlink feeds are
push-based with a deviation threshold + heartbeat, so the price only moves on
chain every N seconds. The `updatedAt` timestamp from `latestRoundData()` is the
key signal: it reveals exactly when the oracle last refreshed.

How
---
Each Chainlink USD feed exposes a proxy ("aggregator") contract on Polygon
mainnet implementing the standard `AggregatorV3Interface`. We call
`latestRoundData()` via `eth_call` against a public Polygon JSON-RPC endpoint
and decode the returned tuple:
    (roundId uint80, answer int256, startedAt uint256,
     updatedAt uint256, answeredInRound uint80)
`answer` is scaled by `decimals()` (8 for all USD feeds here).

We use raw JSON-RPC over aiohttp rather than pulling in `web3` — the single
`eth_call` we need (selector + 5-word ABI decode) is trivial, and avoiding the
dependency keeps the deployment light. The RPC URL is configurable via the
`POLYGON_RPC_URL` env var.

Feed addresses (Polygon mainnet, verified against docs.chain.link):
    BTC/USD  0xc907E116054Ad103354f2D350FD2514433D57F6f
    ETH/USD  0xF9680D99D6C9589e2a93a78A04A279e509205945
    SOL/USD  0x10C8264C0935b3B9870013e057f330Ff3e9C56dC
    XRP/USD  0x785ba89291f676b5386652eB12b30cF361020694
"""
from __future__ import annotations
import asyncio
import datetime as dt
import gzip
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
import structlog

log = structlog.get_logger(__name__)

# Chainlink AggregatorV3Interface proxy addresses on Polygon mainnet.
CHAINLINK_FEEDS: Dict[str, str] = {
    "btc": "0xc907E116054Ad103354f2D350FD2514433D57F6f",
    "eth": "0xF9680D99D6C9589e2a93a78A04A279e509205945",
    "sol": "0x10C8264C0935b3B9870013e057f330Ff3e9C56dC",
    "xrp": "0x785ba89291f676b5386652eB12b30cF361020694",
}

# All Chainlink USD feeds use 8 decimals; confirmed live for all four above.
CHAINLINK_DECIMALS = 8

# Public Polygon RPC. polygon-rpc.com / ankr / drpc now gate behind API keys;
# the Tenderly public gateway answers `eth_call` for Chainlink feeds unauthed.
# Override with the POLYGON_RPC_URL env var if this endpoint rate-limits.
DEFAULT_POLYGON_RPC = "https://polygon.gateway.tenderly.co"

# `latestRoundData()` function selector (first 4 bytes of keccak256 signature).
_LATEST_ROUND_DATA_SELECTOR = "0xfeaf968c"

CHAINLINK_COLUMNS: List[str] = [
    "timestamp_ms",   # local poll time (ms epoch, UTC) — when WE queried
    "symbol",         # btc | eth | sol | xrp
    "feed_address",   # the aggregator proxy contract
    "round_id",       # Chainlink roundId (uint80)
    "answer_raw",     # raw int256 answer (scaled by 10**decimals)
    "price",          # answer_raw / 10**decimals — human-readable USD price
    "started_at",     # round startedAt (s epoch)
    "updated_at",     # round updatedAt (s epoch) — KEY SIGNAL: oracle refresh time
    "answered_in_round",  # answeredInRound (uint80)
    "oracle_age_s",   # timestamp_ms/1000 - updated_at — how stale the oracle was
]


def _utc_date_str(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def _to_signed(value: int, bits: int = 256) -> int:
    """Interpret an unsigned big-int as a two's-complement signed integer."""
    if value >= 1 << (bits - 1):
        value -= 1 << bits
    return value


def decode_latest_round_data(hex_result: str) -> Optional[Dict[str, int]]:
    """Decode the ABI-encoded return of `latestRoundData()`.

    Layout: 5 consecutive 32-byte words —
        roundId (uint80), answer (int256), startedAt (uint256),
        updatedAt (uint256), answeredInRound (uint80).
    Returns None if the payload is empty / malformed.
    """
    if not hex_result or hex_result in ("0x", "0x0"):
        return None
    h = hex_result[2:] if hex_result.startswith("0x") else hex_result
    if len(h) < 5 * 64:
        return None
    words = [int(h[i * 64:(i + 1) * 64], 16) for i in range(5)]
    return {
        "round_id": words[0],
        "answer_raw": _to_signed(words[1]),
        "started_at": words[2],
        "updated_at": words[3],
        "answered_in_round": words[4],
    }


class ChainlinkCsvGzAppender:
    """Per-symbol-per-date gzip CSV appender for Chainlink oracle reads."""

    def __init__(self, base_dir: Path, fsync_every_n_rows: int = 10):
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._files: Dict[tuple, gzip.GzipFile] = {}
        self._rows_since_fsync: Dict[tuple, int] = {}
        self._fsync_every = fsync_every_n_rows
        self._lock = threading.Lock()

    @property
    def columns(self) -> List[str]:
        return list(CHAINLINK_COLUMNS)

    def _open(self, symbol: str, date_str: str) -> gzip.GzipFile:
        path = self._base / f"{symbol}_{date_str}.csv.gz"
        is_new = not path.exists() or path.stat().st_size == 0
        f = gzip.open(str(path), mode="ab", compresslevel=6)
        if is_new:
            header = (",".join(CHAINLINK_COLUMNS) + "\n").encode("utf-8")
            f.write(header)
        return f

    def append(self, row: Dict[str, object]) -> None:
        symbol = str(row.get("symbol") or "unknown")
        ts_ms = int(row.get("timestamp_ms", 0) or 0)
        date_str = _utc_date_str(ts_ms)
        key = (symbol, date_str)
        with self._lock:
            f = self._files.get(key)
            if f is None:
                f = self._open(symbol, date_str)
                self._files[key] = f
                self._rows_since_fsync[key] = 0
            values = []
            for c in CHAINLINK_COLUMNS:
                v = row.get(c, "")
                values.append("" if v is None else str(v))
            line = (",".join(values) + "\n").encode("utf-8")
            f.write(line)
            self._rows_since_fsync[key] += 1
            if self._rows_since_fsync[key] >= self._fsync_every:
                try:
                    f.close()
                except Exception:
                    pass
                f = self._open(symbol, date_str)
                self._files[key] = f
                self._rows_since_fsync[key] = 0

    def close(self) -> None:
        with self._lock:
            for f in self._files.values():
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
            self._files.clear()


async def _eth_call_latest_round(
    session: aiohttp.ClientSession, rpc_url: str, feed_address: str
) -> Optional[Dict[str, int]]:
    """Query `latestRoundData()` for one feed. Returns decoded dict or None."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": feed_address, "data": _LATEST_ROUND_DATA_SELECTOR}, "latest"],
    }
    async with session.post(rpc_url, json=payload) as resp:
        resp.raise_for_status()
        data = await resp.json()
    if not isinstance(data, dict) or "result" not in data:
        return None
    return decode_latest_round_data(data["result"])


async def chainlink_loop(
    writer: ChainlinkCsvGzAppender,
    symbols: List[str],
    stop_event: asyncio.Event,
    rpc_url: str = DEFAULT_POLYGON_RPC,
    poll_interval_sec: float = 15.0,
) -> None:
    """Poll each Chainlink feed every `poll_interval_sec` and write a row.

    Best-effort: an RPC failure for one symbol logs a warning and is skipped;
    it never raises into the caller. The polling cadence (15 s) is finer than
    the feeds' on-chain heartbeat, so successive rows with the same `round_id`
    /`updated_at` directly measure the oracle's true refresh interval.
    """
    feeds = {s: CHAINLINK_FEEDS[s] for s in symbols if s in CHAINLINK_FEEDS}
    if not feeds:
        log.warning("chainlink_no_feeds", symbols=symbols)
        return
    log.info("chainlink_loop_starting", feeds=list(feeds.keys()), rpc=rpc_url,
             poll_interval_sec=poll_interval_sec)
    timeout = aiohttp.ClientTimeout(total=20)
    rows_ok = 0
    rows_err = 0
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while not stop_event.is_set():
            t0 = time.time()
            for symbol, addr in feeds.items():
                if stop_event.is_set():
                    break
                try:
                    decoded = await _eth_call_latest_round(session, rpc_url, addr)
                    now_ms = int(time.time() * 1000)
                    if decoded is None:
                        rows_err += 1
                        log.warning("chainlink_empty_result", symbol=symbol)
                        continue
                    price = decoded["answer_raw"] / (10 ** CHAINLINK_DECIMALS)
                    updated_at = decoded["updated_at"]
                    row = {
                        "timestamp_ms": now_ms,
                        "symbol": symbol,
                        "feed_address": addr,
                        "round_id": decoded["round_id"],
                        "answer_raw": decoded["answer_raw"],
                        "price": price,
                        "started_at": decoded["started_at"],
                        "updated_at": updated_at,
                        "answered_in_round": decoded["answered_in_round"],
                        "oracle_age_s": (now_ms // 1000) - updated_at if updated_at else "",
                    }
                    writer.append(row)
                    rows_ok += 1
                except Exception as e:
                    rows_err += 1
                    log.warning("chainlink_fetch_failed", symbol=symbol, err=str(e))
                # Small stagger so we don't burst all 4 RPC calls at once.
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.2)
                except asyncio.TimeoutError:
                    pass
            if rows_ok + rows_err >= 40:
                log.info("chainlink_status", rows_ok=rows_ok, rows_err=rows_err)
                rows_ok = 0
                rows_err = 0
            elapsed = time.time() - t0
            sleep_left = max(0.0, poll_interval_sec - elapsed)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_left)
            except asyncio.TimeoutError:
                pass
