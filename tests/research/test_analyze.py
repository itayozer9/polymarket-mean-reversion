"""Tests for research.wallets.analyze (Task 2.1).

Per CLAUDE.md: no mocking. Round-trip / classification logic is tested on
hand-built activity lists; the cache readers and end-to-end summary are tested
against the 3 real smoke-test wallets already on disk in ``data/wallets/``.
Tests that touch the cache are skipped if the cache is absent.
"""
from __future__ import annotations

import pytest

from research.wallets.analyze import (
    MARKET_TYPES,
    MANIFEST_PATH,
    analyze_all,
    classify_market,
    load_activity,
    load_leaderboards,
    load_manifest,
    load_onchain_roles,
    load_positions,
    reconstruct_roundtrips,
    summarize_wallet,
)

_CACHE_PRESENT = MANIFEST_PATH.exists()
needs_cache = pytest.mark.skipif(
    not _CACHE_PRESENT, reason="data/wallets/ smoke cache not present")


# --------------------------------------------------------------------------
# B. classify_market — real (title, slug) pairs pulled from the cache
# --------------------------------------------------------------------------
def test_classify_compact_updown_5m_and_15m():
    # btc-updown-5m / 15m are the most common slugs in the smoke cache.
    r5 = classify_market("Bitcoin Up or Down - May 22, 2:15PM-2:20PM ET",
                         "btc-updown-5m-1779473700")
    assert r5.market_type == "crypto_15m_updown"
    assert r5.method == "by_slug"

    r15 = classify_market("Bitcoin Up or Down - May 22, 2:15PM-2:30PM ET",
                          "btc-updown-15m-1779473700")
    assert r15.market_type == "crypto_15m_updown"
    assert r15.method == "by_slug"


def test_classify_compact_updown_4h_is_hourly():
    r = classify_market("Bitcoin Up or Down - May 22, 12:00PM-4:00PM ET",
                        "btc-updown-4h-1779465600")
    assert r.market_type == "crypto_hourly_updown"
    assert r.method == "by_slug"


def test_classify_longform_updown_hourly_and_daily():
    # Time-of-day token in slug -> hourly.
    rh = classify_market("Bitcoin Up or Down - May 22, 2PM ET",
                         "bitcoin-up-or-down-may-22-2026-2pm-et")
    assert rh.market_type == "crypto_hourly_updown"
    assert rh.method == "by_slug"
    # Bare date in slug -> daily.
    rd = classify_market("Bitcoin Up or Down on May 23?",
                         "bitcoin-up-or-down-on-may-23-2026")
    assert rd.market_type == "crypto_daily_updown"
    assert rd.method == "by_slug"


def test_classify_price_target_single_date():
    r = classify_market("Will the price of Bitcoin be above $78,000 on May 24?",
                        "bitcoin-above-78k-on-may-24-2026")
    assert r.market_type == "crypto_price_target"
    assert r.method == "by_slug"

    rb = classify_market(
        "Will the price of Bitcoin be between $76,000 and $78,000 on May 23?",
        "will-the-price-of-bitcoin-be-between-76000-78000-on-may-23-2026")
    assert rb.market_type == "crypto_price_target"


def test_classify_longdated_month_and_range():
    # "in May 2026" month span -> weekly_monthly.
    rm = classify_market("Will Bitcoin reach $85,000 in May?",
                         "will-bitcoin-reach-85k-in-may-2026")
    assert rm.market_type == "crypto_weekly_monthly"
    assert rm.method == "by_slug"
    # Multi-day date range -> weekly_monthly.
    rr = classify_market("Will Bitcoin reach $80,000 May 18-24?",
                         "will-bitcoin-reach-80k-may-18-24-2026")
    assert rr.market_type == "crypto_weekly_monthly"


def test_classify_title_fallback_when_slug_unstructured():
    # No recognisable slug -> title fallback.
    r = classify_market("Bitcoin Up or Down - May 22, 11AM ET", "")
    assert r.market_type == "crypto_hourly_updown"
    assert r.method == "by_title"

    rt = classify_market("Will Ethereum reach $5,000 by end of year?",
                         "some-unstructured-slug")
    assert rt.market_type == "crypto_weekly_monthly"
    assert rt.method == "by_title"


def test_classify_non_crypto():
    r = classify_market("Will the Lakers win the 2026 NBA Finals?",
                        "lakers-win-nba-finals-2026")
    assert r.market_type == "non_crypto"


