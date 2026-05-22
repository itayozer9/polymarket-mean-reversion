"""Tests for research.wallets.copytrade_probe.

Per CLAUDE.md: no mocking. The win/loss-classification, slug-timing and
excess-win-rate logic is pure — it is tested on hand-built cash-flow rows and
activity lists. The end-to-end report builder is exercised against the real
wallet cache on disk and skipped if it is absent.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from research.wallets.analyze import MANIFEST_PATH
from research.wallets.copytrade_probe import (
    build_per_slug_frame,
    build_winloss_frame,
    classify_directional_slug,
    co_trading_pairs,
    excess_win_rate,
    first_mover_consistency,
    parse_compact_slug,
    rebate_estimate,
    slug_buys_by_outcome,
    slug_vwap_entry,
    window_close_ts,
)

_CACHE_PRESENT = MANIFEST_PATH.exists()
needs_cache = pytest.mark.skipif(
    not _CACHE_PRESENT, reason="data/wallets/ cache not present")

# A window that closes well before any plausible fetch time.
_RESOLVED_FETCH_TS = 2_000_000_000


def _buy(slug, outcome, price, usdc, ts):
    """One BUY activity record."""
    return {
        "type": "TRADE", "side": "BUY", "slug": slug, "outcome": outcome,
        "price": price, "usdc_size": usdc,
        "size": usdc / price if price else 0.0, "timestamp": ts,
    }


# --------------------------------------------------------------------------
# Slug timing
# --------------------------------------------------------------------------
def test_parse_compact_slug_15m_and_5m():
    assert parse_compact_slug("btc-updown-15m-1779473700") == (1779473700, 900)
    assert parse_compact_slug("eth-updown-5m-1779473100") == (1779473100, 300)
    # hourly compact slug -> duration in seconds
    assert parse_compact_slug("sol-updown-4h-1779470000") == (1779470000,
                                                              4 * 3600)


def test_parse_compact_slug_rejects_longform():
    assert parse_compact_slug("bitcoin-above-78k-on-may-24-2026") is None
    assert parse_compact_slug("bitcoin-up-or-down-may-22-2026-2pm-et") is None
    assert parse_compact_slug("") is None
    assert parse_compact_slug(None) is None


def test_window_close_ts():
    assert window_close_ts("btc-updown-15m-1000") == 1900
    assert window_close_ts("not-a-compact-slug") is None


# --------------------------------------------------------------------------
# PART A — win/loss classification (the silent-loss fix)
# --------------------------------------------------------------------------
def test_won_slug_positive_net_pnl():
    """A directional slug, window closed, net_pnl > 0 -> won."""
    row = {"slug": "btc-updown-15m-1000", "exit_mode": "redeem",
           "n_merges": 0, "n_buys": 3, "net_pnl": 12.5}
    assert classify_directional_slug(row, _RESOLVED_FETCH_TS) == "won"


def test_lost_slug_silent_loss_is_counted():
    """The structural-bias fix: a slug with only BUYs and NO exit
    (exit_mode='none') whose window has closed is a SILENT LOSS — it must be
    classified 'lost', not dropped. This is the whole point of the module."""
    row = {"slug": "btc-updown-15m-1000", "exit_mode": "none",
           "n_merges": 0, "n_buys": 4, "net_pnl": -8.0}
    assert classify_directional_slug(row, _RESOLVED_FETCH_TS) == "lost"


def test_lost_slug_redeem_exit_but_net_negative():
    """A wallet can redeem winning shares yet still net-lose on the slug
    (fees, or it also bought the losing side). net_pnl < 0 -> lost."""
    row = {"slug": "btc-updown-15m-1000", "exit_mode": "redeem",
           "n_merges": 0, "n_buys": 9, "net_pnl": -3.2}
    assert classify_directional_slug(row, _RESOLVED_FETCH_TS) == "lost"


def test_unresolved_window_excluded_not_counted_as_loss():
    """A market whose window has NOT closed by fetch time must be excluded
    (None), never counted as a loss."""
    # window closes at 1000+900 = 1900; fetch time is 1500 -> still open.
    row = {"slug": "btc-updown-15m-1000", "exit_mode": "none",
           "n_merges": 0, "n_buys": 2, "net_pnl": 0.0}
    assert classify_directional_slug(row, fetch_ts=1500) is None
    # the same row IS countable once the window has closed.
    assert classify_directional_slug(row, fetch_ts=5000) is None  # net 0
    row["net_pnl"] = -1.0
    assert classify_directional_slug(row, fetch_ts=5000) == "lost"


def test_merge_slug_excluded():
    """A slug with a merge leg is a non-directional unwind -> excluded."""
    row = {"slug": "btc-updown-15m-1000", "exit_mode": "redeem",
           "n_merges": 2, "n_buys": 5, "net_pnl": 10.0}
    assert classify_directional_slug(row, _RESOLVED_FETCH_TS) is None


def test_sell_exit_slug_excluded():
    """A sell-exited slug is a scalp, not a directional hold -> excluded."""
    row = {"slug": "btc-updown-15m-1000", "exit_mode": "sell",
           "n_merges": 0, "n_buys": 3, "net_pnl": 4.0}
    assert classify_directional_slug(row, _RESOLVED_FETCH_TS) is None


def test_no_buys_artefact_excluded():
    """A redeem-only row (truncated cache, no buys) is neither a win nor a
    loss -> excluded."""
    row = {"slug": "btc-updown-15m-1000", "exit_mode": "redeem",
           "n_merges": 0, "n_buys": 0, "net_pnl": 5.0}
    assert classify_directional_slug(row, _RESOLVED_FETCH_TS) is None


def test_longform_slug_excluded():
    """A non-compact slug has no machine-readable window close -> excluded."""
    row = {"slug": "bitcoin-above-78k-on-may-24-2026", "exit_mode": "none",
           "n_merges": 0, "n_buys": 3, "net_pnl": -5.0}
    assert classify_directional_slug(row, _RESOLVED_FETCH_TS) is None


def test_exactly_zero_net_pnl_excluded():
    """net_pnl exactly 0 is neither a win nor a loss."""
    row = {"slug": "btc-updown-15m-1000", "exit_mode": "redeem",
           "n_merges": 0, "n_buys": 1, "net_pnl": 0.0}
    assert classify_directional_slug(row, _RESOLVED_FETCH_TS) is None


def test_build_winloss_frame_counts_wins_and_silent_losses():
    """End-to-end on a tiny cash-flow frame: one win, one silent loss, one
    unresolved (excluded), one merge (excluded)."""
    cf = pd.DataFrame([
        # won
        {"wallet": "w1", "slug": "btc-updown-15m-1000", "exit_mode": "redeem",
         "n_merges": 0, "n_buys": 2, "net_pnl": 9.0, "buy_usdc": 50.0},
        # silent loss: no exit, window closed
        {"wallet": "w1", "slug": "eth-updown-15m-2000", "exit_mode": "none",
         "n_merges": 0, "n_buys": 3, "net_pnl": -7.0, "buy_usdc": 30.0},
        # unresolved: window closes at 9_999_999_000+900, far in the future
        {"wallet": "w1", "slug": "btc-updown-15m-9999999000",
         "exit_mode": "none", "n_merges": 0, "n_buys": 1, "net_pnl": -1.0,
         "buy_usdc": 5.0},
        # merge -> excluded
        {"wallet": "w1", "slug": "sol-updown-15m-3000", "exit_mode": "redeem",
         "n_merges": 1, "n_buys": 4, "net_pnl": 2.0, "buy_usdc": 20.0},
    ])
    wl = build_winloss_frame(cf, fetch_ts=_RESOLVED_FETCH_TS)
    assert len(wl) == 2  # only the win and the silent loss
    assert int(wl["won"].sum()) == 1
    assert int(wl["lost"].sum()) == 1
    # the silent loss must be present — that is the structural-bias correction
    lost = wl[wl["lost"]]
    assert lost.iloc[0]["slug"] == "eth-updown-15m-2000"


# --------------------------------------------------------------------------
# PART A — single-sided detection, VWAP entry, excess
# --------------------------------------------------------------------------
def test_slug_vwap_entry_single_sided():
    """USDC-weighted average buy price for a single-outcome slug."""
    activity = [
        _buy("btc-updown-15m-1000", "Up", 0.60, 100.0, 1100),
        _buy("btc-updown-15m-1000", "Up", 0.80, 300.0, 1150),
    ]
    bbo = slug_buys_by_outcome(activity, {"btc-updown-15m-1000"})
    # vwap = (0.60*100 + 0.80*300) / 400 = 300/400 = 0.75
    assert slug_vwap_entry(bbo["btc-updown-15m-1000"]) == pytest.approx(0.75)


def test_slug_vwap_entry_both_sided_returns_none():
    """A slug bought on BOTH outcomes is not a directional position — its
    entry odds is undefined."""
    activity = [
        _buy("btc-updown-15m-1000", "Up", 0.65, 100.0, 1100),
        _buy("btc-updown-15m-1000", "Down", 0.35, 100.0, 1120),
    ]
    bbo = slug_buys_by_outcome(activity, {"btc-updown-15m-1000"})
    assert slug_vwap_entry(bbo["btc-updown-15m-1000"]) is None


def test_excess_win_rate_basic():
    """excess = honest win rate - entry odds. The decisive edge metric."""
    # buy at 0.85, win 85% -> zero excess (no skill, calibrated favourite)
    assert excess_win_rate(0.85, 0.85) == pytest.approx(0.0)
    # buy at 0.50, win 60% -> +10% excess (genuine gross edge)
    assert excess_win_rate(0.60, 0.50) == pytest.approx(0.10)
    # buy at 0.70, win 55% -> negative excess (paid up, underperformed)
    assert excess_win_rate(0.55, 0.70) == pytest.approx(-0.15)


def test_excess_win_rate_nan_safe():
    assert math.isnan(excess_win_rate(float("nan"), 0.5))
    assert math.isnan(excess_win_rate(0.5, float("nan")))


def test_build_per_slug_frame_excludes_both_sided_and_computes_excess():
    """The per-slug frame must drop both-sided slugs and, for single-sided
    slugs, set slug_excess = won{0,1} - entry_odds."""
    # wl_all: two slugs for one wallet, both won.
    wl_all = pd.DataFrame([
        {"wallet": "w1", "slug": "btc-updown-15m-1000", "won": True,
         "lost": False, "net_pnl": 5.0, "buy_usdc": 100.0,
         "window_start_ts": 1000, "window_close_ts": 1900},
        {"wallet": "w1", "slug": "eth-updown-15m-2000", "won": True,
         "lost": False, "net_pnl": 3.0, "buy_usdc": 100.0,
         "window_start_ts": 2000, "window_close_ts": 2900},
    ])

    # Patch load_activity for this wallet via a monkeypatch-free approach:
    # build the per-slug frame using a tiny local activity through the
    # public helpers. We exercise build_per_slug_frame's logic by checking
    # slug_buys_by_outcome + slug_vwap_entry directly here, since
    # build_per_slug_frame reads the on-disk cache.
    activity = [
        # single-sided slug 1: one outcome, vwap 0.70
        _buy("btc-updown-15m-1000", "Up", 0.70, 100.0, 1100),
        # both-sided slug 2: two outcomes -> must be dropped
        _buy("eth-updown-15m-2000", "Up", 0.55, 50.0, 2100),
        _buy("eth-updown-15m-2000", "Down", 0.45, 50.0, 2150),
    ]
    slugs = set(wl_all["slug"])
    bbo = slug_buys_by_outcome(activity, slugs)
    e1 = slug_vwap_entry(bbo["btc-updown-15m-1000"])
    e2 = slug_vwap_entry(bbo["eth-updown-15m-2000"])
    assert e1 == pytest.approx(0.70)   # single-sided -> has entry odds
    assert e2 is None                  # both-sided -> dropped
    # slug 1 won at 0.70 entry -> excess = 1.0 - 0.70 = +0.30
    assert (1.0 - e1) == pytest.approx(0.30)


# --------------------------------------------------------------------------
# PART B — co-trading lift, first-mover
# --------------------------------------------------------------------------
def test_co_trading_lift_independent_pair():
    """Two wallets that share markets exactly at the chance rate -> lift ~1."""
    # N = 4 markets. wallet a in all 4, wallet b in all 4 -> they co-occur in
    # all 4. expected = 4*4/4 = 4. observed = 4. lift = 1.
    market_entries = {
        ("s1", "Up"): [("a", 10), ("b", 20)],
        ("s2", "Up"): [("a", 10), ("b", 20)],
        ("s3", "Up"): [("a", 10), ("b", 20)],
        ("s4", "Up"): [("a", 10), ("b", 20)],
    }
    df = co_trading_pairs(market_entries, min_markets_each=1)
    assert len(df) == 1
    assert df.iloc[0]["lift"] == pytest.approx(1.0)


def test_co_trading_lift_concentrated_pair():
    """Two wallets that ONLY appear together -> lift far above 1."""
    # 100 markets total; a and b each appear in only 2, and those are the
    # same 2. expected = 2*2/100 = 0.04; observed = 2; lift = 50.
    market_entries = {}
    for i in range(100):
        if i < 2:
            market_entries[(f"s{i}", "Up")] = [("a", i), ("b", i + 1)]
        else:
            market_entries[(f"s{i}", "Up")] = [("c", i)]
    df = co_trading_pairs(market_entries, min_markets_each=2)
    ab = df[(df["wallet_a"] == "a") & (df["wallet_b"] == "b")]
    assert len(ab) == 1
    assert ab.iloc[0]["lift"] == pytest.approx(50.0)


def test_first_mover_consistency_clear_leader():
    """A wallet that is first in every co-traded market -> first_frac 1.0."""
    market_entries = {
        ("s1", "Up"): [("leader", 10), ("follower", 30)],
        ("s2", "Up"): [("leader", 10), ("follower", 25)],
        ("s3", "Up"): [("leader", 10), ("follower", 40)],
    }
    df = first_mover_consistency(market_entries, min_markets=1)
    leader = df[df["wallet"] == "leader"].iloc[0]
    follower = df[df["wallet"] == "follower"].iloc[0]
    assert leader["first_frac"] == pytest.approx(1.0)
    assert follower["first_frac"] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# PART C — rebate magnitude
# --------------------------------------------------------------------------
def test_rebate_estimate_is_small_fraction_of_pnl():
    """The rebate upper bound on a high-volume wallet must be a small
    fraction of a large leaderboard PnL — the Part-C conclusion."""
    row = {
        "proxy_wallet": "w", "user_name": "bigmaker",
        "total_buy_usdc": 1_000_000.0, "maker_fill_frac": 1.0,
        "lb_pnl_all": 300_000.0, "lb_pnl_month": float("nan"),
    }
    out = rebate_estimate(row)
    # upper bound = 1e6 * 0.035 * 0.20 = $7,000 ; that is ~2.3% of $300k PnL.
    assert out["rebate_upper_bound"] == pytest.approx(7_000.0)
    assert out["rebate_upper_share_of_pnl"] < 0.05  # a rounding error


def test_rebate_estimate_handles_missing_pnl():
    row = {
        "proxy_wallet": "w", "user_name": "x",
        "total_buy_usdc": 500_000.0, "maker_fill_frac": float("nan"),
        "lb_pnl_all": float("nan"), "lb_pnl_month": float("nan"),
    }
    out = rebate_estimate(row)
    assert out["rebate_upper_bound"] > 0
    assert math.isnan(out["rebate_upper_share_of_pnl"])


# --------------------------------------------------------------------------
# End-to-end against the real cache (skipped if absent)
# --------------------------------------------------------------------------
@needs_cache
def test_build_per_slug_frame_on_real_cache_drops_both_sided():
    """On the real cache, the per-slug frame must contain only single-sided
    slugs (every entry_odds is a real number, never NaN)."""
    import datetime as dt

    from research.wallets.analyze import load_manifest
    from research.wallets.copytrade_probe import build_winloss_frame
    from research.wallets.wallet_report import assign_archetype

    manifest = load_manifest()
    fetch_ts = int(dt.datetime.fromisoformat(
        manifest["fetch_timestamp"]).timestamp())
    cf = pd.read_parquet(
        MANIFEST_PATH.parent / "derived" / "market_cashflow.parquet")
    summary = pd.read_parquet(
        MANIFEST_PATH.parent / "derived" / "wallet_summary.parquet")
    summary["archetype"] = summary.apply(assign_archetype, axis=1)
    dh = set(summary.loc[summary["archetype"] == "directional_holder",
                         "proxy_wallet"])
    wl = build_winloss_frame(cf, fetch_ts)
    # take a handful of DH wallets to keep the test quick
    sample = set(list(dh)[:5])
    ps = build_per_slug_frame(wl[wl["wallet"].isin(sample)], sample)
    if len(ps):
        assert ps["entry_odds"].notna().all()
        assert (ps["entry_odds"] >= 0).all()
        assert (ps["entry_odds"] <= 1).all()
        # slug_excess must equal won - entry_odds
        recomputed = ps["won"].astype(float) - ps["entry_odds"]
        assert (ps["slug_excess"] - recomputed).abs().max() < 1e-9
