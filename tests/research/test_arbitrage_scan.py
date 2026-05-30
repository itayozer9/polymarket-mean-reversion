"""Tests for the within-market arbitrage scan (lead A)."""
import numpy as np
import pandas as pd

from research.analysis.arbitrage_scan import (
    taker_fee, _genuine_mask, _mirror_mask, scan_side,
)


def test_taker_fee_formula():
    # fee = 0.07 * p * (1-p); zero at the boundaries, max at p=0.5.
    assert taker_fee(0.0) == 0.0
    assert taker_fee(1.0) == 0.0
    assert abs(taker_fee(0.5) - 0.07 * 0.25) < 1e-12


def test_mirror_book_yields_no_arb():
    # An exact-mirror book: no_bid = 1-yes_ask, no_ask = 1-yes_bid.
    # Then yes_ask + no_ask = 1 + spread > 1 -> buy arb impossible.
    df = pd.DataFrame({
        "market_slug": ["m"], "date": ["2026-05-15"],
        "yes_best_bid": [0.40], "yes_best_ask": [0.42],
        "no_best_bid": [0.58], "no_best_ask": [0.60],
        "yes_bid_depth": [100.0], "yes_ask_depth": [100.0],
        "no_bid_depth": [100.0], "no_ask_depth": [100.0],
    })
    assert bool(np.asarray(_mirror_mask(df))[0]) is True
    assert len(scan_side(df, "buy")) == 0
    assert len(scan_side(df, "sell")) == 0


def test_genuine_buy_arb_is_detected_and_priced():
    # Independently quoted books with yes_ask + no_ask = 0.95 < 1: a real arb.
    df = pd.DataFrame({
        "market_slug": ["m"], "date": ["2026-05-15"],
        "yes_best_bid": [0.44], "yes_best_ask": [0.46],
        "no_best_bid": [0.47], "no_best_ask": [0.49],
        "yes_bid_depth": [80.0], "yes_ask_depth": [30.0],
        "no_bid_depth": [60.0], "no_ask_depth": [50.0],
    })
    assert bool(_genuine_mask(df).iloc[0]) is True
    s = scan_side(df, "buy")
    assert len(s) == 1
    row = s.iloc[0]
    assert abs(row.gross - (1 - 0.46 - 0.49)) < 1e-12
    # net_maker == gross (maker pays nothing); net_taker is lower.
    assert abs(row.net_maker - row.gross) < 1e-12
    assert row.net_taker < row.gross
    # executable size limited by the thinner ask leg (30 vs 50).
    assert row.exec_size == 30.0


def test_decided_market_book_is_rejected_as_artifact():
    # yes_ask collapsed to ~0 (resolved market): gross "edge" ~0.99 but bogus.
    df = pd.DataFrame({
        "market_slug": ["m"], "date": ["2026-05-15"],
        "yes_best_bid": [0.99], "yes_best_ask": [0.001],
        "no_best_bid": [0.0], "no_best_ask": [0.01],
        "yes_bid_depth": [100.0], "yes_ask_depth": [100.0],
        "no_bid_depth": [100.0], "no_ask_depth": [100.0],
    })
    # raw buy condition is satisfied (0.001 + 0.01 < 1) ...
    assert (df.yes_best_ask + df.no_best_ask < 1.0).iloc[0]
    # ... but the genuine filter rejects it.
    assert bool(_genuine_mask(df).iloc[0]) is False
    assert len(scan_side(df, "buy")) == 0
