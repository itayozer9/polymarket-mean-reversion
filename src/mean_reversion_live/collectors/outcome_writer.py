"""Append resolved window outcomes to data/outcomes.csv (same schema as historical)."""
from __future__ import annotations
import csv
import os
import threading
from pathlib import Path
from typing import Optional

import structlog

from mean_reversion_live.clients.gamma import MarketInfo

log = structlog.get_logger(__name__)


OUTCOME_COLUMNS = [
    "timestamp_ms",
    "market_slug",
    "symbol",
    "window_start_ts",
    "window_end_ts",
    "start_price",
    "end_price",
    "outcome",
    "move_pct",
]


class OutcomeWriter:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, market: MarketInfo, end_price: Optional[float]) -> None:
        with self._lock:
            is_new = not self._path.exists() or self._path.stat().st_size == 0
            row = self._row(market, end_price)
            with open(self._path, "a", newline="") as fh:
                w = csv.writer(fh)
                if is_new:
                    w.writerow(OUTCOME_COLUMNS)
                w.writerow([row[c] for c in OUTCOME_COLUMNS])

    @staticmethod
    def _row(market: MarketInfo, end_price: Optional[float]) -> dict:
        sp = market.start_price or 0.0
        ep = end_price or 0.0
        if sp > 0:
            move_pct = (ep - sp) / sp * 100 if ep > 0 else 0.0
            outcome = "Up" if ep > sp else ("Down" if ep > 0 else "Unknown")
        else:
            move_pct = 0.0
            outcome = "Unknown"
        return {
            "timestamp_ms": int(market.window_end_ts * 1000),
            "market_slug": market.slug,
            "symbol": market.symbol,
            "window_start_ts": market.window_start_ts,
            "window_end_ts": market.window_end_ts,
            "start_price": sp,
            "end_price": ep,
            "outcome": outcome,
            "move_pct": round(move_pct, 6),
        }
