"""CLOB REST client — for book snapshots and prices-history (fallback only, WS is primary)."""
from __future__ import annotations
from typing import Optional

import aiohttp
import structlog

from mean_reversion_live.clients._session import get_json
from mean_reversion_live.config import get_settings

log = structlog.get_logger(__name__)


async def get_book(session: aiohttp.ClientSession, token_id: str) -> Optional[dict]:
    """Return the current top-of-book snapshot for `token_id`. None if 404."""
    base = get_settings().clob_base_url
    try:
        return await get_json(session, f"{base}/book", params={"token_id": token_id})
    except aiohttp.ClientResponseError as e:
        if e.status == 404:
            return None
        raise


async def get_price_at(session: aiohttp.ClientSession, token_id: str, ts: int) -> Optional[float]:
    """Return mid price for `token_id` at or near `ts` (unix seconds). None on failure."""
    base = get_settings().clob_base_url
    try:
        data = await get_json(
            session,
            f"{base}/prices-history",
            params={"market": token_id, "interval": "1h", "fidelity": "1"},
        )
    except aiohttp.ClientResponseError:
        return None
    if not data or "history" not in data:
        return None
    # Find the row closest to `ts`.
    rows = data["history"]
    if not rows:
        return None
    closest = min(rows, key=lambda r: abs(int(r.get("t", 0)) - ts))
    return float(closest.get("p", 0.0)) or None
