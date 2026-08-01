"""SpotPriceCache as-of history — the look-ahead-killer for strike capture.

2026-06-13: the window strike (start_price, the SIGNAL basis) was captured by a
LIVE `coinbase.get_spot()` call at discovery-poll time — median ~24s AFTER the
window opened (the 30s poll cadence). Research back-fills that single value onto
the whole window, so any rule firing in the first ~30s inherits a baseline that
already "knows" up to ~24s of post-open price movement (the xb look-ahead, 74%
acausal; test_ledger "XI4 AMENDMENT"). The fix mirrors the Chainlink settlement
basis, which is ALREADY captured correctly via an as-of lookup at window_start_ts.

This requires the spot cache to retain a short price history and answer
"what was the spot at-or-before t_ms?". These tests pin that capability.
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.collectors.spot_collector import SpotPriceCache  # noqa: E402


def test_price_asof_returns_latest_at_or_before():
    c = SpotPriceCache(["btc"])
    c.set("btc", 100.0, ts_ms=1_000)
    c.set("btc", 101.0, ts_ms=2_000)
    c.set("btc", 102.0, ts_ms=3_000)
    assert c.price_asof("btc", 2_500) == 101.0   # latest at-or-before 2500
    assert c.price_asof("btc", 2_000) == 101.0   # exact boundary is inclusive
    assert c.price_asof("btc", 3_000) == 102.0


def test_price_asof_none_before_all_history():
    c = SpotPriceCache(["btc"])
    c.set("btc", 100.0, ts_ms=5_000)
    # asking for a time before any recorded sample -> honest None (caller falls back)
    assert c.price_asof("btc", 4_999) is None


def test_price_asof_unknown_symbol_is_none():
    c = SpotPriceCache(["btc"])
    c.set("btc", 100.0, ts_ms=1_000)
    assert c.price_asof("eth", 2_000) is None


def test_price_asof_does_not_leak_future_value():
    """The core anti-look-ahead property: a sample recorded AFTER t_ms must
    NOT be returned for t_ms — even though it is the latest value overall."""
    c = SpotPriceCache(["btc"])
    c.set("btc", 100.0, ts_ms=1_000)   # at window open
    c.set("btc", 130.0, ts_ms=25_000)  # 24s later — the contaminating value
    assert c.price_asof("btc", 1_000) == 100.0   # strike = the open value, not 130


def test_price_asof_robust_to_out_of_order_writes():
    """Two threads (spot_loop + WS collector) feed the same cache; near-duplicate
    or slightly out-of-order timestamps must still resolve correctly."""
    c = SpotPriceCache(["btc"])
    c.set("btc", 101.0, ts_ms=2_000)
    c.set("btc", 100.0, ts_ms=1_000)   # arrives after, but older timestamp
    c.set("btc", 102.0, ts_ms=3_000)
    assert c.price_asof("btc", 2_500) == 101.0
    assert c.price_asof("btc", 1_500) == 100.0


def test_history_is_bounded():
    """History must not grow unbounded over a 7-day run."""
    c = SpotPriceCache(["btc"], history_max=100)
    for i in range(1, 501):
        c.set("btc", float(i), ts_ms=i * 1000)
    # the oldest samples have been evicted; a query in the dropped region returns
    # the oldest RETAINED sample (or None if before it), never a crash
    assert c.price_asof("btc", 500_000) == 500.0           # newest retained
    assert len(c._hist["btc"]) <= 100


def test_set_still_updates_latest_value_api():
    """REGRESSION: the existing get()/age_ms() latest-value API is unchanged."""
    c = SpotPriceCache(["btc"])
    c.set("btc", 100.0, ts_ms=1_000)
    c.set("btc", 105.0, ts_ms=2_000)
    assert c.get("btc") == 105.0      # latest, as before
    c2 = SpotPriceCache(["eth"])
    assert c2.get("eth") is None      # unset -> None (0.0 sentinel), as before
