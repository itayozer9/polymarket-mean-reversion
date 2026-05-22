"""Strike-capture correctness — Task 8c forensic fix.

Forensic finding (docs/research/phase0_audit.md, "Task 8c — Window-open (t=0)
forensics"): `markets/discovery.py` froze `start_price` (the resolution strike)
on FIRST sight of a market slug. Discovery probes slugs k=+2 slots ahead
(~1800 s for 15m) so the order book can be warmed up before the window opens.
Capturing the strike at first sight therefore froze it at the Coinbase spot
~30 minutes BEFORE `window_start_ts` — corrupting `start_price`, `move_pct`
and the derived `outcome` for every collected window.

The corrected behaviour: the strike is the spot at/after `window_start_ts`
(when the window actually opens). Early discovery is still fine — it is needed
for the WS subscription — but the strike capture is deferred until the window
is open.

These tests drive the REAL `MarketDiscovery._tick` logic. Only the two I/O
boundaries are stubbed: `gamma.list_active_markets` (the Gamma probe) and
`coinbase.get_spot` (the spot quote), plus a virtual `windows.now_ts` clock.
"""
from __future__ import annotations

import pytest

from mean_reversion_live.clients import coinbase, gamma
from mean_reversion_live.markets import discovery as discovery_mod
from mean_reversion_live.markets import windows
from mean_reversion_live.markets.discovery import MarketDiscovery


WINDOW_START = 1779453900  # btc-updown-15m-1779453900 — a real 15m boundary
WINDOW_DUR = 900
SLUG = f"btc-updown-15m-{WINDOW_START}"


def _market(start_price: float = 0.0) -> gamma.MarketInfo:
    """A fresh MarketInfo as gamma.list_active_markets would return it
    (start_price=0.0 sentinel; discovery fills it in)."""
    return gamma.MarketInfo(
        slug=SLUG,
        symbol="btc",
        timeframe="15m",
        yes_token_id="111",
        no_token_id="222",
        window_start_ts=WINDOW_START,
        window_end_ts=WINDOW_START + WINDOW_DUR,
        start_price=start_price,
    )


@pytest.fixture
def patched(monkeypatch):
    """Stub the I/O boundaries and the clock; return knobs to control them."""
    state = {"now": WINDOW_START, "spot": 70000.0, "spot_calls": []}

    async def fake_list_active_markets(session, symbols, timeframes=("15m", "5m")):
        return [_market()]

    async def fake_get_spot(session, symbol):
        state["spot_calls"].append((symbol, state["now"]))
        return state["spot"]

    monkeypatch.setattr(gamma, "list_active_markets", fake_list_active_markets)
    monkeypatch.setattr(discovery_mod.gamma, "list_active_markets", fake_list_active_markets)
    monkeypatch.setattr(coinbase, "get_spot", fake_get_spot)
    monkeypatch.setattr(discovery_mod.coinbase, "get_spot", fake_get_spot)
    monkeypatch.setattr(windows, "now_ts", lambda: state["now"])
    monkeypatch.setattr(discovery_mod.windows, "now_ts", lambda: state["now"])
    return state


async def _noop_subscribe(asset_ids):
    pass


async def _noop_close(market, end_price):
    pass


@pytest.mark.asyncio
async def test_strike_not_captured_before_window_opens(patched):
    """A future window discovered k slots early must NOT freeze start_price.

    This is the core of the Task 8c bug: discovery sees the slug ~30 min early
    (k=+2). The strike must stay 0.0 (the sentinel) until the window opens.
    """
    patched["now"] = WINDOW_START - 1800  # 30 min before open — exactly the k=+2 case
    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=_noop_close)

    await d._tick(session=None, symbols=["btc"])

    m = d.get_market(SLUG)
    assert m is not None, "market should be discovered early for WS warm-up"
    assert m.start_price == 0.0, "strike must NOT be captured before window opens"
    assert patched["spot_calls"] == [], "no spot quote should be made for a future window"


@pytest.mark.asyncio
async def test_strike_captured_at_window_open(patched):
    """Once now >= window_start_ts, the strike is sampled from Coinbase spot."""
    patched["now"] = WINDOW_START  # window just opened
    patched["spot"] = 70123.45
    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=_noop_close)

    await d._tick(session=None, symbols=["btc"])

    m = d.get_market(SLUG)
    assert m is not None
    assert m.start_price == 70123.45, "strike must equal spot sampled at window open"
    assert len(patched["spot_calls"]) == 1
    symbol, call_ts = patched["spot_calls"][0]
    assert symbol == "btc"
    assert call_ts >= WINDOW_START, "strike spot must be sampled at/after window_start_ts"


@pytest.mark.asyncio
async def test_strike_deferred_then_captured_across_polls(patched):
    """End-to-end: discovered early (strike stays 0.0), then captured on the
    first poll after the window opens, at the price prevailing then."""
    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=_noop_close)

    # Poll 1: 30 min early — spot is 69000, but the strike must be ignored.
    patched["now"] = WINDOW_START - 1800
    patched["spot"] = 69000.0
    await d._tick(session=None, symbols=["btc"])
    assert d.get_market(SLUG).start_price == 0.0

    # Poll 2: still before open — spot drifted to 69500, still ignored.
    patched["now"] = WINDOW_START - 30
    patched["spot"] = 69500.0
    await d._tick(session=None, symbols=["btc"])
    assert d.get_market(SLUG).start_price == 0.0
    assert patched["spot_calls"] == [], "no strike spot calls before window opens"

    # Poll 3: window is open — strike is frozen at the spot prevailing NOW
    # (70200), not the stale ~30-min-early prices (69000 / 69500).
    patched["now"] = WINDOW_START + 12
    patched["spot"] = 70200.0
    await d._tick(session=None, symbols=["btc"])
    assert d.get_market(SLUG).start_price == 70200.0


@pytest.mark.asyncio
async def test_strike_frozen_once_captured(patched):
    """After capture, the strike is never re-sampled even as spot moves —
    a window's strike is fixed for its lifetime."""
    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=_noop_close)

    # Capture at open.
    patched["now"] = WINDOW_START
    patched["spot"] = 70000.0
    await d._tick(session=None, symbols=["btc"])
    assert d.get_market(SLUG).start_price == 70000.0

    # Later in the window, spot has moved — strike must NOT follow it.
    patched["now"] = WINDOW_START + 300
    patched["spot"] = 71500.0
    await d._tick(session=None, symbols=["btc"])
    assert d.get_market(SLUG).start_price == 70000.0, "strike must stay frozen"
    assert len(patched["spot_calls"]) == 1, "spot must be sampled exactly once"
