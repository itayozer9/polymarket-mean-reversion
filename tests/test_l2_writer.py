"""Tests for the additive L2 (full-depth) capture stream.

Covers:
  - OrderBook.top_levels: ordering, top-N truncation, short-side padding.
  - L2CsvGzAppender: header, per-symbol-per-date files, mid-run readability
    (segment-flushed gzip), round-trip of values.
  - book_to_l2_row: schema completeness.

These are entirely separate from the 23-column tick schema / decision path.
"""
from __future__ import annotations
import csv
import gzip
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.collectors.l2_writer import (  # noqa: E402
    L2_COLUMNS,
    L2_LEVELS,
    L2CsvGzAppender,
    book_to_l2_row,
)
from mean_reversion_live.collectors.ws_collector import OrderBook  # noqa: E402


# ----- OrderBook.top_levels -------------------------------------------------

def test_top_levels_orders_best_first():
    ob = OrderBook()
    ob.apply_book_snapshot(
        {
            "bids": [
                {"price": "0.40", "size": "100"},
                {"price": "0.45", "size": "200"},
                {"price": "0.42", "size": "50"},
            ],
            "asks": [
                {"price": "0.55", "size": "300"},
                {"price": "0.50", "size": "150"},
                {"price": "0.52", "size": "75"},
            ],
        }
    )
    bids, asks = ob.top_levels(10)
    # Bids: best (highest price) first.
    assert [p for p, _ in bids[:3]] == [0.45, 0.42, 0.40]
    assert [s for _, s in bids[:3]] == [200.0, 50.0, 100.0]
    # Asks: best (lowest price) first.
    assert [p for p, _ in asks[:3]] == [0.50, 0.52, 0.55]
    assert [s for _, s in asks[:3]] == [150.0, 75.0, 300.0]


def test_top_levels_pads_short_side():
    ob = OrderBook()
    ob.apply_book_snapshot(
        {"bids": [{"price": "0.40", "size": "100"}], "asks": []}
    )
    bids, asks = ob.top_levels(10)
    assert len(bids) == 10
    assert len(asks) == 10
    assert bids[0] == (0.40, 100.0)
    assert bids[1:] == [(0.0, 0.0)] * 9
    assert asks == [(0.0, 0.0)] * 10


def test_top_levels_truncates_to_n():
    ob = OrderBook()
    ob.apply_book_snapshot(
        {
            "bids": [{"price": str(0.01 * i), "size": "10"} for i in range(1, 26)],
            "asks": [{"price": str(0.50 + 0.01 * i), "size": "10"} for i in range(1, 26)],
        }
    )
    bids, asks = ob.top_levels(10)
    assert len(bids) == 10
    assert len(asks) == 10
    # Best bid = highest price among 0.01..0.25 → 0.25.
    assert bids[0][0] == 0.25
    # 10th-best bid is 0.16.
    assert round(bids[9][0], 2) == 0.16


def test_top_levels_reflects_price_change():
    ob = OrderBook()
    ob.apply_book_snapshot(
        {"bids": [{"price": "0.40", "size": "100"}], "asks": [{"price": "0.60", "size": "100"}]}
    )
    # New, better bid arrives; original ask is removed (size 0).
    ob.apply_price_change(
        {
            "changes": [
                {"price": "0.45", "size": "250", "side": "BUY"},
                {"price": "0.60", "size": "0", "side": "SELL"},
            ]
        }
    )
    bids, asks = ob.top_levels(10)
    assert bids[0] == (0.45, 250.0)
    assert bids[1] == (0.40, 100.0)
    assert asks == [(0.0, 0.0)] * 10


# ----- L2CsvGzAppender ------------------------------------------------------

def _make_row(ts_ms: int, slug: str, symbol: str, levels: int):
    ob = OrderBook()
    ob.apply_book_snapshot(
        {
            "bids": [{"price": "0.30", "size": "10"}, {"price": "0.29", "size": "20"}],
            "asks": [{"price": "0.31", "size": "15"}, {"price": "0.32", "size": "25"}],
        }
    )
    bids, asks = ob.top_levels(levels)
    return book_to_l2_row(
        timestamp_ms=ts_ms,
        market_slug=slug,
        symbol=symbol,
        seconds_into_window=ts_ms // 1000 % 900,
        bids=bids,
        asks=asks,
    )


def _read_gz_csv(path: Path):
    with gzip.open(path, "rb") as f:
        text = f.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def test_writer_header_and_roundtrip(tmp_path):
    w = L2CsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    ts_ms = 1779175200_000  # 2026-05-19 07:20:00 UTC
    row = _make_row(ts_ms, "btc-updown-15m-1779175200", "btc", w.levels)
    w.append(row)
    w.close()

    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    assert files[0].name.startswith("btc_")

    rows = _read_gz_csv(files[0])
    assert len(rows) == 1
    rec = rows[0]
    assert list(rec.keys()) == L2_COLUMNS
    assert rec["symbol"] == "btc"
    assert rec["market_slug"] == "btc-updown-15m-1779175200"
    assert float(rec["bid_px_1"]) == 0.30
    assert float(rec["bid_sz_1"]) == 10.0
    assert float(rec["ask_px_1"]) == 0.31
    assert float(rec["ask_sz_2"]) == 25.0
    # Padded levels.
    assert float(rec[f"bid_px_{L2_LEVELS}"]) == 0.0


def test_writer_segment_flush_readable_mid_run(tmp_path):
    """With a small fsync interval, the file is closed+reopened per segment and
    must be fully readable while the writer is still open (mid-run)."""
    w = L2CsvGzAppender(tmp_path, fsync_every_n_rows=2)
    ts0 = 1779175200_000
    for i in range(5):
        w.append(_make_row(ts0 + i * 1000, f"btc-updown-15m-{1779175200 + i}", "btc", w.levels))
    # Do NOT close — simulate reading mid-run.
    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    rows = _read_gz_csv(files[0])
    # 4 rows committed in 2 closed segments; the 5th may be in the open member.
    assert len(rows) >= 4
    w.close()
    rows_after = _read_gz_csv(files[0])
    assert len(rows_after) == 5


def test_writer_splits_by_symbol_and_date(tmp_path):
    w = L2CsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    day1 = 1779175200_000          # 2026-05-19 UTC
    day2 = day1 + 86_400_000       # 2026-05-20 UTC
    w.append(_make_row(day1, "btc-updown-15m-1779175200", "btc", w.levels))
    w.append(_make_row(day1, "eth-updown-15m-1779175200", "eth", w.levels))
    w.append(_make_row(day2, "btc-updown-15m-1779261600", "btc", w.levels))
    w.close()
    names = sorted(p.name for p in tmp_path.glob("*.csv.gz"))
    assert len(names) == 3
    assert any(n.startswith("btc_2026-05-19") for n in names)
    assert any(n.startswith("btc_2026-05-20") for n in names)
    assert any(n.startswith("eth_2026-05-19") for n in names)


def test_book_to_l2_row_full_schema():
    row = _make_row(1779175200_000, "btc-updown-15m-1779175200", "btc", L2_LEVELS)
    # Every declared column must be present.
    for col in L2_COLUMNS:
        assert col in row, f"missing column {col}"
    assert len(L2_COLUMNS) == 4 + 4 * L2_LEVELS