def test_classify_returns_known_bucket_always():
    for title, slug in [("", ""), ("garbage", "garbage"),
                        ("Bitcoin something", "btc-weird")]:
        assert classify_market(title, slug).market_type in MARKET_TYPES


# --------------------------------------------------------------------------
# C. reconstruct_roundtrips — hand-built activity
# --------------------------------------------------------------------------
def _trade(ts, side, size, price, slug="btc-updown-15m-100", outcome="Up",
           tx="0xabc", title="Bitcoin Up or Down - May 22, 1:00PM-1:15PM ET"):
    return {"timestamp": ts, "type": "TRADE", "size": size, "usdc_size":
            size * price, "transaction_hash": tx, "price": price,
            "side": side, "title": title, "outcome": outcome, "slug": slug}


def test_roundtrip_buy_then_sell_exact_pnl():
    activity = [
        _trade(1000, "BUY", 100.0, 0.40),
        _trade(1600, "SELL", 100.0, 0.55),
    ]
    rt = reconstruct_roundtrips(activity, wallet="0xw")
    assert len(rt) == 1
    row = rt.iloc[0]
    assert row["exit_kind"] == "sell"
    assert row["shares"] == 100.0
    assert row["entry_price"] == 0.40
    assert row["exit_price"] == 0.55
    # gross = 100 * (0.55 - 0.40) = 15.0
    assert row["gross_pnl"] == pytest.approx(15.0)
    assert row["hold_seconds"] == 600
    assert row["entry_ts"] == 1000
    assert row["exit_ts"] == 1600
    # No on-chain fee supplied -> estimated taker fee, flagged.
    assert bool(row["fee_estimated"]) is True
    assert row["net_pnl"] == pytest.approx(row["gross_pnl"] - row["fee_usdc"])


def test_roundtrip_fifo_partial_match_across_lots():
    # Two BUY lots, one SELL crossing both -> two round-trip rows, FIFO order.
    activity = [
        _trade(100, "BUY", 50.0, 0.30),
        _trade(200, "BUY", 50.0, 0.40),
        _trade(900, "SELL", 75.0, 0.60),
    ]
    rt_all = reconstruct_roundtrips(activity, wallet="0xw")
    sells = rt_all[rt_all["exit_kind"] == "sell"].sort_values("entry_ts")
    assert len(sells) == 2
    first, second = sells.iloc[0], sells.iloc[1]
    # First lot fully consumed (50 sh @0.30).
    assert first["shares"] == 50.0
    assert first["entry_price"] == 0.30
    assert first["gross_pnl"] == pytest.approx(50.0 * (0.60 - 0.30))
    # Second lot partially consumed (25 sh @0.40).
    assert second["shares"] == 25.0
    assert second["entry_price"] == 0.40
    assert second["gross_pnl"] == pytest.approx(25.0 * (0.60 - 0.40))
    # 25 shares of lot 2 remain open.
    open_rows = rt_all[rt_all["exit_kind"] == "open"]
    assert len(open_rows) == 1
    assert open_rows.iloc[0]["shares"] == 25.0


def test_roundtrip_buy_then_redeem_resolves_at_one():
    redeem = {"timestamp": 5000, "type": "REDEEM", "size": 100.0,
              "usdc_size": 100.0, "transaction_hash": "0xredeem",
              "price": 0.0, "side": "", "title": "", "outcome": "",
              "slug": "btc-updown-15m-100"}
    activity = [_trade(1000, "BUY", 100.0, 0.65), redeem]
    rt = reconstruct_roundtrips(activity, wallet="0xw")
    realized = rt[rt["exit_kind"] != "open"]
    assert len(realized) == 1
    row = realized.iloc[0]
    assert row["exit_kind"] == "resolution"
    assert row["exit_price"] == 1.0
    # gross = 100 * (1.0 - 0.65) = 35.0
    assert row["gross_pnl"] == pytest.approx(35.0)
    assert row["hold_seconds"] == 4000


