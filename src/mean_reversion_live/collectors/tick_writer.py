"""Crash-safe gzip CSV appender. One file per (symbol, date).

We open append-mode gzip files and fsync periodically. On date rollover the
writer opens a new file for the new date. Header is written on new files only.
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


# Match the historical CSV columns EXACTLY so loaders.py works on both old + new.
TICK_COLUMNS: List[str] = [
    "timestamp_ms",
    "market_slug",
    "symbol",
    "window_start_ts",
    "window_end_ts",
    "seconds_into_window",
    "yes_best_bid",
    "yes_best_ask",
    "yes_bid_depth",
    "yes_ask_depth",
    "no_best_bid",
    "no_best_ask",
    "no_bid_depth",
    "no_ask_depth",
    "chainlink_price",
    "coinbase_price",
    "start_price",
    "move_pct",
    "yes_mid",
    "no_mid",
    "spread_yes",
    "spread_no",
    "total_mid",
]


def _utc_date_str(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


class CrashSafeCsvGzAppender:
    """Per-symbol-per-date gzip CSV appender."""

    def __init__(self, base_dir: Path, fsync_every_n_rows: int = 60):
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._files: Dict[tuple, gzip.GzipFile] = {}      # (symbol, date_str) -> open file
        self._rows_since_fsync: Dict[tuple, int] = {}
        self._fsync_every = fsync_every_n_rows
        self._lock = threading.Lock()

    def _file_for(self, symbol: str, ts_ms: int) -> tuple:
        date_str = _utc_date_str(ts_ms)
        return (symbol, date_str)

    def _open(self, symbol: str, date_str: str) -> gzip.GzipFile:
        path = self._base / f"{symbol}_{date_str}.csv.gz"
        is_new = not path.exists() or path.stat().st_size == 0
        # Open in append-binary mode via gzip.
        # NOTE: using append mode 'ab' keeps appending to the same gzip member
        # but Python's gzip can read multi-member streams. Our loader already
        # handles corrupt-tail cases tolerantly.
        f = gzip.open(str(path), mode="ab", compresslevel=6)
        if is_new:
            header = (",".join(TICK_COLUMNS) + "\n").encode("utf-8")
            f.write(header)
        return f

    def append(self, row: Dict[str, object]) -> None:
        """Append one row. Required keys: TICK_COLUMNS."""
        symbol = str(row.get("symbol"))
        ts_ms = int(row.get("timestamp_ms", 0))
        key = self._file_for(symbol, ts_ms)
        with self._lock:
            f = self._files.get(key)
            if f is None:
                f = self._open(symbol, key[1])
                self._files[key] = f
                self._rows_since_fsync[key] = 0
            # Build the CSV line in column order
            values = []
            for c in TICK_COLUMNS:
                v = row.get(c, "")
                values.append("" if v is None else str(v))
            line = (",".join(values) + "\n").encode("utf-8")
            f.write(line)
            self._rows_since_fsync[key] += 1
            if self._rows_since_fsync[key] >= self._fsync_every:
                try:
                    f.flush()
                    if hasattr(f, "fileobj") and f.fileobj is not None:
                        os.fsync(f.fileobj.fileno())
                except (OSError, AttributeError):
                    pass
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
