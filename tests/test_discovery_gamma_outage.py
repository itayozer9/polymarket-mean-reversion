"""A Gamma/DNS outage must NOT mass-settle live windows (2026-08-09 incident).

Incident: 2026-08-09 22:02-22:13 UTC the host lost DNS, so every `_market_by_slug`
probe inside `gamma.list_active_markets` raised and the call returned `[]`.
`MarketDiscovery._tick` step 3 read "slug absent from now_active" as "the window
closed", so ALL 28 in-flight markets were:

  1. force-settled through `on_close` with no price basis (42x `settle_skipped_no_basis`),
  2. permanently added to `_closed_slugs`.

(2) is the damaging half: `_closed_slugs` is a hard blacklist (step 1 of the next
tick), and `gamma.candidate_window_starts` only ever proposes k in {-1,0,1,2}. A
blacklisted window can therefore never be re-adopted, so the collector stayed dark
for the CURRENT (22:15) and NEXT (22:30) 15m windows even after DNS recovered at
22:13 - 3 lost 15m windows, `rows_written=0` for 21-43 min, while the heartbeat
stayed green.

A window ends when its clock says so. Gamma's reachability is not evidence about
it, so absence from a poll must never close anything.

Same stubbing convention as test_discovery_strike.py: only the two I/O boundaries
(`gamma.list_active_markets`, `coinbase.get_spot`) and the clock are faked.
"""
from __future__ import annotations

import pytest

from mean_reversion_live.clients import coinbase, gamma
from mean_reversion_live.markets import discovery as discovery_mod
from mean_reversion_live.markets import windows
from mean_reversion_live.markets.discovery import MarketDiscovery


WINDOW_START = 1786313700  # the real 22:15-22:30 window that the incident lost
WINDOW_DUR = 900
SLUG = f"btc-updown-15m-{WINDOW_START}"


def _market() -> gamma.MarketInfo:
    return gamma.MarketInfo(
        slug=SLUG,
        symbol="btc",
        timeframe="15m",
        yes_token_id="111",
        no_token_id="222",
        window_start_ts=WINDOW_START,
        window_end_ts=WINDOW_START + WINDOW_DUR,
        start_price=0.0,
    )


@pytest.fixture
def patched(monkeypatch):
    """Stub the I/O boundaries and the clock. `outage` toggles Gamma returning []."""
    state = {"now": WINDOW_START, "spot": 65000.0, "outage": False}

    async def fake_list_active_markets(session, symbols, timeframes=("15m", "5m")):
        # This is exactly what the real call does when every slug probe raises:
        # `_market_by_slug` exceptions are swallowed per-slug, so the list is empty.
        return [] if state["outage"] else [_market()]

    async def fake_get_spot(session, symbol):
        return state["spot"]

    monkeypatch.setattr(discovery_mod.gamma, "list_active_markets", fake_list_active_markets)
    monkeypatch.setattr(discovery_mod.coinbase, "get_spot", fake_get_spot)
    monkeypatch.setattr(windows, "now_ts", lambda: state["now"])
    monkeypatch.setattr(discovery_mod.windows, "now_ts", lambda: state["now"])
    return state


async def _noop_subscribe(asset_ids):
    pass


@pytest.mark.asyncio
async def test_gamma_outage_does_not_close_an_open_window(patched):
    """Gamma returning [] mid-window must not settle the window nor blacklist it."""
    closes = []

    async def capture_close(market, end_price, chainlink_start=None, chainlink_end=None):
        closes.append(market.slug)

    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=capture_close)

    patched["now"] = WINDOW_START + 10          # window open, strike captured
    await d._tick(session=None, symbols=["btc"])
    assert d.get_market(SLUG) is not None

    # DNS dies 4 min into the window; Gamma probes all raise -> [] for several polls.
    patched["outage"] = True
    for offset in (240, 270, 300, 330):
        patched["now"] = WINDOW_START + offset
        await d._tick(session=None, symbols=["btc"])

    assert closes == [], f"an open window must not settle during a Gamma outage, got {closes}"
    assert SLUG not in d._closed_slugs, (
        "the window must not be blacklisted - _closed_slugs is permanent and would "
        "make the window unrecoverable once Gamma returns"
    )
    assert d.get_market(SLUG) is not None, "the open window must stay tracked through the outage"


@pytest.mark.asyncio
async def test_window_is_re_adopted_after_the_outage_ends(patched):
    """The whole point: once Gamma recovers, the still-open window keeps emitting.

    This is the half the incident actually lost - recovery, not the outage itself.
    """
    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=_noop_close)

    patched["now"] = WINDOW_START + 10
    await d._tick(session=None, symbols=["btc"])
    captured_strike = d.get_market(SLUG).start_price

    patched["outage"] = True
    patched["now"] = WINDOW_START + 300
    await d._tick(session=None, symbols=["btc"])

    patched["outage"] = False                   # DNS back, still inside the window
    patched["now"] = WINDOW_START + 600
    await d._tick(session=None, symbols=["btc"])

    m = d.get_market(SLUG)
    assert m is not None, "the window must be tracked again after Gamma recovers"
    assert m.start_price == captured_strike, "the strike captured at open must survive the outage"


@pytest.mark.asyncio
async def test_window_still_closes_on_its_own_clock_during_an_outage(patched):
    """The fix must not stop real closes: window_end_ts is the sole close trigger,
    so a window whose clock has run out settles even while Gamma is unreachable."""
    closes = []

    async def capture_close(market, end_price, chainlink_start=None, chainlink_end=None):
        closes.append(market.slug)

    d = MarketDiscovery(on_subscribe=_noop_subscribe, on_close=capture_close)

    patched["now"] = WINDOW_START + 10
    await d._tick(session=None, symbols=["btc"])

    patched["outage"] = True
    patched["now"] = WINDOW_START + WINDOW_DUR + 1   # clock says the window is over
    await d._tick(session=None, symbols=["btc"])

    assert closes == [SLUG], "an ENDED window must settle even if Gamma is down"
    assert SLUG in d._closed_slugs, "a genuinely-closed window is still blacklisted"


async def _noop_close(market, end_price, chainlink_start=None, chainlink_end=None):
    pass
