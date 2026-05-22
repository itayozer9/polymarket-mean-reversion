"""Tests for the leaderboard directional-holder backtest (M15-DH-1).

Covers the pure pieces — favourite-side identification, entry-tick selection,
the fee calc, settlement, the taker PnL, and the realistic maker fill model.
No mocking: the pure-function tests build small explicit tick frames; the
data-shape test reads the real 15m parquet.
"""
import numpy as np
import pandas as pd
import pytest

from research.analysis.leaderboard_strategy_backtest import (
    ENTRY_ZONE, WINDOW_LEN, fee, favourite_side, settle, select_entry_tick,
    taker_pnl, maker_trade, backtest_band, STAKE,
)


# ---------------------------------------------------------------------------
# favourite_side
# ---------------------------------------------------------------------------

def test_favourite_side_picks_yes_when_yes_mid_higher():
    assert favourite_side(0.70, 0.30) == "YES"


def test_favourite_side_picks_no_when_no_mid_higher():
    assert favourite_side(0.25, 0.75) == "NO"


def test_favourite_side_none_on_exact_tie():
    assert favourite_side(0.50, 0.50) is None


def test_favourite_side_is_the_above_half_leg():
    # By construction the favourite is the side with mid > 0.50.
    for ym in (0.51, 0.62, 0.88, 0.99):
        fav = favourite_side(ym, 1.0 - ym)
        assert fav == "YES" and ym > 0.50
    for nm in (0.55, 0.73, 0.95):
        fav = favourite_side(1.0 - nm, nm)
        assert fav == "NO" and nm > 0.50


# ---------------------------------------------------------------------------
# fee
# ---------------------------------------------------------------------------

def test_fee_zero_at_certainty():
    # A fee 0.07*p*(1-p) vanishes at p in {0,1} — a held position settling at
    # resolution pays no exit fee.
    assert fee(0.0) == pytest.approx(0.0)
    assert fee(1.0) == pytest.approx(0.0)


def test_fee_max_at_half():
    # The 0.07*p*(1-p) parabola peaks at p=0.5 -> 0.07*0.25 = 0.0175.
    assert fee(0.5) == pytest.approx(0.0175)
    assert fee(0.5) > fee(0.7) > fee(0.9)


def test_fee_symmetric():
    assert fee(0.3) == pytest.approx(fee(0.7))


# ---------------------------------------------------------------------------
# settle
# ---------------------------------------------------------------------------

def test_settle_yes_wins_on_up():
    assert settle("YES", 1.0) == 1
    assert settle("YES", 0.0) == 0


def test_settle_no_wins_on_down():
    assert settle("NO", 0.0) == 1
    assert settle("NO", 1.0) == 0


def test_settle_rejects_bad_side():
    with pytest.raises(ValueError):
        settle("MAYBE", 1.0)


# ---------------------------------------------------------------------------
# entry-tick selection
# ---------------------------------------------------------------------------

def _window(rows):
    """Build a one-window tick frame from (sec, yes_bid, yes_ask, no_bid,
    no_ask) tuples. yes_mid/no_mid are the bid/ask averages."""
    recs = []
    for sec, yb, ya, nb, na in rows:
        recs.append({
            "seconds_into_window": float(sec),
            "yes_best_bid": yb, "yes_best_ask": ya,
            "no_best_bid": nb, "no_best_ask": na,
            "yes_mid": 0.5 * (yb + ya), "no_mid": 0.5 * (nb + na),
        })
    return pd.DataFrame(recs)


def test_select_entry_tick_takes_first_qualifying_in_zone():
    # Favourite (YES) mid rises into [0.60,0.90] at sec=40.
    w = _window([
        (10, 0.50, 0.52, 0.48, 0.50),   # fav_mid 0.51 — below band
        (40, 0.64, 0.66, 0.34, 0.36),   # fav_mid 0.65 — in band  <-- pick
        (80, 0.74, 0.76, 0.24, 0.26),   # fav_mid 0.75 — also in band
    ])
    e = select_entry_tick(w, "15m", 0.60, 0.90)
    assert e is not None
    assert e["seconds_into_window"] == 40.0
    assert e["side"] == "YES"
    assert e["fav_mid"] == pytest.approx(0.65)
    assert e["fav_ask"] == pytest.approx(0.66)


