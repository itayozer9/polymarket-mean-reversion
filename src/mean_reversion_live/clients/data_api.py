"""Polymarket data-api client — public, no auth.

Wraps `https://data-api.polymarket.com`, the public read-only API that backs
the Polymarket UI's leaderboard and per-wallet history. Used to discover
profitable traders (leaderboard) and pull their full trade history (activity)
and current open positions.

Endpoints (verified live 2026-05-22):
  - GET /v1/leaderboard   — versioned; ranked traders by category/period
  - GET /activity         — unversioned; per-wallet event history, newest first
  - GET /positions        — unversioned; per-wallet current open positions

This module is PURE network code: it fetches and parses, but does not cache to
disk (a later task handles caching). All timestamps from the API are unix
seconds (UTC).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional

import aiohttp
import structlog

from mean_reversion_live.clients._session import get_json
from mean_reversion_live.config import get_settings

log = structlog.get_logger(__name__)


@dataclass
class LeaderboardEntry:
    """One ranked trader from /v1/leaderboard.

    The on-wallet address used everywhere else in the data-api is
    ``proxyWallet`` — that's what ``proxy_wallet`` holds.
    """
    rank: int
    proxy_wallet: str
    user_name: str
    vol: float
    pnl: float


@dataclass
class ActivityRecord:
    """One event from /activity (a TRADE, MERGE, REDEEM, ...).

    Non-TRADE event types (MERGE/REDEEM/...) carry an empty ``side``.
    ``timestamp`` is unix seconds (UTC). ``slug`` is the market slug.
    """
    timestamp: int
    type: str
    size: float
    usdc_size: float
    transaction_hash: str
    price: float
    side: str
    title: str
    outcome: str
    slug: str = ""


def _as_int(value, default: int = 0) -> int:
    """Coerce an API value to int. The leaderboard returns ``rank`` as a
    string, so we cannot rely on the JSON type."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_leaderboard_entry(doc: dict) -> LeaderboardEntry:
    return LeaderboardEntry(
        rank=_as_int(doc.get("rank")),
        proxy_wallet=str(doc.get("proxyWallet") or ""),
        user_name=str(doc.get("userName") or ""),
        vol=_as_float(doc.get("vol")),
        pnl=_as_float(doc.get("pnl")),
    )


def _parse_activity_record(doc: dict) -> ActivityRecord:
    return ActivityRecord(
        timestamp=_as_int(doc.get("timestamp")),
        type=str(doc.get("type") or ""),
        size=_as_float(doc.get("size")),
        usdc_size=_as_float(doc.get("usdcSize")),
        transaction_hash=str(doc.get("transactionHash") or ""),
        price=_as_float(doc.get("price")),
        side=str(doc.get("side") or ""),
        title=str(doc.get("title") or ""),
        outcome=str(doc.get("outcome") or ""),
        slug=str(doc.get("slug") or ""),
    )


async def fetch_leaderboard(
    session: aiohttp.ClientSession,
    *,
    category: str = "CRYPTO",
    time_period: str = "MONTH",
    order_by: str = "PNL",
    page_size: int = 50,
    max_entries: int = 100,
) -> List[LeaderboardEntry]:
    """Fetch the ranked leaderboard, paginating until ``max_entries`` is hit.

    Args:
        category:    OVERALL | POLITICS | SPORTS | CRYPTO | ...
        time_period: DAY | WEEK | MONTH | ALL
        order_by:    PNL | VOL
        page_size:   per-request limit (data-api caps this at 50)
        max_entries: stop once this many entries have been collected

    Pagination advances ``offset`` by ``page_size``; it stops early on an
    empty page or a page shorter than ``page_size`` (end of data).
    """
    base = get_settings().data_api_base_url
    page_size = max(1, min(page_size, 50))
    out: List[LeaderboardEntry] = []
    offset = 0
    while len(out) < max_entries:
        limit = min(page_size, max_entries - len(out))
        params = {
            "category": category,
            "timePeriod": time_period,
            "orderBy": order_by,
            "limit": str(limit),
            "offset": str(offset),
        }
        data = await get_json(session, f"{base}/v1/leaderboard", params=params)
        if not isinstance(data, list) or not data:
            break
        out.extend(_parse_leaderboard_entry(d) for d in data)
        log.info(
            "leaderboard.page",
            category=category,
            time_period=time_period,
            offset=offset,
            got=len(data),
            total=len(out),
        )
        if len(data) < limit:
            break
        offset += page_size
    return out[:max_entries]


