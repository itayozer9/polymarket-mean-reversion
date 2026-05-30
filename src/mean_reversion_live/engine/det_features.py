"""Shared helpers for the determinism / stale-quote strategies.

  - RollingMove: a per-window buffer of recent (sec, move_pct) for live spot
    velocity + realized-vol features (bps). move_pct = (spot-strike)/strike*100.
  - utc_hour_dow: time-of-day / day-of-week for per-trade context.

These feed the rich per-trade context log (trades_detailed.jsonl) so that after a
forward week we can slice win-rate/PnL by hour, regime, depth, distance, etc. and
discover filters (e.g. "don't trade hour X / when rvol > Y") to lift the edge.
"""
from __future__ import annotations
import datetime as dt
from collections import deque


class RollingMove:
    """Recent (seconds_into_window, move_pct) within ONE window. Cheap O(n) reads;
    n is bounded by maxlen (~window length at 1 Hz)."""

    def __init__(self, maxlen: int = 950):
        self.buf = deque(maxlen=maxlen)

    def push(self, sec: int, move_pct: float) -> None:
        self.buf.append((sec, move_pct))

    def vel_bps(self, win_sec: int) -> float:
        """Signed change in distance-to-strike over the last win_sec, in bps."""
        if len(self.buf) < 2:
            return 0.0
        now_sec, now_mp = self.buf[-1]
        past_mp = self.buf[0][1]
        for s, mp in reversed(self.buf):
            if s <= now_sec - win_sec:
                past_mp = mp
                break
        return (now_mp - past_mp) * 100.0

    def rvol_bps(self, win_sec: int = 60) -> float:
        """Std of tick-to-tick move_pct changes over the last win_sec, in bps."""
        if len(self.buf) < 3:
            return 0.0
        cut = self.buf[-1][0] - win_sec
        pts = [mp for s, mp in self.buf if s >= cut]
        if len(pts) < 3:
            return 0.0
        diffs = [pts[i] - pts[i - 1] for i in range(1, len(pts))]
        m = sum(diffs) / len(diffs)
        var = sum((x - m) ** 2 for x in diffs) / len(diffs)
        return (var ** 0.5) * 100.0


def utc_hour_dow(ts_ms: int):
    d = dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc)
    return d.hour, d.weekday()


def symbol_of(slug: str) -> str:
    return slug.split("-")[0]