def test_select_entry_tick_respects_entry_zone():
    # The only in-band tick is at sec=200 — inside the 15m zone (<=300) but
    # outside the 5m zone (<=100).
    w = _window([(200, 0.70, 0.72, 0.28, 0.30)])
    assert select_entry_tick(w, "15m", 0.60, 0.90) is not None
    assert select_entry_tick(w, "5m", 0.60, 0.90) is None


def test_select_entry_tick_none_when_favourite_outside_band():
    w = _window([
        (20, 0.50, 0.52, 0.48, 0.50),   # fav_mid 0.51
        (50, 0.93, 0.95, 0.05, 0.07),   # fav_mid 0.94 — above 0.90
    ])
    assert select_entry_tick(w, "15m", 0.60, 0.90) is None


def test_select_entry_tick_identifies_no_as_favourite():
    # NO is the favourite (no_mid 0.70 > yes_mid 0.30).
    w = _window([(30, 0.29, 0.31, 0.69, 0.71)])
    e = select_entry_tick(w, "15m", 0.60, 0.90)
    assert e is not None and e["side"] == "NO"
    assert e["fav_mid"] == pytest.approx(0.70)
    assert e["fav_ask"] == pytest.approx(0.71)


# ---------------------------------------------------------------------------
# taker PnL
# ---------------------------------------------------------------------------

def test_taker_pnl_winning_trade():
    # Buy YES at ask 0.70, $10 stake -> 14.2857 shares. YES wins (Up).
    # payout = shares*1 ; entry_fee = fee(0.70)*shares.
    shares = STAKE / 0.70
    expected = shares - STAKE - fee(0.70) * shares
    assert taker_pnl(0.70, "YES", 1.0) == pytest.approx(expected)
    assert taker_pnl(0.70, "YES", 1.0) > 0  # a winning favourite is profitable


def test_taker_pnl_losing_trade():
    # Buy YES at ask 0.70 but market resolves Down -> total loss minus fee.
    shares = STAKE / 0.70
    expected = -STAKE - fee(0.70) * shares
    assert taker_pnl(0.70, "YES", 0.0) == pytest.approx(expected)
    assert taker_pnl(0.70, "YES", 0.0) < -STAKE  # worse than -stake (fee)


def test_taker_pnl_fee_costs_money_even_on_a_win():
    # The taker entry fee strictly reduces PnL vs an idealized fee-free fill.
    shares = STAKE / 0.65
    feeless = shares - STAKE
    assert taker_pnl(0.65, "YES", 1.0) < feeless


# ---------------------------------------------------------------------------
# maker fill model
# ---------------------------------------------------------------------------

def test_maker_fills_only_on_a_later_trade_through():
    # Entry tick at sec=30: favourite YES bid 0.64. A later tick at sec=60
    # shows yes_best_bid 0.62 <= 0.64 -> the market traded down through the
    # limit -> fill. Settles at outcome (YES wins).
    w = _window([
        (30, 0.64, 0.66, 0.34, 0.36),
        (60, 0.62, 0.64, 0.36, 0.38),
    ])
    entry = select_entry_tick(w, "15m", 0.60, 0.90)
    res = maker_trade(w, entry, "YES", 1.0)
    assert res["filled"] is True
    shares = STAKE / 0.64        # bought at the limit, no fee
    assert res["pnl"] == pytest.approx(shares - STAKE)


def test_maker_does_not_fill_when_market_never_trades_down():
    # The favourite's bid only ever rises after entry -> the resting buy at the
    # entry bid is never traded through -> no fill, no trade, pnl 0.
    w = _window([
        (30, 0.64, 0.66, 0.34, 0.36),
        (60, 0.70, 0.72, 0.28, 0.30),
        (90, 0.78, 0.80, 0.20, 0.22),
    ])
    entry = select_entry_tick(w, "15m", 0.60, 0.90)
    res = maker_trade(w, entry, "YES", 1.0)
    assert res["filled"] is False
    assert res["pnl"] == 0.0


