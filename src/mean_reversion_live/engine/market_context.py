"""Cross-symbol macro context: rolling view of every symbol we're watching.

Observable-only for week 1 — no strategy uses it as a filter. The point is to
capture "what was happening in the other crypto markets at the moment the
signal fired" so the week-end review can ask:
  - Does the validated edge weaken when 3+ symbols are dipping at once?
  - Do strategies win more when realized vol is high vs. low?
  - Are near-misses correlated with elevated RV on BTC?

API:
    ctx = MarketContext()
    ctx.update("btc", yes_mid=0.47, no_mid=0.53, ts_ms=..., spot_price=79123.45)
    snap = ctx.snapshot(ts_ms)   # dict, ~18 fields

Cheap: O(symbols × window_len) per snapshot; deques are bounded ~5min.
"""
from __future__ import annotations
import statistics
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple


_DROP_WINDOW_SEC = 60
_RV_WINDOWS_SEC = (60, 300)  # realized-vol horizons we emit
_BUFFER_LEN = max(_DROP_WINDOW_SEC, *_RV_WINDOWS_SEC) + 5  # 1Hz updates


class MarketContext:
    """Per-symbol rolling samples + cheap snapshot."""

    def __init__(self):
        # symbol -> deque[(ts_ms, yes_mid)]
        self._yes_mids: Dict[str, Deque[Tuple[int, float]]] = defaultdict(
            lambda: deque(maxlen=_BUFFER_LEN)
        )
        # symbol -> deque[(ts_ms, spot_price)] for realized vol.
        self._spots: Dict[str, Deque[Tuple[int, float]]] = defaultdict(
            lambda: deque(maxlen=_BUFFER_LEN)
        )

    def update(
        self,
        symbol: str,
        yes_mid: float,
        no_mid: float,
        ts_ms: int,
        spot_price: Optional[float] = None,
    ) -> None:
        """Record the latest YES mid + (optional) spot price for this symbol.

        `no_mid` is unused for now but accepted so callers can pass it without
        branching. `spot_price` (USD) feeds the realized-vol calculation; pass
        None or 0 to skip (e.g. when the spot cache hasn't seeded yet).
        """
        if not symbol or yes_mid <= 0:
            return
        self._yes_mids[symbol].append((ts_ms, yes_mid))
        if spot_price is not None and spot_price > 0:
            self._spots[symbol].append((ts_ms, spot_price))

    @staticmethod
    def _realized_vol_pct(samples: list) -> float:
        """Population stdev of 1-tick simple returns from a [(ts, price), ...]
        list. Returned as a percentage (already multiplied by 100). Two or
        fewer samples → 0.0.
        """
        if len(samples) < 3:
            return 0.0
        rets = []
        prev = samples[0][1]
        for _, p in samples[1:]:
            if prev > 0 and p > 0:
                rets.append((p - prev) / prev)
            prev = p
        if len(rets) < 2:
            return 0.0
        return statistics.pstdev(rets) * 100.0

    def snapshot(self, ts_ms: int) -> dict:
        """Cross-symbol snapshot at `ts_ms`. Returns a flat dict suitable for
        embedding in a JSONL row or a CSV.gz column set.

        Fields:
          n_symbols_dipping_5pct_60s: count of symbols whose YES mid dropped ≥ 5% in last 60s
          <sym>_yes_mid: latest YES mid (or 0.0)
          <sym>_drop_60s_pct: max-mid-in-window vs current, in pct
          <sym>_rv_60s_pct: 1-sec return stdev × 100 over last 60s of spot
          <sym>_rv_300s_pct: same over last 300s
        """
        drop_cutoff = ts_ms - _DROP_WINDOW_SEC * 1000
        rv_cutoffs = {w: ts_ms - w * 1000 for w in _RV_WINDOWS_SEC}
        per_sym = {}
        n_dipping = 0
        # Union of symbols seen on either deque so we emit every column for all.
        all_syms = set(self._yes_mids.keys()) | set(self._spots.keys())
        for sym in all_syms:
            ymid_buf = self._yes_mids.get(sym)
            spot_buf = self._spots.get(sym)

            current = 0.0
            drop_pct = 0.0
            if ymid_buf:
                recent = [m for (t, m) in ymid_buf if t >= drop_cutoff]
                if recent:
                    current = recent[-1]
                    peak = max(recent)
                    drop_pct = ((peak - current) / peak * 100) if peak > 0 else 0.0
                    if drop_pct >= 5.0:
                        n_dipping += 1

            rvs = {}
            if spot_buf:
                for w, cutoff in rv_cutoffs.items():
                    win = [(t, p) for (t, p) in spot_buf if t >= cutoff]
                    rvs[w] = self._realized_vol_pct(win)
            else:
                rvs = {w: 0.0 for w in _RV_WINDOWS_SEC}

            per_sym[sym] = (current, drop_pct, rvs)

        out = {"n_symbols_dipping_5pct_60s": n_dipping}
        for sym, (mid, drop_pct, rvs) in per_sym.items():
            out[f"{sym}_yes_mid"] = round(mid, 6)
            out[f"{sym}_drop_60s_pct"] = round(drop_pct, 4)
            for w in _RV_WINDOWS_SEC:
                out[f"{sym}_rv_{w}s_pct"] = round(rvs.get(w, 0.0), 6)
        return out
