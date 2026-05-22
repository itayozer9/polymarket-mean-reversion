"""Tests for research.wallets.analyze (Task 2.1).

Per CLAUDE.md: no mocking. Round-trip / classification logic is tested on
hand-built activity lists; the cache readers and end-to-end summary are tested
against the 3 real smoke-test wallets already on disk in ``data/wallets/``.
Tests that touch the cache are skipped if the cache is absent.
"""
from __future__ import annotations

import pytest

from research.wallets.analyze import (
    MARKET_CASHFLOW_COLUMNS,
    MARKET_TYPES,
    MANIFEST_PATH,
    analyze_all,
    classify_market,
    load_activity,
    load_leaderboards,
    load_manifest,
    load_onchain_roles,
    load_positions,
    reconstruct_market_cashflow,
    reconstruct_roundtrips,
    summarize_wallet,
)

RANK1_WALLET = "0x6e1d5040d0ac73709b0621f620d2a60b80d2d0fa"

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
# C2. reconstruct_market_cashflow — hand-built activity
# --------------------------------------------------------------------------
def _merge(ts, size, slug, tx="0xmerge", title=""):
    return {"timestamp": ts, "type": "MERGE", "size": size, "usdc_size": size,
            "transaction_hash": tx, "price": 0.0, "side": "", "title": title,
            "outcome": "", "slug": slug}


def _redeem(ts, size, slug, tx="0xredeem", title=""):
    return {"timestamp": ts, "type": "REDEEM", "size": size, "usdc_size": size,
            "transaction_hash": tx, "price": 0.0, "side": "", "title": title,
            "outcome": "", "slug": slug}


def test_cashflow_buy_yes_buy_no_then_merge():
    # Mint-buy-merge: buy 100 YES @0.30 and 100 NO @0.65, then merge 100 pairs
    # back to 100 USDC collateral. No on-chain receipts -> estimated taker fee
    # on the two BUY legs only.
    slug = "will-bitcoin-reach-78k-on-may-22"
    activity = [
        _trade(100, "BUY", 100.0, 0.30, slug=slug, outcome="Yes"),
        _trade(110, "BUY", 100.0, 0.65, slug=slug, outcome="No"),
        _merge(200, 100.0, slug),
    ]
    cf = reconstruct_market_cashflow(activity, wallet="0xw")
    assert len(cf) == 1
    row = cf.iloc[0]
    buy_cost = 100.0 * 0.30 + 100.0 * 0.65
    est_fee = (100.0 * 0.07 * 0.30 * 0.70) + (100.0 * 0.07 * 0.65 * 0.35)
    assert row["cash_out"] == pytest.approx(buy_cost)
    assert row["cash_in"] == pytest.approx(100.0)        # merge collateral
    assert row["merge_usdc"] == pytest.approx(100.0)
    assert row["fees"] == pytest.approx(est_fee)
    # net = merge_collateral - buy_cost - fees
    assert row["net_pnl"] == pytest.approx(100.0 - buy_cost - est_fee)
    assert row["exit_mode"] == "merge"
    assert row["n_buys"] == 2 and row["n_merges"] == 1


def test_cashflow_buy_then_redeem():
    # Buy 200 winning shares @0.40, hold to resolution, redeem for 200 USDC.
    slug = "will-bitcoin-dip-to-75k-on-may-22"
    activity = [
        _trade(100, "BUY", 200.0, 0.40, slug=slug, outcome="Yes"),
        _redeem(900, 200.0, slug),
    ]
    cf = reconstruct_market_cashflow(activity, wallet="0xw")
    assert len(cf) == 1
    row = cf.iloc[0]
    buy_cost = 200.0 * 0.40
    est_fee = 200.0 * 0.07 * 0.40 * 0.60
    assert row["cash_out"] == pytest.approx(buy_cost)
    assert row["cash_in"] == pytest.approx(200.0)
    assert row["redeem_usdc"] == pytest.approx(200.0)
    assert row["net_pnl"] == pytest.approx(200.0 - buy_cost - est_fee)
    assert row["exit_mode"] == "redeem"


def test_cashflow_uses_onchain_fee_once_per_tx():
    # Two BUYs sharing one tx -> the on-chain fee is counted once, not twice.
    slug = "btc-updown-15m-100"
    activity = [
        _trade(100, "BUY", 50.0, 0.40, slug=slug, outcome="Up", tx="0xt"),
        _trade(110, "BUY", 50.0, 0.40, slug=slug, outcome="Up", tx="0xt"),
        _redeem(900, 100.0, slug, tx="0xr"),
    ]
    onchain = [{"tx_hash": "0xt", "wallet": "0xw", "role": "taker",
                "n_fills": 2, "total_fee_raw": 0, "fee_usdc": 1.5}]
    cf = reconstruct_market_cashflow(activity, onchain_roles=onchain,
                                     wallet="0xw")
    row = cf.iloc[0]
    assert row["fees"] == pytest.approx(1.5)  # counted once for the shared tx
    assert row["net_pnl"] == pytest.approx(100.0 - 40.0 - 1.5)


def test_cashflow_empty_input():
    cf = reconstruct_market_cashflow([], wallet="0xw")
    assert len(cf) == 0
    assert list(cf.columns) == MARKET_CASHFLOW_COLUMNS


@needs_cache
def test_cashflow_on_rank1_wallet_is_merge_dominated():
    # The rank-1 crypto wallet runs a merge-heavy strategy: its cash-flow
    # frame must be non-empty and dominated by merge-exit markets.
    activity = load_activity(RANK1_WALLET)
    assert activity, "rank-1 wallet activity must be cached"
    onchain = load_onchain_roles(RANK1_WALLET)
    cf = reconstruct_market_cashflow(activity, onchain, wallet=RANK1_WALLET)
    assert len(cf) > 0
    # Merge is the single most common exit mode for this wallet.
    mode_counts = cf["exit_mode"].value_counts()
    assert mode_counts.idxmax() == "merge"
    # And merge collateral is the largest share of total cash-in.
    assert cf["merge_usdc"].sum() > cf["redeem_usdc"].sum()
    assert cf["merge_usdc"].sum() > cf["sell_usdc"].sum()


# --------------------------------------------------------------------------
# Cache-backed tests
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
def test_analyze_all_on_cache():
    manifest = load_manifest()
    n_wallets = len(manifest["wallets"])
    summary_df, roundtrips_df, cashflow_df = analyze_all(manifest)
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
        # cash-flow shares in [0,1] or NaN.
        for c in ("merge_share", "redeem_share", "sell_share"):
            v = row[c]
            assert (v != v) or (-1e-9 <= v <= 1.0 + 1e-9)

    # Round-trips / cash-flows concatenated across wallets carry a wallet col.
    if len(roundtrips_df):
        assert set(roundtrips_df["wallet"]).issubset(set(manifest["wallets"]))
    if len(cashflow_df):
        assert set(cashflow_df["wallet"]).issubset(set(manifest["wallets"]))


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
