"""UTC window math for Polymarket up/down crypto markets."""
from __future__ import annotations
import time


def now_ts() -> int:
    """Current unix seconds (UTC)."""
    return int(time.time())


def window_duration(timeframe: str) -> int:
    if timeframe == "5m":
        return 300
    if timeframe == "15m":
        return 900
    if timeframe == "1h":
        return 3600
    raise ValueError(f"unknown timeframe: {timeframe!r}")


def current_window_start(timeframe: str, now: int = None) -> int:
    """Floor `now` to the most recent boundary of `timeframe`."""
    now = now if now is not None else now_ts()
    dur = window_duration(timeframe)
    return (now // dur) * dur


def next_window_start(timeframe: str, now: int = None) -> int:
    return current_window_start(timeframe, now) + window_duration(timeframe)


def seconds_into_window(window_start_ts: int, now: int = None) -> int:
    now = now if now is not None else now_ts()
    return now - window_start_ts
