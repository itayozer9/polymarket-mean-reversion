"""Tests for the additive executed-trade-print capture stream (Stream 1).

Covers:
  - parse_trade_event: field extraction, second/ms timestamp normalisation.
  - TradesCsvGzAppender: header, per-symbol-per-date files, segment-flush
    mid-run readability, value round-trip, unknown-symbol bucketing.

Entirely separate from the 23-column tick schema / decision path.
"""
from __future__ import annotations
import csv
import gzip
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.collectors.trades_writer import (  # noqa: E402
    TRADE_COLUMNS,
    TradesCsvGzAppender,
    parse_trade_event,
)


def _read_gz_csv(path: Path):
    with gzip.open(path, "rb") as f:
        text = f.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


# ----- parse_trade_event ----------------------------------------------------

def test_parse_trade_event_basic_fields():
    msg = {
        "event_type": "last_trade_price",
        "asset_id": "tok-yes-123",
        "price": "0.42",
        "size": "150",
        "side": "buy",
        "timestamp": "1779175200000",
        "fee_rate_bps": "10",
    }
    out = parse_trade_event(msg)
    assert out["asset_id"] == "tok-yes-123"
    assert out["price"] == 0.42
    assert out["size"] == 150.0
    assert out["side"] == "BUY"
    assert out["event_ts_ms"] == 1779175200000
    assert out["fee_rate_bps"] == 10.0


def test_parse_trade_event_seconds_timestamp_scaled_to_ms():
    # A seconds-epoch timestamp must be scaled up to ms.
    msg = {"event_type": "last_trade_price", "asset_id": "t", "price": "0.5",
           "size": "1", "side": "SELL", "timestamp": "1779175200"}
    out = parse_trade_event(msg)
    assert out["event_ts_ms"] == 1779175200000


def test_parse_trade_event_market_field_fallback():
    # Some payloads carry the token id under `market` instead of `asset_id`.
    msg = {"type": "last_trade_price", "market": "tok-no-9", "price": "0.3", "size": "2"}
    out = parse_trade_event(msg)
    assert out["asset_id"] == "tok-no-9"
    assert out["side"] == ""  # missing side -> empty string


def test_parse_trade_event_missing_numeric_fields():
    out = parse_trade_event({"event_type": "last_trade_price", "asset_id": "x"})
    assert out["price"] == ""
    assert out["size"] == ""
    assert out["event_ts_ms"] == 0


# ----- TradesCsvGzAppender --------------------------------------------------

def _trade_row(ts_ms, slug, symbol, outcome="yes", asset_id="tok1"):
    return {
        "timestamp_ms": ts_ms,
        "event_ts_ms": ts_ms - 5,
        "market_slug": slug,
        "symbol": symbol,
        "asset_id": asset_id,
        "outcome": outcome,
        "price": 0.37,
        "size": 88.0,
        "side": "BUY",
        "fee_rate_bps": 0.0,
    }


def test_trades_writer_header_and_roundtrip(tmp_path):
    w = TradesCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    ts_ms = 1779175200_000  # 2026-05-19 07:20:00 UTC
    w.append(_trade_row(ts_ms, "btc-updown-15m-1779175200", "btc"))
    w.close()

    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    assert files[0].name.startswith("btc_2026-05-19")
    rows = _read_gz_csv(files[0])
    assert len(rows) == 1
    rec = rows[0]
    assert list(rec.keys()) == TRADE_COLUMNS
    assert rec["symbol"] == "btc"
    assert rec["outcome"] == "yes"
    assert float(rec["price"]) == 0.37
    assert float(rec["size"]) == 88.0
    assert rec["side"] == "BUY"


def test_trades_writer_segment_flush_readable_mid_run(tmp_path):
    w = TradesCsvGzAppender(tmp_path, fsync_every_n_rows=2)
    ts0 = 1779175200_000
    for i in range(5):
        w.append(_trade_row(ts0 + i * 1000, f"btc-updown-15m-{1779175200 + i}", "btc"))
    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    rows = _read_gz_csv(files[0])
    assert len(rows) >= 4  # 4 in 2 closed segments; 5th may be in open member
    w.close()
    rows_after = _read_gz_csv(files[0])
    assert len(rows_after) == 5


def test_trades_writer_splits_by_symbol_and_date(tmp_path):
    w = TradesCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    day1 = 1779175200_000
    day2 = day1 + 86_400_000
    w.append(_trade_row(day1, "btc-updown-15m-1779175200", "btc"))
    w.append(_trade_row(day1, "eth-updown-15m-1779175200", "eth"))
    w.append(_trade_row(day2, "btc-updown-15m-1779261600", "btc"))
    w.close()
    names = sorted(p.name for p in tmp_path.glob("*.csv.gz"))
    assert len(names) == 3
    assert any(n.startswith("btc_2026-05-19") for n in names)
    assert any(n.startswith("btc_2026-05-20") for n in names)
    assert any(n.startswith("eth_2026-05-19") for n in names)


def test_trades_writer_unknown_symbol_bucketed(tmp_path):
    # A trade we could not map to a market must NOT be dropped.
    w = TradesCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    row = _trade_row(1779175200_000, "", "")
    w.append(row)
    w.close()
    names = [p.name for p in tmp_path.glob("*.csv.gz")]
    assert len(names) == 1
    assert names[0].startswith("unknown_")
