"""Unit tests for the E4 'disagree' determinism mode (fade the book, buy the cheap
spot-implied side). Verified edge — docs/research/IMPROVEMENT_FINDINGS_2026-06-04.md.
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mean_reversion_live.adapters.arb_imports import TICK_DTYPE, Portfolio, HumanParams  # noqa: E402
from mean_reversion_live.engine.determinism_state import DetParams, DeterminismState  # noqa: E402


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


def _pf():
    return Portfolio(human=HumanParams(fixed_bet_usd=10.0, daily_trade_cap=None,
                                       concurrent_position_cap=50,
                                       post_loss_cooldown_sec=0), bankroll=1000.0)


def _disagree_state():
    return DeterminismState("btc-updown-15m-1",
                            DetParams(mode="disagree", min_ask=0.05, max_ask=0.90),
                            window_duration_sec=900)


def test_disagree_fires_on_disagreement_and_buys_cheap_spot_side():
    """Spot favours UP (move>0, dist=10bps) but the BOOK has YES as underdog
    (yes_mid=0.30). disagree mode must FADE the book: buy YES (UP) at the cheap
    yes_ask=0.32, hold to resolution."""
    s, pf, rng = _disagree_state(), _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.30, yes_ask=0.32, no_ask=0.70), pf, rng)
    assert s.state == "HOLDING" and pf.open_positions == 1
    assert s.pos["side"] == "UP"                      # bought the spot-implied side
    assert abs(s.pos["entry"] - 0.32) < 1e-4          # at the cheap book-underdog ask (float32)
    # settles to a WIN when the true outcome matches spot (Up)
    tr = s.settle(outcome_up=True, ts_ms=1_000_900, portfolio=pf)
    assert tr.side == "UP" and tr.pnl > 0


def test_disagree_skips_when_book_agrees_with_spot():
    """Consistent window (book YES favourite AND spot up) -> NOT a disagreement,
    so disagree mode must NOT fire."""
    s, pf, rng = _disagree_state(), _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
    assert s.state == "FLAT" and pf.open_positions == 0


def test_consistent_mode_skips_a_disagreement_window():
    """Default mode must be unaffected: a disagreement window (book underdog =
    spot favourite) does NOT fire in the original det_lwd path."""
    s = DeterminismState("btc-updown-15m-1", DetParams(), window_duration_sec=900)
    pf, rng = _pf(), np.random.default_rng(0)
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.30, yes_ask=0.32, no_ask=0.70), pf, rng)
    assert s.state == "FLAT" and pf.open_positions == 0
