"""Tests for research.wallets.wallet_report (Task 2.2 — archetypes & report).

Per CLAUDE.md: no mocking. ``assign_archetype`` is tested on hand-built
summary rows that each hit exactly one branch of the decision cascade; the
persistence filter and report builder are exercised against the real derived
parquet on disk and skipped if it is absent.
"""
from __future__ import annotations

import pandas as pd
import pytest

from research.wallets.analyze import DERIVED_DIR
from research.wallets.wallet_report import (
    ARCH_DIR_HOLDER,
    ARCH_MINT_MERGE,
    ARCH_PASSIVE_LP,
    ARCH_SCALPER,
    ARCH_UNKNOWN,
    assign_archetype,
    board_combo,
    dominant_market_type,
    entry_role,
    persistence_rank,
)

_SUMMARY_PATH = DERIVED_DIR / "wallet_summary.parquet"
needs_derived = pytest.mark.skipif(
    not _SUMMARY_PATH.exists(),
    reason="data/wallets/derived/wallet_summary.parquet not present")


def _row(**overrides):
    """A neutral wallet-summary row; overrides set the archetype-deciding
    metrics. Defaults are chosen so nothing fires unless explicitly set."""
    base = {
        "pct_vol_noncrypto": 0.0, "pct_vol_15m": 1.0, "pct_vol_hourly": 0.0,
        "pct_vol_daily": 0.0, "pct_vol_longdated": 0.0,
        "pct_vol_pricetarget": 0.0, "pct_vol_other": 0.0,
        "merge_share": 0.0, "redeem_share": 0.0, "sell_share": 0.0,
        "maker_fill_frac": float("nan"), "median_hold_seconds": 1000.0,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# assign_archetype — one test per branch
# --------------------------------------------------------------------------
def test_archetype_noncrypto_routes_to_unknown():
    # Non-crypto dominant must short-circuit to mixed_or_unknown even when
    # other directional signals are present.
    r = _row(pct_vol_noncrypto=0.7, redeem_share=0.9)
    assert assign_archetype(r) == ARCH_UNKNOWN


def test_archetype_mint_merge():
    r = _row(merge_share=0.8, redeem_share=0.1)
    assert assign_archetype(r) == ARCH_MINT_MERGE


def test_archetype_mint_merge_wins_over_redeem():
    # merge_share > 0.5 fires before the redeem rule.
    r = _row(merge_share=0.6, redeem_share=0.6)
    assert assign_archetype(r) == ARCH_MINT_MERGE


def test_archetype_passive_liquidity_provider():
    # Maker-dominant AND flattens by selling (not held to resolution).
    r = _row(maker_fill_frac=0.8, sell_share=0.7, redeem_share=0.2)
    assert assign_archetype(r) == ARCH_PASSIVE_LP


def test_archetype_maker_but_holds_is_directional_not_lp():
    # A maker-dominant wallet that holds to resolution is NOT a market maker.
    r = _row(maker_fill_frac=0.9, sell_share=0.1, redeem_share=0.9)
    assert assign_archetype(r) == ARCH_DIR_HOLDER


def test_archetype_directional_holder():
    r = _row(redeem_share=0.9, merge_share=0.05, sell_share=0.05)
    assert assign_archetype(r) == ARCH_DIR_HOLDER


def test_archetype_scalper():
    # Sell-dominant but NOT maker-dominant -> active trader / scalper.
    r = _row(sell_share=0.7, redeem_share=0.2, maker_fill_frac=0.2)
    assert assign_archetype(r) == ARCH_SCALPER


def test_archetype_unknown_fallback():
    # Nothing dominant -> mixed_or_unknown.
    r = _row(redeem_share=0.3, sell_share=0.3, merge_share=0.3)
    assert assign_archetype(r) == ARCH_UNKNOWN


def test_archetype_accepts_pandas_series():
    s = pd.Series(_row(redeem_share=0.9))
    assert assign_archetype(s) == ARCH_DIR_HOLDER


# --------------------------------------------------------------------------
# entry_role
# --------------------------------------------------------------------------
def test_entry_role_buckets():
    assert entry_role(_row(maker_fill_frac=0.8)) == "maker"
    assert entry_role(_row(maker_fill_frac=0.2)) == "taker"
    assert entry_role(_row(maker_fill_frac=0.5)) == "mixed"
    # NaN coverage hole must NOT be reported as taker.
    assert entry_role(_row(maker_fill_frac=float("nan"))) == "unknown"


def test_dominant_market_type():
    assert dominant_market_type(
        _row(pct_vol_15m=0.9, pct_vol_pricetarget=0.1)) == "crypto_15m_updown"
    assert dominant_market_type(
        _row(pct_vol_15m=0.1, pct_vol_pricetarget=0.9)
    ) == "crypto_price_target"


def test_board_combo():
    assert board_combo({"on_all_board": True, "on_month_board": True,
                        "on_week_board": False}) == "ALL+MONTH"
    assert board_combo({"on_all_board": False, "on_month_board": False,
                        "on_week_board": False}) == "(none)"


# --------------------------------------------------------------------------
# persistence_rank
# --------------------------------------------------------------------------
def test_persistence_filter_keeps_only_multi_board():
    df = pd.DataFrame([
        {"proxy_wallet": "a", "n_boards": 1, "on_all_board": True,
         "on_month_board": False, "on_week_board": False,
         "lb_pnl_month": None, "lb_pnl_week": None, "lb_pnl_all": 100.0},
        {"proxy_wallet": "b", "n_boards": 2, "on_all_board": True,
         "on_month_board": True, "on_week_board": False,
         "lb_pnl_month": 50.0, "lb_pnl_week": None, "lb_pnl_all": 200.0},
        {"proxy_wallet": "c", "n_boards": 3, "on_all_board": True,
         "on_month_board": True, "on_week_board": True,
         "lb_pnl_month": 10.0, "lb_pnl_week": 10.0, "lb_pnl_all": 10.0},
    ])
    pers = persistence_rank(df)
    assert set(pers["proxy_wallet"]) == {"b", "c"}
    # b and c both on ALL+MONTH; b has higher total PnL -> ranked first.
    assert list(pers["proxy_wallet"]) == ["b", "c"]
    assert bool(pers.iloc[0]["on_all_and_month"]) is True


# --------------------------------------------------------------------------
# End-to-end against the real derived parquet
# --------------------------------------------------------------------------
@needs_derived
def test_archetypes_cover_all_239_wallets():
    df = pd.read_parquet(_SUMMARY_PATH)
    assert len(df) == 239
    arch = df.apply(assign_archetype, axis=1)
    assert arch.notna().all()
    # The directional_holder bucket must be the largest, per the population.
    assert arch.value_counts().idxmax() == ARCH_DIR_HOLDER


@needs_derived
def test_persistence_count_is_54():
    df = pd.read_parquet(_SUMMARY_PATH)
    pers = persistence_rank(df)
    assert len(pers) == 54
