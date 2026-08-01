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


async def _noop_close(market, end_price, chainlink_start=None, chainlink_end=None):
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


@pytest.mark.asyncio
async def test_chainlink_basis_threaded_through_close_with_correct_units(patched):
    """REGRESSION: discovery must query the ChainlinkPriceCache (keyed in
    MILLISECONDS) using window_start_ts/window_end_ts * 1000 (they are SECONDS).
    If it passes raw seconds, price_asof always returns None and settlement
    silently falls back to Coinbase. This asserts the numeric Chainlink open/close
    reach on_close — i.e. the unit conversion is correct."""
    from mean_reversion_live.collectors.chainlink_collector import ChainlinkPriceCache

    cache = ChainlinkPriceCache()
    # Oracle reads are recorded in MS (the poll loop uses time.time()*1000).
    cache.record("btc", WINDOW_START * 1000, 70010.0)              # at the open boundary
    cache.record("btc", (WINDOW_START + WINDOW_DUR) * 1000, 69990.0)  # at the close boundary

    closes = []

    async def capture_close(market, end_price, chainlink_start=None, chainlink_end=None):
        closes.append((chainlink_start, chainlink_end))

    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=capture_close,
                        chainlink_price_asof=cache.price_asof)

    # Tick 1: window opens -> strike + chainlink_start captured.
    patched["now"] = WINDOW_START
    await d._tick(session=None, symbols=["btc"])

    # Tick 2: past window_end -> close detected, on_close gets both chainlink prices.
    patched["now"] = WINDOW_START + WINDOW_DUR + 1
    await d._tick(session=None, symbols=["btc"])

    assert closes, "on_close must fire when the window ends"
    cl_start, cl_end = closes[-1]
    assert cl_start == 70010.0, "chainlink_start must be the MS-keyed read at window open (not None)"
    assert cl_end == 69990.0, "chainlink_end must be the MS-keyed read at window close (not None)"


@pytest.mark.asyncio
async def test_strike_captured_asof_window_open_not_live(patched):
    """The look-ahead fix: when a spot_price_asof callback is wired, the strike
    is the spot AT window_start_ts (the cache lookup), NOT the live spot at
    poll time (~24s late). The live get_spot must NOT be called when as-of wins."""
    asof_calls = []

    def fake_spot_asof(symbol, t_ms):
        asof_calls.append((symbol, t_ms))
        return 70050.0                      # the TRUE open spot

    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=_noop_close,
                        spot_price_asof=fake_spot_asof)
    # poll lands 18s into the window; live spot has already drifted to 70200
    patched["now"] = WINDOW_START + 18
    patched["spot"] = 70200.0

    await d._tick(session=None, symbols=["btc"])

    m = d.get_market(SLUG)
    assert m.start_price == 70050.0, "strike must be the as-of(open) value, not the late live spot"
    assert asof_calls == [("btc", WINDOW_START * 1000)], "as-of queried in MS at window open"
    assert patched["spot_calls"] == [], "live get_spot must NOT run when as-of succeeds"


@pytest.mark.asyncio
async def test_strike_falls_back_to_live_when_asof_missing(patched):
    """If the cache has no sample at-or-before window open (fresh boot / feed gap),
    as-of returns None and discovery falls back to the live get_spot — preserving
    the legacy behaviour rather than recording a 0 strike."""
    def fake_spot_asof(symbol, t_ms):
        return None                         # no history yet

    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=_noop_close,
                        spot_price_asof=fake_spot_asof)
    patched["now"] = WINDOW_START + 5
    patched["spot"] = 70123.45

    await d._tick(session=None, symbols=["btc"])

    m = d.get_market(SLUG)
    assert m.start_price == 70123.45, "must fall back to live spot when as-of is None"
    assert len(patched["spot_calls"]) == 1, "fallback path calls get_spot exactly once"


@pytest.mark.asyncio
async def test_ended_window_closes_exactly_once(patched):
    """REGRESSION: Gamma keeps returning a window after it ends; discovery must
    close+settle it EXACTLY ONCE, not re-close every poll (which spammed
    market_closed with chainlink_start=None after the strike was popped, and burned
    a Coinbase fetch per stale slug per poll). `fake_list_active_markets` always
    returns the slug, simulating Gamma's persistent stale listing."""
    closes = []

    async def capture_close(market, end_price, chainlink_start=None, chainlink_end=None):
        closes.append(market.slug)

    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=capture_close)

    patched["now"] = WINDOW_START                       # open: capture strike
    await d._tick(session=None, symbols=["btc"])
    assert closes == []
    # poll repeatedly AFTER the window ended — Gamma still lists the slug each time
    for dt in (1, 31, 61, 91, 121):
        patched["now"] = WINDOW_START + WINDOW_DUR + dt
        await d._tick(session=None, symbols=["btc"])

    assert closes == [SLUG], f"ended window must close exactly once, got {len(closes)}: {closes}"
    assert SLUG in d._closed_slugs