def test_roundtrip_uses_onchain_fee_when_available():
    activity = [
        _trade(1000, "BUY", 100.0, 0.40, tx="0xbuy"),
        _trade(1600, "SELL", 100.0, 0.55, tx="0xsell"),
    ]
    onchain = [{"tx_hash": "0xsell", "wallet": "0xw", "role": "taker",
                "n_fills": 1, "total_fee_raw": 2000000, "fee_usdc": 2.0}]
    rt = reconstruct_roundtrips(activity, wallet="0xw", onchain_roles=onchain)
    row = rt.iloc[0]
    assert bool(row["fee_estimated"]) is False
    assert row["fee_usdc"] == pytest.approx(2.0)
    assert row["net_pnl"] == pytest.approx(15.0 - 2.0)


def test_roundtrip_merge_is_zero_pnl_unwind():
    # A MERGE closes open lots at their own entry price -> no directional PnL.
    merge = {"timestamp": 5000, "type": "MERGE", "size": 100.0,
             "usdc_size": 100.0, "transaction_hash": "0xmerge",
             "price": 0.0, "side": "", "title": "", "outcome": "",
             "slug": "btc-updown-15m-100"}
    activity = [_trade(1000, "BUY", 100.0, 0.30), merge]
    rt = reconstruct_roundtrips(activity, wallet="0xw")
    merged = rt[rt["exit_kind"] == "merge"]
    assert len(merged) == 1
    assert merged.iloc[0]["gross_pnl"] == pytest.approx(0.0)
    assert merged.iloc[0]["exit_price"] == 0.30


def test_roundtrip_empty_input():
    rt = reconstruct_roundtrips([], wallet="0xw")
    assert len(rt) == 0
    assert list(rt.columns)  # columns still present


# --------------------------------------------------------------------------
# Cache-backed tests (3 real smoke wallets)
# --------------------------------------------------------------------------
@needs_cache
def test_manifest_has_wallets():
    manifest = load_manifest()
    assert manifest.get("wallets")
    assert manifest.get("total_wallets", 0) >= 1


@needs_cache
def test_reconstruct_roundtrips_on_real_wallet():
    manifest = load_manifest()
    wallet = next(iter(manifest["wallets"]))
    activity = load_activity(wallet)
    assert activity, "expected cached activity for the first smoke wallet"
    onchain = load_onchain_roles(wallet)
    rt = reconstruct_roundtrips(activity, wallet=wallet, onchain_roles=onchain)
    assert len(rt) > 0
    realized = rt[rt["exit_kind"] != "open"]
    assert len(realized) > 0
    assert (rt["hold_seconds"] >= 0).all()


@needs_cache
def test_analyze_all_on_smoke_cache():
    manifest = load_manifest()
    n_wallets = len(manifest["wallets"])
    summary_df, roundtrips_df = analyze_all(manifest)
    assert len(summary_df) == n_wallets

    pct_cols = ["pct_vol_15m", "pct_vol_hourly", "pct_vol_daily",
                "pct_vol_longdated", "pct_vol_pricetarget",
                "pct_vol_noncrypto", "pct_vol_other"]
    for _, row in summary_df.iterrows():
        # Market-type shares sum to ~1.0 (a wallet with zero trades sums to 0).
        s = sum(row[c] for c in pct_cols)
        assert s == pytest.approx(1.0, abs=1e-6) or s == pytest.approx(0.0)
        # maker_fill_frac is in [0,1] or NaN.
        mf = row["maker_fill_frac"]
        assert (mf != mf) or (0.0 <= mf <= 1.0)
        # resolution_exit_frac in [0,1] or NaN.
        ref = row["resolution_exit_frac"]
        assert (ref != ref) or (0.0 <= ref <= 1.0)

    # Round-trips concatenated across wallets carry a wallet column.
    if len(roundtrips_df):
        assert set(roundtrips_df["wallet"]).issubset(set(manifest["wallets"]))


@needs_cache
def test_summarize_wallet_pure_dict():
    manifest = load_manifest()
    leaderboards = load_leaderboards()
    wallet, entry = next(iter(manifest["wallets"].items()))
    activity = load_activity(wallet)
    onchain = load_onchain_roles(wallet)
    positions = load_positions(wallet)
    rt = reconstruct_roundtrips(activity, wallet=wallet, onchain_roles=onchain)
    row = summarize_wallet(wallet, entry, activity, rt, onchain, positions,
                           leaderboards)
    assert isinstance(row, dict)
    assert row["proxy_wallet"] == wallet
    assert row["n_roundtrips"] >= 0
    assert row["n_buy_trades"] >= 0
    # The smoke wallets are all on the MONTH board.
    assert row["n_boards"] >= 1
