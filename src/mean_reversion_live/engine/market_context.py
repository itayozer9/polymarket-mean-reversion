"""Cross-symbol macro context: rolling 60s view of every symbol we're watching.

This is observable-only for week 1 — no strategy uses it as a filter. The point
is to capture "what was happening in the other crypto markets at the moment
the signal fired" so the week-end review can ask:
  - Does the validated edge weaken when 3+ symbols are dipping at once?
  - Are near-misses correlated with elevated realized vol on BTC?

API:
    ctx = MarketContext()
    ctx.update("btc", yes_mid=0.47, no_mid=0.53, ts_ms=1731234567000)
    snap = ctx.snapshot(ts_ms)   # dict, ~10 fields

Cheap: O(symbols) per update; deques are bounded.
"""
from __future__ import annotations
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


_WINDOW_SEC = 60
_SAMPLES_PER_SEC_MAX = 1  # we get one update per symbol per second from the aggregator
_BUFFER_LEN = _WINDOW_SEC * _SAMPLES_PER_SEC_MAX + 5


class MarketContext:
    """Per-symbol rolling samples + cheap snapshot."""

    def __init__(self):
        # symbol -> deque[(ts_ms, yes_mid)]
        self._yes_mids: Dict[str, Deque[Tuple[int, float]]] = defaultdict(
            lambda: deque(maxlen=_BUFFER_LEN)
        )

    def update(self, symbol: str, yes_mid: float, no_mid: float, ts_ms: int) -> None:
        """Record the latest YES mid for this symbol. `no_mid` is unused for now
        but accepted so callers can pass it without branching."""
        if not symbol or yes_mid <= 0:
            return
        self._yes_mids[symbol].append((ts_ms, yes_mid))

    def snapshot(self, ts_ms: int) -> dict:
        """Cross-symbol snapshot at `ts_ms`. Returns a flat dict suitable for
        embedding in a JSONL row or a CSV.gz column set.

        Fields:
          n_symbols_dipping_5pct_60s: count of symbols whose YES mid dropped ≥ 5% in last 60s
          <sym>_yes_mid: latest YES mid (or 0.0)
          <sym>_drop_60s_pct: max-mid-in-window vs current, in pct
        """
        cutoff = ts_ms - _WINDOW_SEC * 1000
        per_sym = {}
        n_dipping = 0
        for sym, buf in self._yes_mids.items():
            if not buf:
                per_sym[sym] = (0.0, 0.0)
                continue
            recent = [m for (t, m) in buf if t >= cutoff]
            if not recent:
                per_sym[sym] = (0.0, 0.0)
                continue
            current = recent[-1]
            peak = max(recent)
            drop_pct = ((peak - current) / peak * 100) if peak > 0 else 0.0
            per_sym[sym] = (current, drop_pct)
            if drop_pct >= 5.0:
                n_dipping += 1

        out = {"n_symbols_dipping_5pct_60s": n_dipping}
        for sym, (mid, drop_pct) in per_sym.items():
            out[f"{sym}_yes_mid"] = round(mid, 6)
            out[f"{sym}_drop_60s_pct"] = round(drop_pct, 4)
        return out
