"""Per-order fill detection (the execution-fix for the live probe).

The first real probe fill was logged as 46.48 shares / avg 0.233 for a ~13-share,
~0.74 order — because fill detection polled WALLET BALANCE deltas on a wallet shared
with the elon-tweets bot (pUSD noise + balance-API lag). The fix reads the fill from
the ORDER itself (get_order.size_matched + the order's realized trades), which is
per-order and immune to that noise. These tests pin the parser with synthetic V2 REST
shapes (no network). Skipped under the 3.9 paper-bot venv (SDK is 3.11-only).
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("py_clob_client_v2")  # SDK only present in the 3.11 live env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mean_reversion_live.live.clob_trade import (  # noqa: E402
    ClobTradeClient, _f, _trade_refs_order, clamp_buy_fallback,
)


def test_f_parses_rest_strings():
    assert _f("0.74") == 0.74
    assert _f(13) == 13.0
    assert _f(None) == 0.0
    assert _f("garbage") == 0.0


def test_trade_refs_order_taker_and_maker():
    oid = "0xABC"
    assert _trade_refs_order({"taker_order_id": "0xABC"}, oid)
    assert _trade_refs_order({"maker_orders": [{"order_id": "0xABC"}]}, oid)
    assert _trade_refs_order({"makerOrders": [{"orderID": "0xABC"}]}, oid)
    assert not _trade_refs_order({"taker_order_id": "0xZZZ"}, oid)
    assert not _trade_refs_order({}, oid)


class _FakeClient:
    """Stands in for ClobClient.get_order / get_trades (sync; wrapped via to_thread)."""

    def __init__(self, order, trades):
        self._order = order
        self._trades = trades
        self.order_calls = 0

    def get_order(self, order_id):
        self.order_calls += 1
        return self._order

    def get_trades(self, params=None, only_first_page=False, next_cursor=None):
        return self._trades


def _client_with(order, trades):
    c = ClobTradeClient.__new__(ClobTradeClient)   # bypass __init__ (no keys/network)
    c._client = _FakeClient(order, trades)
    return c


def test_fill_exact_from_trades_matches_intent_not_balance_noise():
    """The eth-probe scenario, done right: order says 13 shares matched at ~0.74,
    trades confirm. Result must be ~13 shares / ~$9.6 / avg ~0.74 — NOT the
    46-share / 0.233 balance-noise the old path produced."""
    oid = "0xETH"
    order = {"status": "MATCHED", "size_matched": "13", "price": "0.79",
             "asset_id": "tok123"}
    trades = [
        {"taker_order_id": oid, "size": "10", "price": "0.74"},
        {"taker_order_id": oid, "size": "3", "price": "0.76"},
        {"taker_order_id": "0xOTHER", "size": "99", "price": "0.10"},  # must be ignored
    ]
    c = _client_with(order, trades)
    filled, usdc, avg, status, src = asyncio.run(
        c._fill_from_order_api(oid, token_id="tok123", side="BUY"))
    assert filled == 13.0
    assert src == "trades"
    assert abs(avg - (10 * 0.74 + 3 * 0.76) / 13) < 1e-6   # ~0.7446
    assert abs(usdc - 13 * avg) < 1e-6
    assert 9.0 < usdc < 10.0 and status == "matched"


def test_fill_falls_back_to_limit_price_when_no_trades():
    """No retrievable trades -> use the order's (limit) price as a conservative
    upper-bound for usdc, flagged price_src='limit'."""
    oid = "0xNOTRADES"
    order = {"status": "MATCHED", "size_matched": "5", "price": "0.80", "asset_id": "t"}
    c = _client_with(order, [])
    filled, usdc, avg, status, src = asyncio.run(
        c._fill_from_order_api(oid, token_id="t", side="BUY"))
    assert filled == 5.0 and avg == 0.80 and src == "limit"
    assert abs(usdc - 4.0) < 1e-9


def test_balance_fallback_clamps_impossible_cost():
    """The 2026-06-18 fav_disagree_live incident: order API unreadable -> balance
    fallback measured pUSD delta -5.14057 for an 11-share BUY at limit 0.45. A CLOB
    IOC can never cost more than shares x limit (11 x 0.45 = $4.95), so the extra
    $0.19 was shared-wallet pollution, not money paid for THIS order. The recorded
    avg_price (0.4673) breached the strategy's max_ask cap purely as a measurement
    artifact. The fallback must clamp cost to the physical bound."""
    shares, usdc, clamped = clamp_buy_fallback(
        tok_delta=11.0, pusd_delta=-5.14057, limit_price=0.45)
    assert shares == 11.0
    assert abs(usdc - 4.95) < 1e-9          # clamped to shares x limit
    assert clamped is True
    assert usdc / shares <= 0.45 + 1e-9     # avg can no longer breach the cap


def test_balance_fallback_passes_clean_measurement():
    """A clean delta within the physical bound goes through untouched."""
    shares, usdc, clamped = clamp_buy_fallback(
        tok_delta=6.0, pusd_delta=-4.62, limit_price=0.78)
    assert (shares, usdc, clamped) == (6.0, 4.62, False)


def test_balance_fallback_zero_shares_records_zero_cost():
    """No shares received but pUSD moved (pure pollution) -> no phantom cost."""
    shares, usdc, clamped = clamp_buy_fallback(
        tok_delta=0.0, pusd_delta=-3.10, limit_price=0.50)
    assert shares == 0.0 and usdc == 0.0 and clamped is True


def test_fill_zero_on_killed_no_match():
    """A FAK that matched nothing -> 0 filled, terminal status, no crash."""
    oid = "0xKILL"
    order = {"status": "UNMATCHED", "size_matched": "0", "price": "0.85", "asset_id": "t"}
    c = _client_with(order, [])
    filled, usdc, avg, status, src = asyncio.run(
        c._fill_from_order_api(oid, token_id="t", side="BUY", attempts=2, interval_s=0.0))
    assert filled == 0.0 and usdc == 0.0 and status == "unmatched"