def test_maker_no_fill_when_no_later_ticks():
    # Entry on the last tick of the window -> nothing later to fill against.
    w = _window([(30, 0.64, 0.66, 0.34, 0.36)])
    entry = select_entry_tick(w, "15m", 0.60, 0.90)
    res = maker_trade(w, entry, "YES", 1.0)
    assert res["filled"] is False


def test_maker_pays_no_fee_taker_does():
    # Same winning favourite: the maker (no fee, fills at the bid) keeps more
    # than the taker (pays the ask + fee) on a win.
    w = _window([
        (30, 0.64, 0.66, 0.34, 0.36),
        (60, 0.63, 0.65, 0.35, 0.37),
    ])
    entry = select_entry_tick(w, "15m", 0.60, 0.90)
    mk = maker_trade(w, entry, "YES", 1.0)
    tk = taker_pnl(entry["fav_ask"], "YES", 1.0)
    assert mk["filled"] and mk["pnl"] > tk


# ---------------------------------------------------------------------------
# entry-zone geometry
# ---------------------------------------------------------------------------

def test_entry_zone_is_first_third():
    assert ENTRY_ZONE["15m"] == WINDOW_LEN["15m"] // 3
    assert ENTRY_ZONE["5m"] == WINDOW_LEN["5m"] // 3


# ---------------------------------------------------------------------------
# backtest_band — integration on a tiny two-window book
# ---------------------------------------------------------------------------

def _book_window(slug, symbol, date, outcome_up, rows):
    df = _window(rows)
    df["slug"] = slug
    df["symbol"] = symbol
    df["date"] = date
    df["outcome_up"] = float(outcome_up)
    df.index = range(len(df))
    return df


def test_backtest_band_one_trade_per_window():
    # Two windows; both qualify in [0.60,0.90]. Expect exactly two trades.
    w1 = _book_window("btc-updown-15m-1", "btc", "2026-05-15", 1.0, [
        (20, 0.64, 0.66, 0.34, 0.36),
        (50, 0.66, 0.68, 0.32, 0.34),
    ])
    w2 = _book_window("btc-updown-15m-2", "btc", "2026-05-15", 0.0, [
        (30, 0.71, 0.73, 0.27, 0.29),
        (60, 0.70, 0.72, 0.28, 0.30),
    ])
    book = pd.concat([w1, w2], ignore_index=True)
    book.index = range(len(book))
    tr = backtest_band(book, "15m", 0.60, 0.90)
    assert len(tr) == 2
    assert set(tr["slug"]) == {"btc-updown-15m-1", "btc-updown-15m-2"}
    # w1 favourite YES wins (Up); w2 favourite YES loses (Down).
    r1 = tr[tr["slug"] == "btc-updown-15m-1"].iloc[0]
    r2 = tr[tr["slug"] == "btc-updown-15m-2"].iloc[0]
    assert r1["won"] == 1 and r1["taker_pnl"] > 0
    assert r2["won"] == 0 and r2["taker_pnl"] < 0


def test_backtest_band_random_side_trades_same_windows():
    # The random-side null trades the SAME windows at the SAME entry ticks as
    # the favourite rule — only the chosen side differs.
    w1 = _book_window("btc-updown-15m-1", "btc", "2026-05-15", 1.0, [
        (20, 0.64, 0.66, 0.34, 0.36),
        (50, 0.66, 0.68, 0.32, 0.34),
    ])
    fav = backtest_band(w1, "15m", 0.60, 0.90, random_side=False)
    nul = backtest_band(w1, "15m", 0.60, 0.90, random_side=True, seed=1)
    assert len(fav) == len(nul) == 1
    assert fav.iloc[0]["entry_sec"] == nul.iloc[0]["entry_sec"]


# ---------------------------------------------------------------------------
# real-data shape / sanity
# ---------------------------------------------------------------------------

def test_real_15m_parquet_outcome_up_is_binary():
    # The 15m parquet's outcome_up must be a clean 0/1 label.
    from research.analysis.leaderboard_strategy_backtest import TICKS_15M
    if not TICKS_15M.exists():
        pytest.skip("ticks_15m.parquet not present")
    df = pd.read_parquet(TICKS_15M, columns=["outcome_up"])
    vals = set(df["outcome_up"].dropna().unique())
    assert vals <= {0, 1, 0.0, 1.0}
