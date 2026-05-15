"""Atomic file writes."""
from __future__ import annotations
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON atomically: write to tmp, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(obj, fh, default=str, indent=None)
    os.replace(tmp, path)


class JsonlAppender:
    """Append-only jsonl writer with fsync. Thread-safe.

    `line_buffered=True` (default) forces OS-level flush on every write — use
    this for durable records (trades). `line_buffered=False` uses block
    buffering (~8KB) — use this for high-volume diagnostic logs (signals)
    where losing the last few hundred entries on crash is acceptable.
    """

    def __init__(self, path: Path, fsync_every_n: int = 50, line_buffered: bool = True):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        buffering = 1 if line_buffered else -1
        self._fp = open(self._path, "a", buffering=buffering)
        self._fsync_every = fsync_every_n
        self._n_since_fsync = 0
        self._lock = threading.Lock()

    def append(self, obj: dict) -> None:
        line = json.dumps(obj, default=str) + "\n"
        with self._lock:
            self._fp.write(line)
            self._n_since_fsync += 1
            if self._n_since_fsync >= self._fsync_every:
                try:
                    self._fp.flush()
                    os.fsync(self._fp.fileno())
                except OSError:
                    pass
                self._n_since_fsync = 0

    def close(self) -> None:
        with self._lock:
            try:
                self._fp.flush()
                self._fp.close()
            except Exception:
                pass
