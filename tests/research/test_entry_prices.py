"""Tests for research.wallets.entry_prices (Task 2.2 — buy-price analysis).

Per CLAUDE.md: no mocking. The band/percentile maths is tested on hand-built
activity lists; the end-to-end ``entry_prices_all`` is exercised against the
real wallet cache on disk and skipped if it is absent.
"""
from __future__ import annotations

import pytest

from research.wallets.analyze import MANIFEST_PATH
from research.wallets.entry_prices import (
    ODDS_BANDS,
    entry_prices_all,
    wallet_entry_prices,
)

_CACHE_PRESENT = MANIFEST_PATH.exists()
needs_cache = pytest.mark.skipif(
    not _CACHE_PRESENT, reason="data/wallets/ cache not present")


def _buy(price, usdc, slug="bitcoin-above-78k-on-may-24-2026",
         title="Bitcoin", ts=1779473786, outcome="Yes"):
    return {
        "type": "TRADE", "side": "BUY", "price": price, "usdc_size": usdc,
        "size": usdc / price if price else 0.0, "slug": slug, "title": title,
        "timestamp": ts, "outcome": outcome,
    }


# --------------------------------------------------------------------------
# Percentiles & VWAP
# --------------------------------------------------------------------------
def test_percentiles_and_vwap_on_known_prices():
    # Five equal-USDC buys at 0.1 .. 0.9 -> simple quantiles, VWAP = mean.
    acts = [_buy(p, 100.0) for p in (0.1, 0.3, 0.5, 0.7, 0.9)]
    out = wallet_entry_prices("w", acts)
    assert out["n_buys"] == 5
    assert out["price_p50"] == pytest.approx(0.5)
    assert out["price_p10"] == pytest.approx(0.18, abs=1e-9)
    assert out["price_p90"] == pytest.approx(0.82, abs=1e-9)
    assert out["vwap_price"] == pytest.approx(0.5)
    assert out["buy_usdc"] == pytest.approx(500.0)


def test_vwap_is_usdc_weighted_not_simple_mean():
    # A big buy at 0.9 and a tiny buy at 0.1: VWAP must skew toward 0.9.
    acts = [_buy(0.9, 990.0), _buy(0.1, 10.0)]
    out = wallet_entry_prices("w", acts)
    # VWAP = (0.9*990 + 0.1*10) / 1000 = 0.892
    assert out["vwap_price"] == pytest.approx(0.892)
    # The simple mean would be 0.5 — confirm we did NOT compute that.
    assert out["vwap_price"] > 0.8


def test_odds_band_shares_sum_to_one_and_bucket_correctly():
    # One buy in each of the five bands, equal USDC -> 20% each.
    acts = [_buy(p, 100.0) for p in (0.1, 0.3, 0.5, 0.7, 0.9)]
    out = wallet_entry_prices("w", acts)
    shares = [out[name] for name, _lo, _hi in ODDS_BANDS]
    assert shares == pytest.approx([0.2, 0.2, 0.2, 0.2, 0.2])
    assert sum(shares) == pytest.approx(1.0)


def test_top_band_is_right_inclusive():
    # A buy at exactly 1.0 must land in the top [0.8, 1.0] band.
    out = wallet_entry_prices("w", [_buy(1.0, 100.0)])
    assert out["band_80_100"] == pytest.approx(1.0)
    assert out["band_60_80"] == 0.0


def test_empty_activity_yields_nan_percentiles():
    out = wallet_entry_prices("w", [])
    assert out["n_buys"] == 0
    assert out["price_p50"] != out["price_p50"]  # NaN
    assert out["vwap_price"] != out["vwap_price"]
    for name, _lo, _hi in ODDS_BANDS:
        assert out[name] == 0.0


def test_sells_and_out_of_range_prices_ignored():
    acts = [
        _buy(0.5, 100.0),
        {"type": "TRADE", "side": "SELL", "price": 0.6, "usdc_size": 50.0,
         "slug": "x", "title": "x", "timestamp": 1},
        _buy(1.5, 100.0),   # price out of [0,1] — dropped
        _buy(-0.2, 100.0),  # negative — dropped
    ]
    out = wallet_entry_prices("w", acts)
    assert out["n_buys"] == 1
    assert out["price_p50"] == pytest.approx(0.5)


# --------------------------------------------------------------------------
# 15m subset + entry offset
# --------------------------------------------------------------------------
def test_15m_subset_only_counts_15m_markets():
    acts = [
        _buy(0.3, 100.0, slug="btc-updown-15m-1779472800",
             title="Bitcoin Up or Down", ts=1779473000),
        _buy(0.8, 100.0, slug="bitcoin-above-78k-on-may-24-2026",
             title="Bitcoin above 78k"),  # price-target, NOT 15m
    ]
    out = wallet_entry_prices("w", acts)
    assert out["n_buys"] == 2
    assert out["m15_n_buys"] == 1
    assert out["m15_price_p50"] == pytest.approx(0.3)


def test_15m_entry_offset_from_compact_slug_ts():
    # Window starts at 1779472800; buy at +500 s -> offset 500 s into window.
    acts = [_buy(0.2, 10.0, slug="sol-updown-15m-1779472800",
                 title="Solana Up or Down", ts=1779472800 + 500)]
    out = wallet_entry_prices("w", acts)
    assert out["m15_offset_n"] == 1
    assert out["m15_offset_coverage"] == pytest.approx(1.0)
    assert out["m15_entry_offset_p50"] == pytest.approx(500.0)


def test_15m_offset_skips_implausible_values():
    # A buy 2 hours after the window start is implausible -> not counted.
    acts = [_buy(0.2, 10.0, slug="sol-updown-15m-1779472800",
                 title="Solana Up or Down", ts=1779472800 + 7200)]
    out = wallet_entry_prices("w", acts)
    assert out["m15_offset_n"] == 0
    assert out["m15_entry_offset_p50"] != out["m15_entry_offset_p50"]  # NaN


# --------------------------------------------------------------------------
# End-to-end against the real cache
# --------------------------------------------------------------------------
@needs_cache
def test_entry_prices_all_one_row_per_wallet():
    ep = entry_prices_all()
    assert len(ep) == 239
    assert "proxy_wallet" in ep.columns
    assert ep["proxy_wallet"].is_unique
    # At least some wallets have real 15m buy-price data.
    assert (ep["m15_n_buys"] > 0).sum() > 50
