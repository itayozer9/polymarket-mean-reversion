"""Incremental signals_today counter for the heartbeat.

Replaces the full-file re-parse that saturated the main event loop: the old
`_count_signals_today` parsed EVERY strategy's ENTIRE signals.jsonl (4.4 GB /
6.35M records by 2026-06-12, never rotated) with stdlib json on every 5s
heartbeat — ~42s of synchronous CPU per tick. The blocked loop starved the
Polymarket WS reader past its 20s ping timeout (connections died every
~25-30s since ~Jun 5), left order books 10-25s stale, and zeroed live fills
once 5m markets tripled the per-death resync cost.

This counter remembers a byte offset per file and parses only newly-appended
lines. Semantics: "today's signals observed since process start" — files that
exist at construction are initialized at end-of-file (their history is NOT
re-counted; a restart shows since-boot counts), files first seen later are
read from byte 0, and counts reset at UTC midnight. Steady cost per poll is
proportional to the few lines appended since the previous poll.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict

DAY_MS = 86_400_000


class SignalsTodayCounter:
    def __init__(self, jsonl_root: Path):
        self._root = Path(jsonl_root)
        self._offsets: Dict[Path, int] = {}
        self._count = 0
        self._day = None  # UTC day index of the running count
        # Existing files: skip their history — start tailing from the end.
        for f in self._iter_files():
            try:
                self._offsets[f] = f.stat().st_size
            except OSError:
                continue

    def _iter_files(self):
        if not self._root.is_dir():
            return
        for sid_dir in self._root.iterdir():
            f = sid_dir / "signals.jsonl"
            if f.exists():
                yield f

    def poll(self, now_ms: int) -> int:
        """Consume newly-appended lines; return today's running count."""
        day = now_ms // DAY_MS
        if day != self._day:
            self._day = day
            self._count = 0
        midnight_ms = day * DAY_MS
        for f in self._iter_files():
            offset = self._offsets.get(f, 0)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size <= offset:
                # unchanged (or truncated — resync to the new end)
                if size < offset:
                    self._offsets[f] = size
                continue
            try:
                with open(f, "r") as fh:
                    fh.seek(offset)
                    chunk = fh.read(size - offset)
            except OSError:
                continue
            # Only consume complete lines; a partially-written tail line is
            # left for the next poll (the writer appends line-atomically).
            last_nl = chunk.rfind("\n")
            if last_nl < 0:
                continue
            self._offsets[f] = offset + last_nl + 1
            for line in chunk[:last_nl].splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("ts_ms", 0) >= midnight_ms:
                    self._count += 1
        return self._count
