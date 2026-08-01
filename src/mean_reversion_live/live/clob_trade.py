"""Polymarket CLOB V2 trading wrapper around py_clob_client_v2.

V2 (April 28, 2026) replaced V1 wholesale. Major changes:
- New CTF Exchange V2 at 0xE111180000d2663C0091e4f400237545B87B996B
- New Neg Risk CTF Exchange at 0xe2222d279d744050d28e00520010520000310F59
- EIP-712 domain version "2" for orders, "1" for auth
- New order schema (timestamp/metadata/builder, no nonce/feeRateBps/taker/expiration)
- pUSD as collateral (wraps USDC via Onramp 0x93070a847efEf7F70739046A929D47a521F5B8ee)
- Fees calculated at match time, charged in USDC

This wrapper:
- Inits ClobClient with proxy-wallet auth (signature_type=2, funder=proxy)
- Wraps sync py_clob_client_v2 calls in asyncio.to_thread
- Handles per-order neg_risk + tick_size
- Provides IOC (FAK) limit-order placement
- NEVER logs the private key
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional

import structlog

# V2 SDK imports — must be installed via: uv add py-clob-client-v2
from py_clob_client_v2 import (
    ClobClient,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
)
from py_clob_client_v2.clob_types import (
    AssetType,
    BalanceAllowanceParams,
    OpenOrderParams,
    TradeParams,
)
from py_clob_client_v2.order_builder.constants import BUY, SELL

log = structlog.get_logger()


def _f(x) -> float:
    """Parse a CLOB REST value (often a string) into a float; 0.0 on failure."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _trade_refs_order(trade: dict, order_id: str) -> bool:
    """True if a CLOB trade record references our order as taker or maker."""
    if not isinstance(trade, dict) or not order_id:
        return False
    if str(trade.get("taker_order_id") or trade.get("takerOrderId") or "") == order_id:
        return True
    for mo in (trade.get("maker_orders") or trade.get("makerOrders") or []):
        if isinstance(mo, dict) and str(mo.get("order_id") or mo.get("orderID") or "") == order_id:
            return True
    return False

def clamp_buy_fallback(
    tok_delta: float, pusd_delta: float, limit_price: float,
) -> tuple[float, float, bool]:
    """Sanitize a BUY fill measured via wallet balance deltas (the shared-wallet
    fallback path). A CLOB IOC can never cost more than shares x its limit price,
    so any pUSD-delta excess over that bound is pollution (concurrent wallet
    movement / balance-API lag), not money paid for THIS order — unclamped it
    inflates usdc_paid/avg_price and can record a fill "above" the strategy's
    max_ask cap that never happened (4 such phantom cap breaches by 2026-07-06).
    Returns (filled_shares, usdc_paid, clamped)."""
    shares = max(0.0, tok_delta)
    usdc = max(0.0, -pusd_delta)
    bound = shares * limit_price
    if usdc > bound + 1e-9:
        return shares, bound, True
    return shares, usdc, False


DEFAULT_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137


