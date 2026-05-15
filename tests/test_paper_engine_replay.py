"""LOAD-BEARING: bit-for-bit parity between PerMarketState and simulate_market.

We can't ship a live engine if its decisions drift from the validated backtest.
This test loads a historical CSV, runs the SAME SimConfig through both:
  (a) `simulate.simulate_market` — batch, the validated reference
  (b) `PerMarketState.on_tick` — streaming, the new live code

And asserts the trade lists match.
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
    """Loaders use a relative path 'data_v2' that resolves via the symlink in polymarket-arb.
    Module-scoped so it applies BEFORE the module-scoped historical_15m_markets fixture."""
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

# We compare against the canonical simulate_market via the same path.
from scripts.mean_reversion import simulate as sim_mod  # noqa: E402


def _validated_cfg_21c8c00165b3() -> SimConfig:
    """The top in-sample config from the validation run."""
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
        exit=ExitParams(
            profit_target_pct=50.0,
            stop_loss_pct=80,
            max_hold_sec=180,
            trailing_stop_pct=None,
        ),
        filter=FilterParams(
            min_book_depth_usd=0.0,
            max_spread=0.10,
            book_imbalance_min=None,
            vol_regime="ALL",
            time_of_day="ALL",
            multi_tier_entry=1,
            correlated_signal_filter=False,
        ),
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


def _validated_cfg_333fde9cecb8() -> SimConfig:
    return SimConfig(
        entry=EntryParams(
            side="BOTH",
            entry_price_min=0.05,
            entry_price_max=0.15,
            drop_magnitude_pct=15.0,
            drop_window_sec=60,
            min_time_left_sec=180,
            proximity_max_pct=100.0,
            min_seconds_into_window=15,
        ),
        exit=ExitParams(
            profit_target_pct=75.0,
            stop_loss_pct=50,
            max_hold_sec=360,
            trailing_stop_pct=25,
        ),
        filter=FilterParams(
            min_book_depth_usd=50.0,
            max_spread=0.10,
            vol_regime="ALL",
            time_of_day="ASIA",
            multi_tier_entry=1,
            correlated_signal_filter=False,
        ),
        human=HumanParams(
            reaction_delay_min_sec=0.0,
            reaction_delay_max_sec=0.5,
            signal_skip_prob=0.10,
            daily_trade_cap=None,
            post_loss_cooldown_sec=180,
            concurrent_position_cap=1,
            fixed_bet_usd=10.0,
        ),
        fill=FillParams(fee_rate=0.072, reject_prob=0.03, use_next_tick_for_fill=True),
    )


def _run_batch(cfg: SimConfig, markets, outcomes_map, seed: int):
    """Reference: the validated simulate_market loop."""
    portfolio = Portfolio(human=cfg.human)
    rng = np.random.default_rng(seed)
    for slug, arr in markets:
        sim_mod.simulate_market(
            slug=slug, arr=arr, cfg=cfg,
            window_duration_sec=window_duration_sec("15m"),
            portfolio=portfolio, rng=rng,
            outcome=outcomes_map.get(slug),
        )
    return portfolio


def _run_streaming(cfg: SimConfig, markets, outcomes_map, seed: int):
    """Same data, fed tick-by-tick through PerMarketState."""
    portfolio = Portfolio(human=cfg.human)
    rng = np.random.default_rng(seed)
    duration = window_duration_sec("15m")
    for slug, arr in markets:
        if len(arr) < 2:
            continue
        pms = PerMarketState(slug=slug, cfg=cfg, window_duration_sec=duration)
        outcome = outcomes_map.get(slug)
        for i in range(len(arr)):
            pms.on_tick(arr[i], portfolio, rng, outcome=outcome)
    return portfolio


def _compare_portfolios(label: str, batch: Portfolio, stream: Portfolio):
    assert batch.n_trades == stream.n_trades, (
        f"[{label}] trade count: batch={batch.n_trades}, stream={stream.n_trades}"
    )
    for i, (b, s) in enumerate(zip(batch.trades, stream.trades)):
        assert b.slug == s.slug, f"[{label}] trade {i} slug mismatch: {b.slug} vs {s.slug}"
        assert b.side == s.side, f"[{label}] trade {i} side mismatch"
        assert b.entry_ts_ms == s.entry_ts_ms, f"[{label}] trade {i} entry_ts: {b.entry_ts_ms} vs {s.entry_ts_ms}"
        assert b.exit_ts_ms == s.exit_ts_ms, f"[{label}] trade {i} exit_ts"
        assert abs(b.entry_price - s.entry_price) < 1e-6, f"[{label}] trade {i} entry_price"
        assert abs(b.exit_price - s.exit_price) < 1e-6, f"[{label}] trade {i} exit_price"
        assert abs(b.pnl - s.pnl) < 1e-4, f"[{label}] trade {i} pnl: {b.pnl} vs {s.pnl}"
        assert b.exit_reason == s.exit_reason, f"[{label}] trade {i} reason"


@pytest.fixture(scope="module")
def historical_15m_markets():
    """Pre-load Mar 15-17 BTC 15m markets once."""
    markets = list(iter_markets("15m", "btc", "2026-03-15", "2026-03-17"))
    outcomes = load_outcomes()
    return markets, outcomes


def test_replay_parity_cfg_21c8c00165b3(historical_15m_markets):
    """Stream PerMarketState must match batch simulate_market trade-for-trade."""
    markets, outcomes = historical_15m_markets
    cfg = _validated_cfg_21c8c00165b3()
    # Use the same seed scheme the sweep uses (hash-derived) so RNG calls align.
    seed = int(cfg.hash_id(), 16) % (2 ** 32)
    batch = _run_batch(cfg, markets, outcomes, seed=seed)
    stream = _run_streaming(cfg, markets, outcomes, seed=seed)
    _compare_portfolios("cfg_21c8c00165b3", batch, stream)
    # Spot check: this config should generate at least a few trades on this data
    assert batch.n_trades >= 3, f"batch produced only {batch.n_trades} trades — data may have drifted"


def test_replay_parity_cfg_333fde9cecb8(historical_15m_markets):
    markets, outcomes = historical_15m_markets
    cfg = _validated_cfg_333fde9cecb8()
    seed = int(cfg.hash_id(), 16) % (2 ** 32)
    batch = _run_batch(cfg, markets, outcomes, seed=seed)
    stream = _run_streaming(cfg, markets, outcomes, seed=seed)
    _compare_portfolios("cfg_333fde9cecb8", batch, stream)
    assert batch.n_trades >= 3
