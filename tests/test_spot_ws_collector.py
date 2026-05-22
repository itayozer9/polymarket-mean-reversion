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
import csv
import gzip
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.collectors.spot_ws_collector import (  # noqa: E402
    SPOT_COLUMNS,
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
    assert parse_ticker_msg({"type": "ticker", "product_id": "DOGE-USD"}) is None


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
