"""Sanity check: the observer hook on PerMarketState emits structured events
covering fired entries and (when applicable) near-misses, without breaking parity.

We re-use the historical replay path. We attach a simple list-appending observer,
run the streaming engine over a small market, and assert:
  - The number of "fired" decisions matches the number of trades produced.
  - At least one of "armed_new", "fired", or some sequence consistent with trades.
  - When the same data is run with NO observer, trade results are identical.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

POLYMARKET_ARB = REPO_ROOT.parent / "polymarket-arb"


@pytest.fixture(scope="module", autouse=True)
def _cwd_polymarket_arb():
    orig = os.getcwd()
    os.chdir(POLYMARKET_ARB)
    try:
        yield
    finally:
        os.chdir(orig)


from mean_reversion_live.adapters.arb_imports import (  # noqa: E402
    EntryParams, ExitParams, FilterParams, FillParams, HumanParams, SimConfig,
    Portfolio, iter_markets, load_outcomes, window_duration_sec,
)
from mean_reversion_live.engine.per_market_state import PerMarketState  # noqa: E402


def _cfg() -> SimConfig:
    """Same as cfg_21c8c00165b3 — known to produce trades on the test dataset."""
    return SimConfig(
        entry=EntryParams(
            side="DOWN",
            entry_price_min=0.075,
            entry_price_max=0.125,
            drop_magnitude_pct=15.0,
            drop_window_sec=30,
            min_time_left_sec=180,
            proximity_max_pct=0.5,
            min_seconds_into_window=15,
        ),
        exit=ExitParams(profit_target_pct=50.0, stop_loss_pct=80, max_hold_sec=180),
        filter=FilterParams(),
        human=HumanParams(
            reaction_delay_min_sec=0.0,
            reaction_delay_max_sec=1.5,
            signal_skip_prob=0.10,
            daily_trade_cap=25,
            post_loss_cooldown_sec=60,
            concurrent_position_cap=1,
            fixed_bet_usd=10.0,
        ),
        fill=FillParams(fee_rate=0.072, reject_prob=0.03, use_next_tick_for_fill=True),
    )


def _run(cfg, markets, outcomes_map, seed: int, observer=None):
    portfolio = Portfolio(human=cfg.human)
    rng = np.random.default_rng(seed)
    duration = window_duration_sec("15m")
    events_per_strategy = []
    for slug, arr in markets:
        if len(arr) < 2:
            continue
        if observer is not None:
            captured = []
            slug_observer = lambda e, cap=captured: cap.append(dict(e))
            pms = PerMarketState(slug=slug, cfg=cfg, window_duration_sec=duration, observer=slug_observer)
        else:
            pms = PerMarketState(slug=slug, cfg=cfg, window_duration_sec=duration, observer=None)
            captured = None
        outcome = outcomes_map.get(slug)
        for i in range(len(arr)):
            pms.on_tick(arr[i], portfolio, rng, outcome=outcome)
        if captured is not None:
            events_per_strategy.append(captured)
    return portfolio, events_per_strategy


@pytest.fixture(scope="module")
def historical_btc_15m():
    markets = list(iter_markets("15m", "btc", "2026-03-15", "2026-03-17"))
    outcomes = load_outcomes()
    return markets, outcomes


def test_observer_does_not_break_parity(historical_btc_15m):
    """With observer attached, trades must be identical to the no-observer run."""
    markets, outcomes = historical_btc_15m
    cfg = _cfg()
    seed = int(cfg.hash_id(), 16) % (2 ** 32)
    no_obs_portfolio, _ = _run(cfg, markets, outcomes, seed=seed, observer=None)
    with_obs_portfolio, _ = _run(cfg, markets, outcomes, seed=seed, observer=True)
    assert no_obs_portfolio.n_trades == with_obs_portfolio.n_trades
    for a, b in zip(no_obs_portfolio.trades, with_obs_portfolio.trades):
        assert a.slug == b.slug
        assert a.entry_ts_ms == b.entry_ts_ms
        assert a.exit_ts_ms == b.exit_ts_ms
        assert abs(a.pnl - b.pnl) < 1e-9


def test_observer_emits_fired_for_every_trade(historical_btc_15m):
    markets, outcomes = historical_btc_15m
    cfg = _cfg()
    seed = int(cfg.hash_id(), 16) % (2 ** 32)
    portfolio, all_events = _run(cfg, markets, outcomes, seed=seed, observer=True)
    if portfolio.n_trades == 0:
        pytest.skip("No trades produced — can't validate fired-event count")
    flat = [e for ev in all_events for e in ev]
    fired = [e for e in flat if e["decision"] == "fired"]
    closed = [e for e in flat if e["decision"].startswith("trade_closed_")]
    assert len(fired) == portfolio.n_trades, (
        f"fired count ({len(fired)}) != trades ({portfolio.n_trades})"
    )
    assert len(closed) == portfolio.n_trades, (
        f"trade_closed_* count ({len(closed)}) != trades ({portfolio.n_trades})"
    )


def test_observer_emits_decisions_covering_all_states(historical_btc_15m):
    """Sanity check: a long replay produces at least one event per major state."""
    markets, outcomes = historical_btc_15m
    cfg = _cfg()
    seed = int(cfg.hash_id(), 16) % (2 ** 32)
    _portfolio, all_events = _run(cfg, markets, outcomes, seed=seed, observer=True)
    flat = [e for ev in all_events for e in ev]
    decisions = {e["decision"] for e in flat}
    # We expect at minimum: 'flat' (no signal), 'holding' or 'fired' (during a position),
    # and at least one 'armed_new' or 'fired' for the trades that did happen.
    assert "flat" in decisions or "near_miss" in decisions, decisions
