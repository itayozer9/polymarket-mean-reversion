"""Executed-trade-print CSV.gz writer. One file per (symbol, date).

ADDITIVE, separate stream. Captures Polymarket CLOB `last_trade_price` events
(executed trades) from the same `market` WS channel the order-book collector
already consumes. This does NOT touch the sacred 23-column tick schema in
`data/live/` (see tick_writer.py) nor the decision path.

Files land under `data/live_trades/<symbol>_<date>.csv.gz`.

Why capture trade prints
------------------------
The 1 Hz tick CSV records the *state* of the book each second. It cannot tell
us *when retail actually crossed the spread* — the moment of a trade, its size
and side. That timing (panic clusters, size bursts) is exactly the untested
research signal. The CLOB `market` channel emits a `last_trade_price` event on
every executed trade; the order-book collector currently ignores it.

Segment flushing
----------------
Like macro_writer / l2_writer, each fsync segment is closed + reopened so the
file is a sequence of complete gzip members and is readable mid-run by `gunzip`
and the stdlib gzip module. An open append-mode gzip member without a trailer
reads as "unexpected EOF".
"""
from __future__ import annotations
import datetime as dt
import gzip
import threading
from pathlib import Path
from typing import Dict, List

import structlog

log = structlog.get_logger(__name__)


# Fixed schema. `asset_id` is the YES/NO CLOB token the trade hit; `outcome`
# disambiguates which side of the binary market it is when known.
TRADE_COLUMNS: List[str] = [
    "timestamp_ms",      # local receive time (ms epoch, UTC)
    "event_ts_ms",       # trade timestamp from the WS payload (ms epoch), 0 if absent
    "market_slug",       # e.g. btc-updown-15m-1779175200, "" if unmapped
    "symbol",            # btc | eth | sol | xrp, "" if unmapped
    "asset_id",          # CLOB token id the trade executed against
    "outcome",           # "yes" | "no" | "" — which leg of the binary market
    "price",             # executed price (0..1)
    "size",              # executed size (shares)
    "side",              # BUY | SELL | "" — taker side as reported
    "fee_rate_bps",      # fee rate if present, "" otherwise
]


def _utc_date_str(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


class TradesCsvGzAppender:
    """Per-symbol-per-date gzip CSV appender for executed-trade prints."""

    def __init__(self, base_dir: Path, fsync_every_n_rows: int = 30):
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._files: Dict[tuple, gzip.GzipFile] = {}  # (symbol, date_str) -> open file
        self._rows_since_fsync: Dict[tuple, int] = {}
        self._fsync_every = fsync_every_n_rows
        self._lock = threading.Lock()

    @property
    def columns(self) -> List[str]:
        return list(TRADE_COLUMNS)

    def _open(self, symbol: str, date_str: str) -> gzip.GzipFile:
        path = self._base / f"{symbol}_{date_str}.csv.gz"
        is_new = not path.exists() or path.stat().st_size == 0
        f = gzip.open(str(path), mode="ab", compresslevel=6)
        if is_new:
            header = (",".join(TRADE_COLUMNS) + "\n").encode("utf-8")
            f.write(header)
        return f

    def append(self, row: Dict[str, object]) -> None:
        """Append one trade-print row. Required keys: see ``columns``.

        Trades for which we could not resolve a symbol are bucketed under the
        literal symbol ``unknown`` so they are never silently dropped.
        """
        symbol = str(row.get("symbol") or "unknown")
        ts_ms = int(row.get("timestamp_ms", 0) or 0)
        date_str = _utc_date_str(ts_ms)
        key = (symbol, date_str)
        with self._lock:
            f = self._files.get(key)
            if f is None:
                f = self._open(symbol, date_str)
                self._files[key] = f
                self._rows_since_fsync[key] = 0
            values = []
            for c in TRADE_COLUMNS:
                v = row.get(c, "")
                values.append("" if v is None else str(v))
            line = (",".join(values) + "\n").encode("utf-8")
            f.write(line)
            self._rows_since_fsync[key] += 1
            if self._rows_since_fsync[key] >= self._fsync_every:
                # Close + reopen so each segment is a complete gzip member,
                # readable mid-run (same trick as macro_writer / l2_writer).
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


def parse_trade_event(msg: dict) -> Dict[str, object]:
    """Normalise a Polymarket CLOB `last_trade_price` WS event into a row dict.

    The CLOB `market` channel emits trade events with a varying field set
    depending on API version. We read defensively. Known field names:
        event_type / type        -> "last_trade_price"
        asset_id / market        -> CLOB token id
        price                    -> executed price (string, 0..1)
        size                     -> executed size (string)
        side                     -> BUY / SELL
        timestamp                -> ms or s epoch (string)
        fee_rate_bps             -> optional

    `symbol`, `market_slug`, `outcome` and `timestamp_ms` (local) are NOT set
    here — the collector fills them in once it has resolved the asset_id to a
    market. This keeps the WS-payload parsing isolated and testable.
    """
    def _f(*keys):
        for k in keys:
            v = msg.get(k)
            if v not in (None, ""):
                return v
        return None

    price = _f("price")
    size = _f("size")
    side = _f("side")
    fee = _f("fee_rate_bps", "feeRateBps")
    asset_id = _f("asset_id", "market")

    # Event timestamp may arrive in seconds or milliseconds; normalise to ms.
    raw_ts = _f("timestamp", "match_time", "time")
    event_ts_ms = 0
    if raw_ts is not None:
        try:
            ts_val = int(float(raw_ts))
            # Heuristic: anything below ~10^12 is seconds, scale to ms.
            event_ts_ms = ts_val * 1000 if ts_val < 1_000_000_000_000 else ts_val
        except (TypeError, ValueError):
            event_ts_ms = 0

    def _num(v):
        if v is None:
            return ""
        try:
            return float(v)
        except (TypeError, ValueError):
            return ""

    return {
        "event_ts_ms": event_ts_ms,
        "asset_id": str(asset_id) if asset_id is not None else "",
        "price": _num(price),
        "size": _num(size),
        "side": str(side).upper() if side is not None else "",
        "fee_rate_bps": _num(fee),
    }
