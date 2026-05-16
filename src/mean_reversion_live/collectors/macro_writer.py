"""Macro-context CSV.gz writer. One file per UTC date.

Mirrors the shape of tick_writer.CrashSafeCsvGzAppender but ONE file per date
(not per symbol) — the macro snapshot is already cross-symbol. Lives under
`data/live_macro/` to keep the 23-column tick schema in `data/live/` sacred.
"""
from __future__ import annotations
import datetime as dt
import gzip
import os
import threading
from pathlib import Path
from typing import Dict, Optional

import structlog

log = structlog.get_logger(__name__)


def _utc_date_str(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


class MacroCsvGzAppender:
    """Append 1Hz cross-symbol snapshots to data/live_macro/<date>.csv.gz.

    Schema is fixed at construction from `symbols`. This avoids the trap where
    the first written row arrives BEFORE any ticks have populated MarketContext,
    which would lock the schema to just `n_symbols_dipping_5pct_60s` and silently
    drop every per-symbol column from later rows.
    """

    def __init__(self, base_dir: Path, symbols: list, fsync_every_n_rows: int = 60):
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._files: Dict[str, gzip.GzipFile] = {}  # date_str -> open file
        self._rows_since_fsync: Dict[str, int] = {}
        # Fixed schema: ts_ms first, then n_symbols_dipping_*, then per-symbol
        # block (yes_mid, drop, realized-vol horizons). Keep in sync with the
        # fields emitted by MarketContext.snapshot.
        cols = ["ts_ms", "n_symbols_dipping_5pct_60s"]
        for sym in symbols:
            cols.append(f"{sym}_yes_mid")
            cols.append(f"{sym}_drop_60s_pct")
            cols.append(f"{sym}_rv_60s_pct")
            cols.append(f"{sym}_rv_300s_pct")
        self._columns: list = cols
        self._fsync_every = fsync_every_n_rows
        self._lock = threading.Lock()

    def _open(self, date_str: str, columns: list) -> gzip.GzipFile:
        path = self._base / f"{date_str}.csv.gz"
        is_new = not path.exists() or path.stat().st_size == 0
        f = gzip.open(str(path), mode="ab", compresslevel=6)
        if is_new:
            header = (",".join(columns) + "\n").encode("utf-8")
            f.write(header)
        return f

    def append(self, row: dict) -> None:
        ts_ms = int(row.get("ts_ms", 0))
        date_str = _utc_date_str(ts_ms)
        with self._lock:
            f = self._files.get(date_str)
            if f is None:
                f = self._open(date_str, self._columns)
                self._files[date_str] = f
                self._rows_since_fsync[date_str] = 0
            values = []
            for c in self._columns:
                v = row.get(c, "")
                values.append("" if v is None else str(v))
            line = (",".join(values) + "\n").encode("utf-8")
            f.write(line)
            self._rows_since_fsync[date_str] += 1
            if self._rows_since_fsync[date_str] >= self._fsync_every:
                # Close + reopen so each segment is a complete gzip member.
                # Multi-member gzip streams are readable by `gunzip` and the
                # Python gzip module; whereas an open append-mode gzip file
                # without a closed trailer reads as "unexpected EOF" mid-write.
                try:
                    f.close()
                except Exception:
                    pass
                f = self._open(date_str, self._columns)
                self._files[date_str] = f
                self._rows_since_fsync[date_str] = 0

    def close(self) -> None:
        with self._lock:
            for f in self._files.values():
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
            self._files.clear()