def quantize_size_for_buy(price: float, size: float) -> float:
    """Adjust size DOWN so price × size has at most 2 decimal places.

    V2 BUY orders: maker amount (USDC notional = price × size) is rejected
    by the CLOB if it has more than 2 decimals (e.g., 0.382 × 172 = 65.704
    fails — "max accuracy of 2 decimals"). Taker amount (shares) tolerates
    up to 5 decimals.

    Strategy: find the smallest integer ``step`` such that ``price × step``
    has ≤2 decimals (e.g. for 0.382: step=5 since 0.382×5=1.91 is clean).
    Then floor ``size`` to a multiple of ``step``. Both the floored size and
    the resulting notional are guaranteed compatible with the API.
    """
    if size <= 0 or price <= 0:
        return 0.0
    p = Decimal(str(price))
    s = Decimal(str(size))

    # Find smallest step ∈ [1, 100] with (p × step) cleanly 2dp. The cap
    # of 100 handles any realistic 3-decimal price tick.
    target_precision = Decimal("0.01")
    step = 1
    for candidate in range(1, 101):
        product = p * Decimal(candidate)
        if product == product.quantize(target_precision):
            step = candidate
            break
    else:
        # No clean step ≤100 found — fall back to 100 and hope the price was
        # malformed. Order will likely still be rejected, but we did our best.
        step = 100

    step_dec = Decimal(step)
    floored = (s // step_dec) * step_dec
    if floored <= 0:
        return 0.0
    return float(floored)


@dataclass
class FillResult:
    success: bool
    order_id: Optional[str]
    status: str                       # "matched" | "live" | "rejected" | "error"
    making_amount: float = 0.0
    taking_amount: float = 0.0
    error: Optional[str] = None
    raw: Optional[dict] = None


@dataclass
class OpenOrder:
    order_id: str
    token_id: str
    side: str                         # BUY / SELL
    price: float
    size: float
    filled: float
    market: str                       # condition_id


@dataclass
class AggregatedFill:
    """Result of fill_or_chase: aggregate of one or more IOC attempts at
    increasing limit prices to fill thin books."""
    success: bool
    total_shares: float = 0.0
    total_usdc: float = 0.0        # paid (BUY) or received (SELL), always positive
    avg_price: float = 0.0          # USDC per share, volume-weighted
    attempts: int = 0
    fills: list[FillResult] = None  # type: ignore[assignment]
    last_limit_price: float = 0.0
    stopped_reason: Optional[str] = None  # why we stopped chasing


class ClobTradeClient:
    """Thin wrapper around py_clob_client_v2 for live trading."""

    def __init__(
        self,
        *,
        private_key: str,
        proxy_address: str,
        host: str = DEFAULT_HOST,
        chain_id: int = POLYGON_CHAIN_ID,
        signature_type: int = 2,
    ):
        # Normalize private key (accept with or without 0x prefix)
        key = private_key.strip()
        if not key.startswith("0x"):
            key = "0x" + key

        self._proxy = proxy_address
        self._signature_type = signature_type

        # V2 init: two-phase. First a temp client to derive API creds, then
        # a final client with the creds attached.
        temp = ClobClient(host, key=key, chain_id=chain_id)
        try:
            api_creds = temp.create_or_derive_api_key()
        except AttributeError:
            # Older method name
            api_creds = temp.create_or_derive_api_creds()

        self._client = ClobClient(
            host,
            key=key,
            chain_id=chain_id,
            creds=api_creds,
            signature_type=signature_type,
            funder=proxy_address,
        )

        # Tick-size and neg-risk caches (per token_id)
        self._tick_size_cache: dict[str, str] = {}
        self._neg_risk_cache: dict[str, bool] = {}

    # ----- Identity assertions ------------------------------------------

    async def assert_signature_identity(self, expected_eoa: str) -> None:
        signer = await asyncio.to_thread(self._client.get_address)
        if signer.lower() != expected_eoa.lower():
            raise RuntimeError(
                f"Signer identity mismatch. Expected EOA {expected_eoa}, "
                f"got {signer}. Check signature_type and POLYMARKET_PRIVATE_KEY."
            )
        log.info("clob_signer_verified", signer=signer, funder=self._proxy)

    @property
    def proxy_address(self) -> str:
        return self._proxy

    # ----- Market info (cached) -----------------------------------------

    async def get_tick_size(self, token_id: str) -> str:
        if token_id in self._tick_size_cache:
            return self._tick_size_cache[token_id]
        ts = await asyncio.to_thread(self._client.get_tick_size, token_id)
        self._tick_size_cache[token_id] = str(ts)
        return str(ts)

    async def get_neg_risk(self, token_id: str) -> bool:
        if token_id in self._neg_risk_cache:
            return self._neg_risk_cache[token_id]
        nr = await asyncio.to_thread(self._client.get_neg_risk, token_id)
        self._neg_risk_cache[token_id] = bool(nr)
        return bool(nr)

    async def get_midpoint(self, token_id: str) -> Optional[float]:
        try:
            r = await asyncio.to_thread(self._client.get_midpoint, token_id)
            return float(r.get("mid")) if r and r.get("mid") is not None else None
        except Exception as e:
            log.warning("clob_get_midpoint_failed", token_id=token_id, error=str(e))
            return None

    async def get_book(self, token_id: str) -> Optional[dict]:
        try:
            return await asyncio.to_thread(self._client.get_order_book, token_id)
        except Exception as e:
            log.warning("clob_get_book_failed", token_id=token_id, error=str(e))
            return None

    # ----- Balance ------------------------------------------------------

    async def get_collateral_balance(self) -> float:
        """Returns proxy's pUSD balance in dollars (V2 collateral)."""
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=self._signature_type,
        )
        r = await asyncio.to_thread(self._client.get_balance_allowance, params)
        bal_micro = float(r.get("balance", 0))
        return bal_micro / 1e6

    async def get_collateral_allowance(self) -> dict:
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=self._signature_type,
        )
        return await asyncio.to_thread(self._client.get_balance_allowance, params)

    async def get_token_balance(self, token_id: str) -> float:
        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL,
            token_id=token_id,
            signature_type=self._signature_type,
        )
        r = await asyncio.to_thread(self._client.get_balance_allowance, params)
        return float(r.get("balance", 0)) / 1e6

    # ----- Order placement ----------------------------------------------

    async def _balance_for(self, token_id: str) -> tuple[float, float]:
        """Returns (token_balance_shares, pusd_balance_usd) for the proxy."""
        tok = await self.get_token_balance(token_id)
        pusd = await self.get_collateral_balance()
        return tok, pusd

    async def _fill_from_order_api(
        self, order_id: str, token_id: str, side: str,
        attempts: int = 12, interval_s: float = 0.2,
    ) -> Optional[tuple]:
        """Authoritative PER-ORDER fill detection.

        Polls ``get_order(order_id)`` for ``size_matched`` (filled shares, as recorded
        by the matching engine) and derives the average fill price from THIS order's
        trades. Because it keys on the order — not wallet balances — it is immune to
        the shared-wallet pUSD noise and balance-API lag that corrupted balance-delta
        detection (the eth probe fill reported 46 shares / avg 0.233 for a 13-share,
        ~0.74 order). FAK orders are terminal almost immediately, so this returns in
        well under a second in the common case (no fixed multi-second sleep).

        Returns (filled_shares, usdc_paid, avg_price, status, price_source) or None.
        """
        TERMINAL = ("matched", "filled", "complete", "cancelled", "canceled",
                    "killed", "unmatched")
        order = None
        for _ in range(max(1, attempts)):
            try:
                order = await asyncio.to_thread(self._client.get_order, order_id)
            except Exception as e:
                log.debug("get_order_retry", order_id=order_id, err=str(e))
            if order:
                status = str(order.get("status") or order.get("state") or "").lower()
                matched = _f(order.get("size_matched") or order.get("sizeMatched")
                             or order.get("matched_size"))
                if matched > 0 or status in TERMINAL:
                    break
            await asyncio.sleep(interval_s)
        if not order:
            return None
        status = str(order.get("status") or order.get("state") or "unknown").lower()
        filled = _f(order.get("size_matched") or order.get("sizeMatched")
                    or order.get("matched_size"))
        # Average fill price from THIS order's realized trades (exact). Best-effort:
        # pull the asset's recent trades and keep only those referencing our order.
        avg, src = 0.0, "limit"
        try:
            asset_id = str(order.get("asset_id") or order.get("assetId") or token_id)
            trades = await asyncio.to_thread(
                self._client.get_trades, TradeParams(asset_id=asset_id))
            num = den = 0.0
            for t in (trades or []):
                if not _trade_refs_order(t, order_id):
                    continue
                sz, px = _f(t.get("size")), _f(t.get("price"))
                if sz > 0 and px > 0:
                    num += sz * px
                    den += sz
            if den > 0:
                avg, src = num / den, "trades"
                if filled <= 0:
                    filled = den
        except Exception as e:
            log.debug("get_trades_failed", order_id=order_id, err=str(e))
        if avg <= 0:
            avg = _f(order.get("price"))   # conservative: the (limit) price, an upper bound
            src = "limit"
        usdc = filled * avg
        return (filled, usdc, avg, status, src)

    async def place_ioc(
        self,
        *,
        token_id: str,
        side: str,                    # "BUY" | "SELL"
        price: float,
        size: float,
        neg_risk: Optional[bool] = None,
        tick_size: Optional[str] = None,
        settlement_wait_seconds: float = 10.0,
    ) -> FillResult:
        """Place a marketable IOC limit order (FAK = Fill-and-Kill).

        V2 quirk: the API returns `status="delayed"` with making/taking=0
        synchronously, even for orders that DO fill on-chain. We snapshot the
        proxy's balance before posting, then poll after to detect the actual
        fill quantities.
        """
        side_const = BUY if side == "BUY" else SELL if side == "SELL" else None
        if side_const is None:
            return FillResult(success=False, order_id=None, status="rejected",
                              error=f"invalid side: {side}")

        if neg_risk is None:
            neg_risk = await self.get_neg_risk(token_id)
        if tick_size is None:
            tick_size = await self.get_tick_size(token_id)

        # Round price to tick_size
        ts_float = float(tick_size)
        rounded = round(round(price / ts_float) * ts_float, 4)

        # V2 precision constraint:
        #   BUY  → maker amount (USDC notional = price × size) must be ≤2 dp
        #   SELL → maker amount (shares = size) must be ≤2 dp
        # For BUY, integer-flooring size is NOT sufficient when price has
        # 3 decimals (e.g. 0.382 × 172 = 65.704 → rejected). Use Decimal
        # notional quantization instead.
        if side == "BUY":
            size = quantize_size_for_buy(rounded, size)
        else:
            # SELL: floor to integer (shares ≤2 dp); kept simple
            if size != int(size):
                size = float(int(size))

        # Snapshot balances BEFORE placing — used to detect actual fill if
        # V2 returns status="delayed" with zero making/taking synchronously.
        tok_before, pusd_before = await self._balance_for(token_id)

        order_args = OrderArgs(
            token_id=token_id,
            price=rounded,
            size=size,
            side=side_const,
        )
        opts = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

        try:
            resp = await asyncio.to_thread(
                self._client.create_and_post_order,
                order_args,
                options=opts,
                order_type=OrderType.FAK,
            )
        except TypeError:
            # Some V2 builds use positional args
            try:
                resp = await asyncio.to_thread(
                    self._client.create_and_post_order,
                    order_args, opts, OrderType.FAK,
                )
            except Exception as e:
                log.error("clob_place_failed", token_id=token_id, side=side,
                          price=rounded, size=size, error=str(e))
                return FillResult(success=False, order_id=None, status="error", error=str(e))
        except Exception as e:
            log.error("clob_place_failed", token_id=token_id, side=side,
                      price=rounded, size=size, error=str(e))
            return FillResult(success=False, order_id=None, status="error", error=str(e))

        if not resp:
            return FillResult(success=False, order_id=None, status="rejected",
                              error="empty response")

        order_id = resp.get("orderID") or resp.get("order_id") or resp.get("id")
        status = resp.get("status", "unknown")

        # Fill detection. PRIMARY: per-order via get_order/get_trades (size_matched +
        # realized trade prices) — immune to the shared-wallet pUSD noise and
        # balance-API lag that corrupted balance deltas (the eth probe fill reported
        # 46 shares / avg 0.233 for a 13-share / ~0.74 order). FALLBACK: balance
        # deltas (legacy), only if the order API can't be read, and flagged as such.
        making = 0.0
        taking = 0.0
        fill_source = "none"
        if order_id and status not in ("rejected", "error"):
            res = None
            if side == "BUY":
                try:
                    res = await self._fill_from_order_api(order_id, token_id, side)
                except Exception as e:
                    log.warning("order_api_fill_failed", order_id=order_id, err=str(e))
            if res is not None:
                filled_sh, usdc_amt, avg_px, ostatus, price_src = res
                making, taking = filled_sh, usdc_amt
                fill_source = f"order_api:{price_src}"
                if ostatus and ostatus != "unknown":
                    status = ostatus
                try:    # cross-check vs balance deltas (logged only, NOT authoritative)
                    tok_after, pusd_after = await self._balance_for(token_id)
                    xb_sh, xb_usd = round(tok_after - tok_before, 3), round(pusd_before - pusd_after, 4)
                except Exception:
                    xb_sh, xb_usd = None, None
                log.info("clob_fill_via_order_api", order_id=order_id, token_id=token_id,
                         filled_shares=round(filled_sh, 3), usdc=round(usdc_amt, 4),
                         avg_price=round(avg_px, 4), price_src=price_src, status=status,
                         xcheck_bal_shares=xb_sh, xcheck_bal_usdc=xb_usd)
            else:
                # Fallback: balance deltas (shared-wallet-noisy; flagged loudly).
                await asyncio.sleep(settlement_wait_seconds)
                tok_after, pusd_after = await self._balance_for(token_id)
                tok_delta = tok_after - tok_before
                pusd_delta = pusd_after - pusd_before
                if side == "BUY":
                    # shares + usdc, clamped to the cost <= shares x limit bound
                    # (excess pUSD delta = shared-wallet pollution, not our cost)
                    making, taking, clamped = clamp_buy_fallback(
                        tok_delta, pusd_delta, rounded)
                else:  # SELL
                    taking = max(0.0, -tok_delta)      # shares delivered
                    making = max(0.0, pusd_delta)      # usdc received
                    clamped = False
                fill_source = "balance_fallback"
                log.warning("clob_fill_via_balance_fallback", token_id=token_id, side=side,
                            tok_delta=tok_delta, pusd_delta=pusd_delta,
                            making=making, taking=taking, clamped=clamped,
                            limit_price=rounded)

        success = bool(order_id) and status not in ("rejected", "error")

        log.info(
            "clob_placed",
            token_id=token_id,
            side=side,
            price=rounded,
            size=size,
            neg_risk=neg_risk,
            order_id=order_id,
            status=status,
            making=making,
            taking=taking,
            fill_source=fill_source,
        )
        return FillResult(
            success=success,
            order_id=order_id,
            status=status,
            making_amount=making,
            taking_amount=taking,
            error=resp.get("errorMsg"),
            raw=resp,
        )

    async def fill_or_chase(
        self,
        *,
        token_id: str,
        side: str,
        target_price: float,            # the original limit (already 1-tick-slipped by caller)
        target_size: float,             # total shares we want
        neg_risk: Optional[bool] = None,
        tick_size: Optional[str] = None,
        max_chase_ticks: int = 3,       # walk up to N additional ticks above target_price
        price_ceiling: Optional[float] = None,
        min_notional_usd: float = 1.0,  # V2 minimum order is $1 USDC notional
    ) -> AggregatedFill:
        """Place one IOC at target_price. If partially filled, walk the limit
        up one tick at a time (up to max_chase_ticks) to fill the remainder.

        Stops chasing when:
        - All shares are filled
        - The next tick would exceed price_ceiling (defaults to no cap)
        - Remaining notional drops below min_notional_usd ($1)
        - An IOC errors or stays unfilled at the current tick (book is dry above)
        - max_chase_ticks reached

        Returns an AggregatedFill with weighted-avg price and totals.
        """
        if neg_risk is None:
            neg_risk = await self.get_neg_risk(token_id)
        if tick_size is None:
            tick_size = await self.get_tick_size(token_id)
        ts_float = float(tick_size)

        fills: list[FillResult] = []
        remaining = float(target_size)
        limit_price = float(target_price)
        stopped: Optional[str] = None

        for step in range(max_chase_ticks + 1):
            # V2 notional constraint:
            #   BUY  → use Decimal quantization on (limit_price × size)
            #   SELL → integer-floor size
            if side == "BUY":
                size_to_send = quantize_size_for_buy(limit_price, remaining)
            else:
                size_to_send = float(int(remaining))
            if size_to_send * limit_price < min_notional_usd:
                stopped = f"remaining_notional {size_to_send * limit_price:.2f} < min ${min_notional_usd}"
                break

            log.info("chase_attempt",
                     step=step,
                     token_id=token_id[:16] + "...",
                     side=side,
                     limit=round(limit_price, 4),
                     size=size_to_send,
                     remaining_before=remaining)

            fill = await self.place_ioc(
                token_id=token_id,
                side=side,
                price=limit_price,
                size=size_to_send,
                neg_risk=neg_risk,
                tick_size=tick_size,
            )
            fills.append(fill)

            if not fill.success:
                stopped = f"IOC error: {fill.error or fill.status}"
                break

            # For BUY: making=shares received, taking=USDC paid (via balance polling)
            # For SELL: taking=shares delivered, making=USDC received
            if side == "BUY":
                filled_shares = fill.making_amount
            else:
                filled_shares = fill.taking_amount

            remaining -= filled_shares

            if filled_shares <= 0:
                # No fill at this tick — book exhausted; one more tick up won't help
                # unless we're chasing into a thicker layer. Try one more step.
                # But if the previous step also had zero, stop.
                if step > 0 and len(fills) >= 2 and (
                    (fills[-2].making_amount if side == "BUY" else fills[-2].taking_amount) <= 0
                ):
                    stopped = "consecutive zero-fill chases — book empty above target"
                    break

            if remaining <= 0.5:
                stopped = "fully filled"
                break

            # Advance to next tick (chase up for BUY, down for SELL)
            next_price = limit_price + (ts_float if side == "BUY" else -ts_float)
            if price_ceiling is not None:
                if side == "BUY" and next_price > price_ceiling + 1e-9:
                    stopped = f"next tick {next_price:.4f} > ceiling {price_ceiling}"
                    break
                if side == "SELL" and next_price < price_ceiling - 1e-9:
                    stopped = f"next tick {next_price:.4f} < floor {price_ceiling}"
                    break
            limit_price = round(next_price, 4)
        else:
            stopped = f"max_chase_ticks ({max_chase_ticks}) reached"

        # Aggregate
        if side == "BUY":
            total_shares = sum(f.making_amount for f in fills)
            total_usdc = sum(f.taking_amount for f in fills)
        else:
            total_shares = sum(f.taking_amount for f in fills)
            total_usdc = sum(f.making_amount for f in fills)
        avg_price = (total_usdc / total_shares) if total_shares > 0 else 0.0
        success = total_shares > 0.0

        log.info("chase_done",
                 token_id=token_id[:16] + "...", side=side,
                 attempts=len(fills),
                 target_size=target_size,
                 total_shares=total_shares,
                 total_usdc=round(total_usdc, 4),
                 avg_price=round(avg_price, 4),
                 fill_ratio=round(total_shares / max(target_size, 1e-9), 3),
                 stopped=stopped)

        return AggregatedFill(
            success=success,
            total_shares=total_shares,
            total_usdc=total_usdc,
            avg_price=avg_price,
            attempts=len(fills),
            fills=fills,
            last_limit_price=limit_price,
            stopped_reason=stopped,
        )

    async def cancel(self, order_id: str) -> bool:
        try:
            # V2: cancel_order(order_id) instead of V1's cancel(order_id)
            r = await asyncio.to_thread(self._client.cancel_order, order_id)
            log.info("clob_cancelled", order_id=order_id, response=r)
            return True
        except Exception as e:
            log.warning("clob_cancel_failed", order_id=order_id, error=str(e))
            return False

    async def cancel_all(self) -> bool:
        try:
            # V2 may not expose cancel_all (cancel_all was V1). Use cancel_orders([]) which clears all.
            if hasattr(self._client, "cancel_all"):
                r = await asyncio.to_thread(self._client.cancel_all)
            elif hasattr(self._client, "cancel_orders"):
                # cancel_orders takes a list of order_ids — fetch open then cancel each
                open_orders = await self.get_open_orders()
                if open_orders:
                    ids = [o.order_id for o in open_orders]
                    r = await asyncio.to_thread(self._client.cancel_orders, ids)
                else:
                    r = {"canceled": [], "not_canceled": {}}
            else:
                r = {"error": "no cancel_all or cancel_orders available"}
            log.info("clob_cancel_all", response=r)
            return True
        except Exception as e:
            log.warning("clob_cancel_all_failed", error=str(e))
            return False

    # ----- Open orders --------------------------------------------------

    async def get_open_orders(self, *, asset_id: Optional[str] = None) -> list[OpenOrder]:
        params = OpenOrderParams(asset_id=asset_id) if asset_id else None
        try:
            # V2 renamed get_orders → get_open_orders
            raw = await asyncio.to_thread(self._client.get_open_orders, params)
        except Exception as e:
            log.warning("clob_get_open_orders_failed", error=str(e))
            return []
        out = []
        for o in raw or []:
            out.append(OpenOrder(
                order_id=o.get("id", ""),
                token_id=str(o.get("asset_id", "")),
                side=o.get("side", ""),
                price=float(o.get("price", 0) or 0),
                size=float(o.get("original_size", o.get("size", 0)) or 0),
                filled=float(o.get("size_matched", 0) or 0),
                market=o.get("market", ""),
            ))
        return out