async def fetch_activity(
    session: aiohttp.ClientSession,
    wallet: str,
    *,
    page_size: int = 500,
    max_records: Optional[int] = None,
) -> List[ActivityRecord]:
    """Fetch a wallet's activity history (newest first), paginating fully.

    Args:
        wallet:      the trader's proxyWallet address
        page_size:   per-request limit (data-api 400s above 500)
        max_records: stop once this many records are collected; None = all

    Pagination advances ``offset`` by ``page_size`` and stops on a page
    shorter than ``page_size`` (end of history) or when ``max_records`` is hit.

    DO NOT pass a small ``max_records`` when computing P&L. A losing market
    emits ONE activity record (the BUY); a winner emits two (BUY + REDEEM).
    Truncating the history therefore strands redeems whose buy fell outside the
    window, and any caller that pairs them reads those as pure profit — that is
    exactly how the status cross-check reported +$272 on a -$49 book (2026-07-24).
    """
    base = get_settings().data_api_base_url
    # ponytail: 500 is the endpoint's hard limit (>500 => HTTP 400), and offset
    # is capped at 3000 server-side, so ~3500 records is all that's reachable.
    page_size = max(1, min(page_size, 500))
    out: List[ActivityRecord] = []
    offset = 0
    while True:
        if max_records is not None:
            remaining = max_records - len(out)
            if remaining <= 0:
                break
            limit = min(page_size, remaining)
        else:
            limit = page_size
        params = {
            "user": wallet,
            "limit": str(limit),
            "offset": str(offset),
        }
        data = await get_json(session, f"{base}/activity", params=params)
        if not isinstance(data, list) or not data:
            break
        out.extend(_parse_activity_record(d) for d in data)
        log.info(
            "activity.page",
            wallet=wallet,
            offset=offset,
            got=len(data),
            total=len(out),
        )
        # A short page (vs. the page_size we asked for) means end of history.
        if len(data) < limit:
            break
        offset += page_size
        if offset > 3000:  # server rejects deeper offsets with HTTP 400
            log.warning("activity.offset_cap", wallet=wallet, total=len(out))
            break
    if max_records is not None:
        return out[:max_records]
    return out


async def fetch_positions(
    session: aiohttp.ClientSession,
    wallet: str,
) -> List[dict]:
    """Fetch a wallet's positions as raw dicts, paginating fully.

    Returns the raw API objects unmodified — callers downstream decide which
    fields they need.

    ``sizeThreshold=0`` is required: the endpoint defaults to 1.0 and silently
    drops sub-1-share partial fills, which are real (and often winning)
    positions. Without paging the default page is 100, so a wallet with more
    open markets loses the tail — and a caller that reads "no position record"
    as "unresolved" mislabels those markets.
    """
    base = get_settings().data_api_base_url
    out: List[dict] = []
    offset = 0
    while True:
        params = {"user": wallet, "limit": "500", "offset": str(offset), "sizeThreshold": "0"}
        data = await get_json(session, f"{base}/positions", params=params)
        if not isinstance(data, list):
            log.warning("positions.unexpected_payload", wallet=wallet, type=type(data).__name__)
            break
        out.extend(data)
        if len(data) < 500:
            break
        offset += 500
    log.info("positions.fetched", wallet=wallet, count=len(out))
    return out
