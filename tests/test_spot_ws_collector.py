"""Tests for the additive fast Coinbase-spot stream (Stream 2).

Covers:
  - parse_ticker_msg: ticker field extraction, non-ticker rejection,
    unknown-product rejection.
  - SpotCsvGzAppender: header, per-symbol-per-date files, segment-flush
    mid-run readability, value round-trip.

This stream is separate from the REST spot poll that feeds the sacred
23-column tick CSV's `coinbase_price` column.
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

from mean_reversion_live.collectors.spot_ws_collector import (  # noqa: E402
    SPOT_COLUMNS,
    CoinbaseSpotWsCollector,
    SpotCsvGzAppender,
    parse_ticker_msg,
)


def _read_gz_csv(path: Path):
    with gzip.open(path, "rb") as f:
        text = f.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


# ----- parse_ticker_msg -----------------------------------------------------

def test_parse_ticker_msg_basic():
    msg = {
        "type": "ticker",
        "product_id": "BTC-USD",
        "price": "76840.12",
        "best_bid": "76839.00",
        "best_ask": "76841.00",
        "last_size": "0.015",
        "volume_24h": "12345.6",
        "time": "2026-05-19T07:20:00.123456Z",
    }
    out = parse_ticker_msg(msg)
    assert out is not None
    assert out["symbol"] == "btc"
    assert out["product_id"] == "BTC-USD"
    assert out["price"] == 76840.12
    assert out["best_bid"] == 76839.0
    assert out["best_ask"] == 76841.0
    assert out["last_size"] == 0.015
    assert out["event_time"] == "2026-05-19T07:20:00.123456Z"


def test_parse_ticker_msg_rejects_non_ticker():
    assert parse_ticker_msg({"type": "subscriptions"}) is None
    assert parse_ticker_msg({"type": "heartbeat", "product_id": "BTC-USD"}) is None


def test_parse_ticker_msg_rejects_unknown_product():
    # NOTE: DOGE-USD became a REAL collected product in the 2026-07-17 capacity
    # expansion (bnb/doge/hype) — use a symbol we genuinely never collect.
    assert parse_ticker_msg({"type": "ticker", "product_id": "SHIB-USD"}) is None


def test_parse_ticker_msg_handles_missing_numeric():
    out = parse_ticker_msg({"type": "ticker", "product_id": "ETH-USD"})
    assert out is not None
    assert out["symbol"] == "eth"
    assert out["price"] == ""


# ----- SpotCsvGzAppender ----------------------------------------------------

def _spot_row(ts_ms, symbol, product_id, price=100.0):
    return {
        "timestamp_ms": ts_ms,
        "event_time": "2026-05-19T07:20:00Z",
        "symbol": symbol,
        "product_id": product_id,
        "price": price,
        "best_bid": price - 0.5,
        "best_ask": price + 0.5,
        "last_size": 1.0,
        "volume_24h": 999.0,
    }


def test_spot_writer_header_and_roundtrip(tmp_path):
    w = SpotCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    ts_ms = 1779175200_000  # 2026-05-19 UTC
    w.append(_spot_row(ts_ms, "btc", "BTC-USD", price=76840.0))
    w.close()
    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    assert files[0].name.startswith("btc_2026-05-19")
    rows = _read_gz_csv(files[0])
    assert len(rows) == 1
    rec = rows[0]
    assert list(rec.keys()) == SPOT_COLUMNS
    assert rec["symbol"] == "btc"
    assert float(rec["price"]) == 76840.0
    assert float(rec["best_ask"]) == 76840.5


def test_spot_writer_segment_flush_readable_mid_run(tmp_path):
    w = SpotCsvGzAppender(tmp_path, fsync_every_n_rows=2)
    ts0 = 1779175200_000
    for i in range(5):
        w.append(_spot_row(ts0 + i * 1000, "eth", "ETH-USD", price=2000.0 + i))
    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    rows = _read_gz_csv(files[0])
    assert len(rows) >= 4
    w.close()
    rows_after = _read_gz_csv(files[0])
    assert len(rows_after) == 5


def test_spot_writer_splits_by_symbol_and_date(tmp_path):
    w = SpotCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    day1 = 1779175200_000
    day2 = day1 + 86_400_000
    w.append(_spot_row(day1, "btc", "BTC-USD"))
    w.append(_spot_row(day1, "sol", "SOL-USD"))
    w.append(_spot_row(day2, "btc", "BTC-USD"))
    w.close()
    names = sorted(p.name for p in tmp_path.glob("*.csv.gz"))
    assert len(names) == 3
    assert any(n.startswith("btc_2026-05-19") for n in names)
    assert any(n.startswith("btc_2026-05-20") for n in names)
    assert any(n.startswith("sol_2026-05-19") for n in names)


# ----- supervisor restart behaviour -----------------------------------------

@pytest.mark.asyncio
async def test_run_respawns_dead_child(tmp_path):
    """If a child coroutine of CoinbaseSpotWsCollector.run() raises, run() must
    restart it rather than letting the stream silently stop. We monkeypatch the
    two children with a crashing floor writer that counts its starts."""
    w = SpotCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    c = CoinbaseSpotWsCollector(w, ["btc"])

    floor_starts = {"n": 0}

    async def crashing_floor():
        floor_starts["n"] += 1
        await asyncio.sleep(0.05)
        raise RuntimeError("simulated floor-writer crash")

    async def idle_ws():
        # A well-behaved child that just waits for stop.
        await c._stop.wait()

    c._floor_writer = crashing_floor
    c._ws_consume = idle_ws

    run_task = asyncio.create_task(c.run())
    await asyncio.sleep(0.4)  # long enough for several crash/respawn cycles
    c.stop()
    await asyncio.wait_for(run_task, timeout=2.0)

    # The crashing child must have been respawned multiple times — proof the
    # supervisor does not let one dead child kill the stream.
    assert floor_starts["n"] >= 2


def test_collector_runs_on_separate_thread_loop():
    """The collector is hosted on a ThreadedCollectorRunner in production. Its
    stop Event must bind to the *thread's* loop, not the constructing thread's
    — otherwise run() raises 'got Future attached to a different loop'.

    This reproduces the production wiring: construct on the main thread, run on
    a dedicated thread, then stop from the main thread."""
    import time as _time

    from mean_reversion_live.collectors.thread_runner import ThreadedCollectorRunner

    w = SpotCsvGzAppender(Path("/tmp"), fsync_every_n_rows=10_000)
    # Construct on the (main/test) thread — mirrors run_combined.amain().
    c = CoinbaseSpotWsCollector(w, ["btc"])

    floor_iters = {"n": 0}

    async def fake_floor():
        # Touches self._stop the way the real floor writer does.
        while not c._stop.is_set():
            floor_iters["n"] += 1
            try:
                await asyncio.wait_for(c._stop.wait(), timeout=0.02)
            except asyncio.TimeoutError:
                pass

    async def fake_ws():
        await c._stop.wait()

    c._floor_writer = fake_floor
    c._ws_consume = fake_ws

    runner = ThreadedCollectorRunner("spot_test", c.run, on_stop=c.stop)
    runner.start()
    _time.sleep(0.3)
    # If the Event were bound to the wrong loop, run() would have crashed and
    # floor_iters would be 0.
    assert floor_iters["n"] > 1
    runner.stop(join_timeout=5.0)
