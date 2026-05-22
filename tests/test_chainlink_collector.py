"""Tests for the additive Chainlink on-chain oracle stream (Stream 3).

Covers:
  - decode_latest_round_data: ABI-decoding of the 5-word return tuple,
    two's-complement handling, malformed-input rejection.
  - ChainlinkCsvGzAppender: header, per-symbol-per-date files, segment-flush
    mid-run readability, value round-trip.
  - chainlink_loop: live RPC smoke test (network) — best-effort, skipped if
    the public RPC is unreachable.

This stream is separate from the (always-0.0) `chainlink_price` column of the
sacred 23-column tick CSV.
"""
from __future__ import annotations
import asyncio
import csv
import gzip
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.collectors.chainlink_collector import (  # noqa: E402
    CHAINLINK_COLUMNS,
    CHAINLINK_DECIMALS,
    CHAINLINK_FEEDS,
    ChainlinkCsvGzAppender,
    chainlink_loop,
    decode_latest_round_data,
)


def _read_gz_csv(path: Path):
    with gzip.open(path, "rb") as f:
        text = f.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def _word(value: int) -> str:
    """Encode an unsigned int as a 32-byte (64-hex-char) word."""
    return f"{value & ((1 << 256) - 1):064x}"


# ----- decode_latest_round_data ---------------------------------------------

def test_decode_latest_round_data_basic():
    # roundId, answer, startedAt, updatedAt, answeredInRound
    raw = "0x" + _word(12345) + _word(7683937507000) + _word(1779468000) \
        + _word(1779468050) + _word(12345)
    out = decode_latest_round_data(raw)
    assert out["round_id"] == 12345
    assert out["answer_raw"] == 7683937507000
    assert out["started_at"] == 1779468000
    assert out["updated_at"] == 1779468050
    assert out["answered_in_round"] == 12345
    # Human price with 8 decimals.
    assert out["answer_raw"] / (10 ** CHAINLINK_DECIMALS) == 76839.37507


def test_decode_latest_round_data_negative_answer():
    # int256 answer can be negative — two's complement.
    neg = (1 << 256) - 500  # encodes -500
    raw = "0x" + _word(1) + _word(neg) + _word(0) + _word(0) + _word(1)
    out = decode_latest_round_data(raw)
    assert out["answer_raw"] == -500


def test_decode_latest_round_data_rejects_empty():
    assert decode_latest_round_data("0x") is None
    assert decode_latest_round_data("") is None
    assert decode_latest_round_data("0x1234") is None  # too short


# ----- ChainlinkCsvGzAppender -----------------------------------------------

def _cl_row(ts_ms, symbol, updated_at=1779468050):
    return {
        "timestamp_ms": ts_ms,
        "symbol": symbol,
        "feed_address": CHAINLINK_FEEDS.get(symbol, "0xabc"),
        "round_id": 999,
        "answer_raw": 7683937507000,
        "price": 76839.37507,
        "started_at": updated_at - 50,
        "updated_at": updated_at,
        "answered_in_round": 999,
        "oracle_age_s": 31,
    }


def test_chainlink_writer_header_and_roundtrip(tmp_path):
    w = ChainlinkCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    ts_ms = 1779175200_000  # 2026-05-19 UTC
    w.append(_cl_row(ts_ms, "btc"))
    w.close()
    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    assert files[0].name.startswith("btc_2026-05-19")
    rows = _read_gz_csv(files[0])
    assert len(rows) == 1
    rec = rows[0]
    assert list(rec.keys()) == CHAINLINK_COLUMNS
    assert rec["symbol"] == "btc"
    assert float(rec["price"]) == 76839.37507
    assert int(rec["updated_at"]) == 1779468050
    assert rec["feed_address"] == CHAINLINK_FEEDS["btc"]


def test_chainlink_writer_segment_flush_readable_mid_run(tmp_path):
    w = ChainlinkCsvGzAppender(tmp_path, fsync_every_n_rows=2)
    ts0 = 1779175200_000
    for i in range(5):
        w.append(_cl_row(ts0 + i * 1000, "eth", updated_at=1779468000 + i))
    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    rows = _read_gz_csv(files[0])
    assert len(rows) >= 4
    w.close()
    rows_after = _read_gz_csv(files[0])
    assert len(rows_after) == 5


def test_chainlink_writer_splits_by_symbol_and_date(tmp_path):
    w = ChainlinkCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    day1 = 1779175200_000
    day2 = day1 + 86_400_000
    w.append(_cl_row(day1, "btc"))
    w.append(_cl_row(day1, "xrp"))
    w.append(_cl_row(day2, "btc"))
    w.close()
    names = sorted(p.name for p in tmp_path.glob("*.csv.gz"))
    assert len(names) == 3
    assert any(n.startswith("btc_2026-05-19") for n in names)
    assert any(n.startswith("btc_2026-05-20") for n in names)
    assert any(n.startswith("xrp_2026-05-19") for n in names)


def test_chainlink_feed_addresses_present():
    # All four symbols the bot trades must have a configured feed.
    for sym in ("btc", "eth", "sol", "xrp"):
        assert sym in CHAINLINK_FEEDS
        assert CHAINLINK_FEEDS[sym].startswith("0x")
        assert len(CHAINLINK_FEEDS[sym]) == 42


# ----- live RPC smoke test --------------------------------------------------

@pytest.mark.asyncio
async def test_chainlink_loop_live_smoke(tmp_path):
    """One real poll against the public Polygon RPC. Network-dependent; if the
    RPC is unreachable we skip rather than fail (matches 'real network is fine,
    just be quick' — but never block CI on a flaky public endpoint)."""
    w = ChainlinkCsvGzAppender(tmp_path, fsync_every_n_rows=1)
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(8.0)
        stop.set()

    task = asyncio.create_task(chainlink_loop(w, ["btc"], stop, poll_interval_sec=3.0))
    await stopper()
    try:
        await asyncio.wait_for(task, timeout=10.0)
    except asyncio.TimeoutError:
        task.cancel()
    w.close()

    files = list(tmp_path.glob("*.csv.gz"))
    if not files:
        pytest.skip("public Polygon RPC unreachable — skipping live smoke test")
    rows = _read_gz_csv(files[0])
    assert len(rows) >= 1
    rec = rows[0]
    # A real BTC/USD oracle read: price plausibly between $1k and $10M.
    assert 1_000 < float(rec["price"]) < 10_000_000
    assert int(rec["updated_at"]) > 1_700_000_000  # a sane recent epoch
