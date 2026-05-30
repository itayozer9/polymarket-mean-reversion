"""Unit tests for the v2 fill+cost simulator. These pin the cost-truth math
that every edge claim depends on. No I/O, no mocking — pure arithmetic.
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pytest
from research.sim.fills_v2 import (
    FeeSchedule, DEFAULT_FEES, walk_buy, walk_sell, settle_pnl,
    taker_roundtrip_pnl, maker_buy_fill,
)


def test_fee_formula_and_symmetry():
    fs = FeeSchedule()
    # fee = 0.07 * p*(1-p) * shares
    assert fs.taker_fee(100, 0.5) == pytest.approx(0.07 * 0.25 * 100)
    # symmetric around 0.5
    assert fs.taker_fee(100, 0.2) == pytest.approx(fs.taker_fee(100, 0.8))
    # maker rebate = 20% of taker fee
    assert fs.maker_rebate(100, 0.3) == pytest.approx(0.2 * fs.taker_fee(100, 0.3))


def test_walk_buy_single_level():
    f = walk_buy([0.20], [1000.0], stake_usd=10.0)
    assert f.filled and f.shares == pytest.approx(50.0)
    assert f.avg_price == pytest.approx(0.20)
    assert f.unfilled_usd == pytest.approx(0.0)
    assert f.fee_usd == pytest.approx(0.07 * 0.20 * 0.80 * 50.0)
    assert f.slippage_vs_best == pytest.approx(0.0)


def test_walk_buy_walks_multiple_levels():
    # $10: level1 0.20x10sh=$2 (10sh), level2 0.22x100sh -> $8 more = 36.36sh
    f = walk_buy([0.20, 0.22], [10.0, 100.0], stake_usd=10.0)
    assert f.levels_used == 2
    assert f.shares == pytest.approx(10.0 + 8.0 / 0.22)
    assert f.avg_price > 0.20 and f.slippage_vs_best > 0  # paid worse than best


def test_walk_buy_partial_when_book_too_thin():
    f = walk_buy([0.20], [10.0], stake_usd=10.0)  # only $2 of liquidity
    assert f.shares == pytest.approx(10.0)
    assert f.unfilled_usd == pytest.approx(8.0)


def test_walk_sell_and_roundtrip():
    entry = walk_buy([0.20], [1000.0], 10.0)
    # sell the 50 shares into a 0.30 bid
    ex = walk_sell([0.30], [1000.0], entry.shares)
    assert ex.filled and ex.avg_price == pytest.approx(0.30)
    pnl = taker_roundtrip_pnl(entry, ex)
    # gross gain 50*(0.30-0.20)=5 minus both fees
    assert pnl == pytest.approx(50 * 0.30 - ex.fee_usd - 50 * 0.20 - entry.fee_usd)
    assert pnl > 0


def test_settle_pnl_win_and_lose():
    entry = walk_buy([0.20], [1000.0], 10.0)  # 50 shares, $10 notional
    win = settle_pnl(entry, won=True)
    lose = settle_pnl(entry, won=False)
    # win: 50 - 10 - fee ; lose: -10 - fee
    assert win == pytest.approx(50.0 - 10.0 - entry.fee_usd)
    assert lose == pytest.approx(-10.0 - entry.fee_usd)
    # holding to resolution pays only the entry (one-way) fee
    assert win - lose == pytest.approx(50.0)


def test_break_even_favourite_needs_edge_over_price_plus_fee():
    # Buy favourite at 0.90; EV/share>0 requires true prob > 0.90 + fee/share
    entry = walk_buy([0.90], [10_000.0], 10.0)
    fee_per_share = entry.fee_usd / entry.shares
    # at q=0.90 (calibrated) hold-to-resolution loses exactly the fee
    ev_calibrated = 0.90 * (entry.shares) - entry.notional_usd - entry.fee_usd
    assert ev_calibrated == pytest.approx(-entry.fee_usd)
    assert fee_per_share == pytest.approx(0.07 * 0.90 * 0.10)


def test_maker_buy_fills_only_on_sell_through_with_queue():
    # resting buy at 0.20, want $10 -> 50 shares
    trades = [
        {"side": "BUY", "yes_px": 0.20, "usd": 100.0, "sec": 5},   # buy: doesn't fill our bid
        {"side": "SELL", "yes_px": 0.25, "usd": 100.0, "sec": 6},  # sold above our bid: no
        {"side": "SELL", "yes_px": 0.20, "usd": 4.0, "sec": 7},    # absorbed by queue
        {"side": "SELL", "yes_px": 0.19, "usd": 6.0, "sec": 8},    # fills us
    ]
    mf = maker_buy_fill(0.20, stake_usd=10.0, later_trades=trades, queue_ahead_usd=4.0)
    assert mf.filled
    assert mf.fill_sec == 8
    # 6 USD of fills at 0.20 -> 30 shares
    assert mf.shares == pytest.approx(30.0)
    assert mf.rebate_usd == pytest.approx(DEFAULT_FEES.maker_rebate(30.0, 0.20))


def test_maker_no_fill_when_market_never_trades_down():
    trades = [{"side": "SELL", "yes_px": 0.30, "usd": 100.0, "sec": 9}]
    mf = maker_buy_fill(0.20, 10.0, trades)
    assert not mf.filled and mf.shares == 0.0
