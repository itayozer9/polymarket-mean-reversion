"""Gamma API client — public, no auth.

Discovers active up/down crypto markets by polling /events.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import List, Optional

import aiohttp
import structlog

from mean_reversion_live.clients._session import get_json
from mean_reversion_live.config import get_settings

log = structlog.get_logger(__name__)


@dataclass
class MarketInfo:
    slug: str
    symbol: str           # "btc", "eth", "sol", "xrp"
    timeframe: str        # "5m" | "15m"
    yes_token_id: str
    no_token_id: str
    window_start_ts: int  # seconds since epoch
    window_end_ts: int
    start_price: float    # BTC price at window start (the "strike")

    @property
    def asset_ids(self) -> List[str]:
        return [self.yes_token_id, self.no_token_id]


def _parse_slug(slug: str) -> Optional[tuple]:
    """`btc-updown-15m-1773705600` → ("btc", "15m", 1773705600). Returns None if not a match."""
    parts = slug.split("-")
    if len(parts) < 4 or parts[1] != "updown":
        return None
    sym, _, tf, ts = parts[0], parts[1], parts[2], parts[3]
    if tf not in ("5m", "15m"):
        return None
    try:
        ts_int = int(ts)
    except ValueError:
        return None
    return sym, tf, ts_int


async def _market_by_slug(session: aiohttp.ClientSession, slug: str) -> Optional[dict]:
    """Look up a Polymarket market by its slug. Returns None if not found."""
    base = get_settings().gamma_base_url
    try:
        data = await get_json(session, f"{base}/markets", params={"slug": slug})
    except aiohttp.ClientResponseError:
        return None
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


def _parse_market_doc(slug: str, market_doc: dict) -> Optional["MarketInfo"]:
    parsed = _parse_slug(slug)
    if parsed is None:
        return None
    sym, tf, window_start_ts = parsed
    clob_token_ids_raw = market_doc.get("clobTokenIds")
    if not clob_token_ids_raw:
        return None
    try:
        token_ids = json.loads(clob_token_ids_raw) if isinstance(clob_token_ids_raw, str) else clob_token_ids_raw
    except json.JSONDecodeError:
        return None
    if not isinstance(token_ids, list) or len(token_ids) < 2:
        return None
    outcomes_raw = market_doc.get("outcomes")
    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
    except json.JSONDecodeError:
        outcomes = []
    yes_idx, no_idx = 0, 1
    for i, o in enumerate(outcomes or []):
        if str(o).lower() in ("up", "yes"):
            yes_idx = i
        elif str(o).lower() in ("down", "no"):
            no_idx = i
    duration = 300 if tf == "5m" else 900
    return MarketInfo(
        slug=slug,
        symbol=sym,
        timeframe=tf,
        yes_token_id=str(token_ids[yes_idx]),
        no_token_id=str(token_ids[no_idx]),
        window_start_ts=window_start_ts,
        window_end_ts=window_start_ts + duration,
        start_price=0.0,
    )


async def list_active_markets(
    session: aiohttp.ClientSession,
    symbols: List[str],
    timeframes: List[str] = ("15m", "5m"),
    limit: int = 100,
) -> List[MarketInfo]:
    """Find markets currently IN their observation window.

    Polymarket's `/events` endpoint sorts by startDate (when trading opened),
    which puts the long 24h-trading markets first; the markets whose 5m/15m
    PRICE OBSERVATION window is happening RIGHT NOW are buried deep or
    not returned at all. We probe `/markets?slug=...` directly with the
    expected slugs for the current observation window plus ±2 future slots.
    """
    import time
    now = int(time.time())
    out: List[MarketInfo] = []
    candidate_slugs: List[str] = []
    for tf in timeframes:
        dur = 300 if tf == "5m" else 900
        # Current observation window plus next 2 future slots (so we can
        # warm up our books before the new window opens).
        for k in (-1, 0, 1, 2):
            window_start_ts = ((now // dur) + k) * dur
            for sym in symbols:
                candidate_slugs.append(f"{sym}-updown-{tf}-{window_start_ts}")
    # Probe each candidate concurrently
    import asyncio
    results = await asyncio.gather(
        *[_market_by_slug(session, s) for s in candidate_slugs],
        return_exceptions=True,
    )
    for slug, result in zip(candidate_slugs, results):
        if isinstance(result, Exception) or result is None:
            continue
        if result.get("closed"):
            continue
        mi = _parse_market_doc(slug, result)
        if mi is not None:
            out.append(mi)
    return out


async def _list_active_markets_OLD(
    session: aiohttp.ClientSession,
    symbols: List[str],
    timeframes: List[str] = ("15m", "5m"),
    limit: int = 100,
) -> List[MarketInfo]:
    """Old impl kept for reference. Uses /events listing — misses near-now obs windows."""
    base = get_settings().gamma_base_url
    out: List[MarketInfo] = []
    params = {
        "closed": "false",
        "active": "true",
        "order": "startDate",
        "ascending": "false",
        "limit": str(limit),
    }
    data = await get_json(session, f"{base}/events", params=params)
    for ev in data or []:
        slug = ev.get("slug") or ""
        parsed = _parse_slug(slug)
        if parsed is None:
            continue
        sym, tf, window_start_ts = parsed
        if sym not in symbols or tf not in timeframes:
            continue
        # The event has 1 market with 2 outcomes (Up/Down). Pull token ids.
        markets = ev.get("markets") or []
        if not markets:
            continue
        market = markets[0]
        clob_token_ids_raw = market.get("clobTokenIds")
        if not clob_token_ids_raw:
            continue
        # clobTokenIds is JSON-encoded string of a list.
        try:
            token_ids = json.loads(clob_token_ids_raw) if isinstance(clob_token_ids_raw, str) else clob_token_ids_raw
        except json.JSONDecodeError:
            continue
        if not isinstance(token_ids, list) or len(token_ids) < 2:
            continue
        # outcomes order in Polymarket: ["Up", "Down"] or similar. The token IDs match the same index.
        outcomes_raw = market.get("outcomes")
        try:
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        except json.JSONDecodeError:
            outcomes = []
        yes_idx = 0
        no_idx = 1
        for i, o in enumerate(outcomes or []):
            if str(o).lower() in ("up", "yes"):
                yes_idx = i
            elif str(o).lower() in ("down", "no"):
                no_idx = i
        # Window math: a 15m slug's ts is the window start. End = start + duration.
        duration = 300 if tf == "5m" else 900
        window_end_ts = window_start_ts + duration
        # start_price is not in the event payload directly; we'll fill it via spot quote
        # later. Keep 0.0 here as a sentinel — discovery.py upgrades it.
        out.append(MarketInfo(
            slug=slug,
            symbol=sym,
            timeframe=tf,
            yes_token_id=str(token_ids[yes_idx]),
            no_token_id=str(token_ids[no_idx]),
            window_start_ts=window_start_ts,
            window_end_ts=window_end_ts,
            start_price=0.0,
        ))
    return out
