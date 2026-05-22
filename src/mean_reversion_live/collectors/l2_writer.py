"""Full-depth (L2) order-book CSV.gz writer. One file per (symbol, date).

ADDITIVE, separate stream. This does NOT touch the sacred 23-column tick schema
in `data/live/` (see tick_writer.py) nor the decision path. It snapshots the top
`L2_LEVELS` price levels per side at the same 1 Hz cadence as the tick writer,
purely for offline research (notably market-making feasibility).

Files land under `data/live_l2/<symbol>_<date>.csv.gz`.

YES-token only — why
--------------------
Polymarket Up/Down markets are binary: the NO token is the exact complement of
the YES token. For matched-size resting liquidity:
    no_bid_px  ~= 1 - yes_ask_px      no_bid_sz  ~= yes_ask_sz
    no_ask_px  ~= 1 - yes_bid_px      no_ask_sz  ~= yes_bid_sz
Capturing the NO book too would roughly double file size for data that is
reconstructable from the YES book. We therefore record only the YES token. If a
future analysis needs the *actual* (non-synthetic) NO book — e.g. to measure
cross-token arbitrage / dislocation — add a parallel ``no_*`` block here.

Segment flushing
----------------
Like macro_writer, each fsync segment is closed + reopened so the file is a
sequence of complete gzip members and is therefore readable mid-run by `gunzip`
and the stdlib gzip module. An open append-mode gzip member without a trailer
reads as "unexpected EOF".
"""
from __future__ import annotations
import datetime as dt
import gzip
import os
import threading
from pathlib import Path
from typing import Dict, List

import structlog

log = structlog.get_logger(__name__)

# Number of price levels captured per side (per token).
L2_LEVELS: int = 10


def _utc_date_str(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def _build_columns(levels: int) -> List[str]:
    cols = [
        "timestamp_ms",
        "market_slug",
        "symbol",
        "seconds_into_window",
    ]
    for i in range(1, levels + 1):
        cols.append(f"bid_px_{i}")
        cols.append(f"bid_sz_{i}")
    for i in range(1, levels + 1):
        cols.append(f"ask_px_{i}")
        cols.append(f"ask_sz_{i}")
    return cols


# Fixed schema — built once at import so writer and tests agree.
L2_COLUMNS: List[str] = _build_columns(L2_LEVELS)


class L2CsvGzAppender:
    """Per-symbol-per-date gzip CSV appender for full-depth L2 snapshots."""

    def __init__(self, base_dir: Path, levels: int = L2_LEVELS, fsync_every_n_rows: int = 60):
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._levels = levels
        self._columns = _build_columns(levels)
        self._files: Dict[tuple, gzip.GzipFile] = {}      # (symbol, date_str) -> open file
        self._rows_since_fsync: Dict[tuple, int] = {}
        self._fsync_every = fsync_every_n_rows
        self._lock = threading.Lock()

    @property
    def columns(self) -> List[str]:
        return list(self._columns)

    @property
    def levels(self) -> int:
        return self._levels

    def _open(self, symbol: str, date_str: str) -> gzip.GzipFile:
        path = self._base / f"{symbol}_{date_str}.csv.gz"
        is_new = not path.exists() or path.stat().st_size == 0
        f = gzip.open(str(path), mode="ab", compresslevel=6)
        if is_new:
            header = (",".join(self._columns) + "\n").encode("utf-8")
            f.write(header)
        return f

    def append(self, row: Dict[str, object]) -> None:
        """Append one L2 snapshot row. Required keys: see ``columns``."""
        symbol = str(row.get("symbol"))
        ts_ms = int(row.get("timestamp_ms", 0))
        date_str = _utc_date_str(ts_ms)
        key = (symbol, date_str)
        with self._lock:
            f = self._files.get(key)
            if f is None:
                f = self._open(symbol, date_str)
                self._files[key] = f
                self._rows_since_fsync[key] = 0
            values = []
            for c in self._columns:
                v = row.get(c, "")
                values.append("" if v is None else str(v))
            line = (",".join(values) + "\n").encode("utf-8")
            f.write(line)
            self._rows_since_fsync[key] += 1
            if self._rows_since_fsync[key] >= self._fsync_every:
                # Close + reopen so each segment is a complete gzip member,
                # readable mid-run (same trick as macro_writer).
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


def book_to_l2_row(
    *,
    timestamp_ms: int,
    market_slug: str,
    symbol: str,
    seconds_into_window: int,
    bids: List,
    asks: List,
) -> Dict[str, object]:
    """Build an L2 row dict from `OrderBook.top_levels()` output.

    `bids` / `asks` are lists of (price, size) tuples, best-first, already
    padded to L2_LEVELS by `OrderBook.top_levels`.
    """
    row: Dict[str, object] = {
        "timestamp_ms": timestamp_ms,
        "market_slug": market_slug,
        "symbol": symbol,
        "seconds_into_window": seconds_into_window,
    }
    for i, (px, sz) in enumerate(bids, start=1):
        row[f"bid_px_{i}"] = px
        row[f"bid_sz_{i}"] = sz
    for i, (px, sz) in enumerate(asks, start=1):
        row[f"ask_px_{i}"] = px
        row[f"ask_sz_{i}"] = sz
    return row
