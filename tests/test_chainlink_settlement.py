"""Chainlink settlement: the engine must settle windows on the CHAINLINK oracle
(open vs close) Polymarket actually pays — NOT Coinbase spot.

Background: the first real live fill was a Coinbase WIN but a Chainlink LOSS
(memory [[backtest-settles-coinbase-not-chainlink]]). These tests pin the fix:
  1. ChainlinkPriceCache.price_asof — asof semantics + staleness tolerance.
  2. settle_window settles on chainlink_end vs chainlink_start, so a window that
     Coinbase calls a WIN but Chainlink calls a LOSS resolves as a LOSS.
  3. When the oracle feed is unavailable, settle_window FALLS BACK to Coinbase
     (and flags it), never silently skipping settlement.
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mean_reversion_live.adapters.arb_imports import TICK_DTYPE, Portfolio, HumanParams  # noqa: E402
from mean_reversion_live.collectors.chainlink_collector import ChainlinkPriceCache  # noqa: E402
from mean_reversion_live.engine.determinism_state import DetParams, DeterminismState  # noqa: E402
from mean_reversion_live.engine.paper_engine import PaperEngine  # noqa: E402


# ───────────────────────── ChainlinkPriceCache ─────────────────────────

def test_cache_price_asof_returns_latest_at_or_before():
    c = ChainlinkPriceCache()
    c.record("btc", 1_000, 100.0)
    c.record("btc", 2_000, 101.0)
    c.record("btc", 3_000, 102.0)
    # exact hit + between-points both resolve to the latest read at-or-before t
    assert c.price_asof("btc", 2_000) == 101.0
    assert c.price_asof("btc", 2_500) == 101.0
    assert c.price_asof("btc", 3_000) == 102.0
    assert c.latest("btc") == 102.0


def test_cache_price_asof_none_before_first_and_when_stale():
    c = ChainlinkPriceCache()
    c.record("eth", 100_000, 2_000.0)
    # query before any read -> None
    assert c.price_asof("eth", 50_000) is None
    # nearest prior read is older than the tolerance -> None (don't settle on it)
    assert c.price_asof("eth", 100_000 + 200_000, tolerance_ms=120_000) is None
    # within tolerance -> the read
    assert c.price_asof("eth", 100_000 + 60_000, tolerance_ms=120_000) == 2_000.0


def test_cache_is_symbol_scoped_and_case_insensitive():
    c = ChainlinkPriceCache()
    c.record("BTC", 1_000, 100.0)
    assert c.price_asof("btc", 1_000) == 100.0
    assert c.price_asof("eth", 1_000) is None


# ───────────────────────── settle_window basis ─────────────────────────

def _row(sec, *, move_pct, yes_mid, yes_ask, no_ask, ts=1_000_000, depth=1000.0):
    a = np.zeros(1, dtype=TICK_DTYPE)
    a["timestamp_ms"][0] = ts
    a["seconds_into_window"][0] = sec
    a["move_pct"][0] = move_pct
    a["yes_mid"][0] = yes_mid
    a["yes_best_ask"][0] = yes_ask
    a["no_best_ask"][0] = no_ask
    a["yes_best_bid"][0] = max(0.01, yes_ask - 0.02)
    a["spread_yes"][0] = 0.02
    a["yes_ask_depth"][0] = depth
    a["no_ask_depth"][0] = depth
    return a[0]


class _StubStrategy:
    """Minimal StrategyHandle surface that settle_window touches — no disk I/O."""

    def __init__(self, sid, state):
        self.id = sid
        self.states = {state.slug: state}
        self.portfolio = Portfolio(
            human=HumanParams(fixed_bet_usd=10.0, daily_trade_cap=None,
                              concurrent_position_cap=50, post_loss_cooldown_sec=0),
            bankroll=1000.0)
        self.recorded = []
        self.detailed = []

    def set_macro_snapshot(self, fn):  # called by PaperEngine.__init__
        pass

    def record_trade(self, trade):
        self.recorded.append(trade)

    def record_trade_detailed(self, trade, ctx):
        self.detailed.append((trade, ctx))

    def persist_portfolio(self):
        pass


def _engine_with_holding_up_position():
    """A determinism strategy holding an UP position (book & spot both favour Up)."""
    slug = "btc-updown-15m-1000"
    st = DeterminismState(slug, DetParams(), window_duration_sec=900)
    strat = _StubStrategy("det_test", st)
    # Drive the entry through the strategy's OWN portfolio so all open/close
    # bookkeeping is internally consistent for the subsequent settle.
    st.on_tick(_row(850, move_pct=0.10, yes_mid=0.70, yes_ask=0.72, no_ask=0.30),
               strat.portfolio, np.random.default_rng(0))
    assert st.state == "HOLDING" and st.pos["side"] == "UP"
    engine = PaperEngine(strategies=[strat], queue=asyncio.Queue(), data_dir=REPO / "data")
    return engine, strat, slug


class _Mkt:
    def __init__(self, slug, start_price):
        self.slug = slug
        self.start_price = start_price
        self.symbol = "btc"


def test_settle_uses_chainlink_not_coinbase_on_disagreement():
    """The headline regression: Coinbase says the UP favourite WON (cb_end>start),
    but Chainlink says it LOST (cl_end<cl_start). The trade must settle as a LOSS,
    because Polymarket pays on Chainlink."""
    engine, strat, slug = _engine_with_holding_up_position()
    mkt = _Mkt(slug, start_price=100.0)
    # Coinbase: close 100.5 >= strike 100.0 -> Up WINS (the misleading basis)
    # Chainlink: close 99.5 <  open  100.0 -> Down  -> our UP bet LOSES
    engine.settle_window(mkt, end_price=100.5, chainlink_start=100.0, chainlink_end=99.5)

    assert len(strat.recorded) == 1
    tr = strat.recorded[0]
    assert tr.exit_price == 0.0 and tr.pnl < 0, "must settle the UP bet as a LOSS on Chainlink"
    _, ctx = strat.detailed[0]
    assert ctx["settle_basis"] == "chainlink"
    assert ctx["outcome_up_chainlink"] == 0
    assert ctx["outcome_up_coinbase"] == 1
    assert ctx["oracle_disagree"] == 1


def test_settle_chainlink_win_when_both_agree():
    engine, strat, slug = _engine_with_holding_up_position()
    mkt = _Mkt(slug, start_price=100.0)
    engine.settle_window(mkt, end_price=100.5, chainlink_start=100.0, chainlink_end=100.8)
    tr = strat.recorded[0]
    assert tr.exit_price == 1.0 and tr.pnl > 0
    _, ctx = strat.detailed[0]
    assert ctx["settle_basis"] == "chainlink" and ctx["oracle_disagree"] == 0


def test_settle_falls_back_to_coinbase_when_chainlink_missing():
    """Oracle feed gap -> settle on Coinbase rather than skip, and flag the basis."""
    engine, strat, slug = _engine_with_holding_up_position()
    mkt = _Mkt(slug, start_price=100.0)
    engine.settle_window(mkt, end_price=100.5, chainlink_start=None, chainlink_end=None)
    tr = strat.recorded[0]
    assert tr.exit_price == 1.0 and tr.pnl > 0, "Coinbase fallback: 100.5>=100.0 -> UP wins"
    _, ctx = strat.detailed[0]
    assert ctx["settle_basis"] == "coinbase_fallback"


def test_settle_skipped_when_no_basis_available():
    """No chainlink AND no usable coinbase -> no trade recorded, position untouched."""
    engine, strat, slug = _engine_with_holding_up_position()
    mkt = _Mkt(slug, start_price=0.0)        # no valid strike
    engine.settle_window(mkt, end_price=None, chainlink_start=None, chainlink_end=None)
    assert strat.recorded == []
    assert strat.states[slug].state == "HOLDING", "position must remain open, not lost"
