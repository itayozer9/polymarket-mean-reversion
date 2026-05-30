"""Realistic fill + cost simulator (v2) — pure functions on ladder arrays.

Replaces the assumptions that produced prior artifacts (fill-at-quoted-best,
optimistic costs). Everything here is a pure function of explicit inputs (book
ladders, trade lists) so it is trivially unit-testable and the Phase 0d null
test can hammer it with no I/O.

Cost truth (authoritative, from the live market metadata — see
docs/research/phase0a_settlement_feed.md):
  - Taker fee = rate * p * (1-p) * shares, rate = 0.07, exponent 1, TAKERS ONLY.
  - Makers pay 0 fee and earn a rebate = rebate_rate * (taker fee on the matched
    volume), rebate_rate = 0.20.
  - Price grid = 1c ticks; min order $5.
  - Settlement: winning shares redeem at $1 (no fee); losing shares -> $0.
    Hold-to-resolution therefore pays only the ENTRY cost (one-way), not a
    round trip.

All prices are YES-equivalent in [0,1]. A "buy side=yes" consumes the YES ask
ladder; "buy side=no" is equivalent to selling YES — callers pass the relevant
ladder. We keep the API side-agnostic: pass the ask ladder you are lifting or
the bid ladder you are hitting.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence, Optional
import numpy as np

TICK = 0.01
MIN_ORDER_USD = 5.0


@dataclass(frozen=True)
class FeeSchedule:
    rate: float = 0.07
    exponent: int = 1          # fee = rate * (p*(1-p))**exponent * shares
    rebate_rate: float = 0.20  # maker rebate = rebate_rate * taker fee on matched vol

    def taker_fee(self, shares: float, price: float) -> float:
        p = min(max(price, 0.0), 1.0)
        return self.rate * (p * (1.0 - p)) ** self.exponent * shares

    def maker_rebate(self, shares: float, price: float) -> float:
        # rebate is a fraction of the taker fee that WOULD have applied
        return self.rebate_rate * self.taker_fee(shares, price)


DEFAULT_FEES = FeeSchedule()


@dataclass(frozen=True)
class Fill:
    filled: bool
    shares: float
    avg_price: float       # USD per share actually paid/received
    notional_usd: float    # shares * avg_price
    fee_usd: float
    levels_used: int
    unfilled_usd: float    # stake that could not be filled (book too thin)
    slippage_vs_best: float  # avg_price - best price (signed; >0 worse for a buy)


def _clean_ladder(px: Sequence[float], sz: Sequence[float]):
    px = np.asarray(px, dtype="f8"); sz = np.asarray(sz, dtype="f8")
    m = np.isfinite(px) & np.isfinite(sz) & (px > 0) & (sz > 0)
    return px[m], sz[m]


def walk_buy(ask_px: Sequence[float], ask_sz: Sequence[float], stake_usd: float,
             fees: FeeSchedule = DEFAULT_FEES) -> Fill:
    """Lift the ask ladder to deploy `stake_usd`. Walks levels worst-case in
    listed order (assumed best->worst). Partial fill if the ladder is too thin."""
    px, sz = _clean_ladder(ask_px, ask_sz)
    if px.size == 0 or stake_usd <= 0:
        return Fill(False, 0.0, 0.0, 0.0, 0.0, 0, stake_usd, 0.0)
    best = float(px[0])
    remaining = float(stake_usd)
    shares = 0.0
    used = 0
    for p, s in zip(px, sz):
        if remaining <= 1e-12:
            break
        cost_full = p * s
        if remaining >= cost_full:
            shares += s; remaining -= cost_full
        else:
            shares += remaining / p; remaining = 0.0
        used += 1
    filled_usd = stake_usd - remaining
    avg = filled_usd / shares if shares > 0 else 0.0
    fee = fees.taker_fee(shares, avg)
    return Fill(shares > 0, shares, avg, filled_usd, fee, used, remaining, avg - best)


def walk_sell(bid_px: Sequence[float], bid_sz: Sequence[float], shares: float,
              fees: FeeSchedule = DEFAULT_FEES) -> Fill:
    """Hit the bid ladder to sell `shares`. Partial if the bids are too thin."""
    px, sz = _clean_ladder(bid_px, bid_sz)
    if px.size == 0 or shares <= 0:
        return Fill(False, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0)
    best = float(px[0])
    remaining = float(shares)
    proceeds = 0.0
    sold = 0.0
    used = 0
    for p, s in zip(px, sz):
        if remaining <= 1e-12:
            break
        take = min(remaining, s)
        proceeds += take * p; sold += take; remaining -= take
        used += 1
    avg = proceeds / sold if sold > 0 else 0.0
    fee = fees.taker_fee(sold, avg)
    unfilled_shares = remaining
    return Fill(sold > 0, sold, avg, proceeds, fee, used,
                unfilled_shares, best - avg)


def settle_pnl(entry: Fill, won: bool) -> float:
    """PnL of a hold-to-resolution taker position. Winning shares redeem at $1
    (no fee); losers -> 0. Cost paid = entry notional + entry fee (one-way)."""
    payoff = entry.shares * (1.0 if won else 0.0)
    return payoff - entry.notional_usd - entry.fee_usd


def taker_roundtrip_pnl(entry: Fill, exit_: Fill, won_if_unfilled_exit: Optional[bool] = None) -> float:
    """PnL if you enter and later exit as a taker (both legs cross). If the exit
    did not fill and you must hold to resolution, pass won_if_unfilled_exit."""
    if exit_.filled:
        return exit_.notional_usd - exit_.fee_usd - entry.notional_usd - entry.fee_usd
    if won_if_unfilled_exit is None:
        raise ValueError("exit unfilled and no resolution outcome provided")
    return settle_pnl(entry, won_if_unfilled_exit)


# --------------------------------------------------------------------------
# Maker fills — driven by the real trade tape (adverse-selection by construction)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MakerFill:
    filled: bool
    shares: float
    price: float           # the resting limit price
    notional_usd: float
    rebate_usd: float
    fill_sec: Optional[int]  # seconds_into_window at which it filled (None if never)


def maker_buy_fill(resting_price: float, stake_usd: float,
                   later_trades: Sequence[dict],
                   queue_ahead_usd: float = 0.0,
                   fees: FeeSchedule = DEFAULT_FEES) -> MakerFill:
    """A resting BUY limit at `resting_price` (YES-equivalent). It fills only
    when subsequent taker SELLs trade at a YES-equivalent price <= resting_price
    (someone sold into our bid). We must first absorb `queue_ahead_usd` of volume
    ahead of us in the FIFO queue. Adverse-selected by construction: we are
    filled precisely when the side is being sold down.

    later_trades: list of dicts with keys yes_px (YES-equivalent exec price),
    usd (notional), sec (seconds_into_window), side ('SELL' fills a resting buy).
    Returns the (possibly partial) fill, capped at our stake.
    """
    want_shares = stake_usd / resting_price if resting_price > 0 else 0.0
    if want_shares <= 0:
        return MakerFill(False, 0.0, resting_price, 0.0, 0.0, None)
    queue = float(queue_ahead_usd)
    got_shares = 0.0
    fill_sec = None
    for t in later_trades:
        if str(t.get("side", "")).upper() != "SELL":
            continue
        if float(t.get("yes_px", 1.0)) > resting_price + 1e-9:
            continue  # sold above our bid -> doesn't reach us
        vol = float(t.get("usd", 0.0))
        if queue > 0:
            absorbed = min(queue, vol); queue -= absorbed; vol -= absorbed
            if vol <= 0:
                continue
        # remaining volume fills us at our price
        fill_usd = min(vol, (want_shares - got_shares) * resting_price)
        if fill_usd <= 0:
            continue
        got_shares += fill_usd / resting_price
        if fill_sec is None:
            fill_sec = int(t.get("sec", 0))
        if got_shares >= want_shares - 1e-9:
            break
    notional = got_shares * resting_price
    rebate = fees.maker_rebate(got_shares, resting_price)
    return MakerFill(got_shares > 0, got_shares, resting_price, notional, rebate, fill_sec)
