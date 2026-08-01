"""Coinbase REST — public spot quotes for BTC/ETH/SOL/XRP."""
from __future__ import annotations
from typing import Optional

import aiohttp
import structlog

from mean_reversion_live.clients._session import get_json
from mean_reversion_live.config import get_settings

log = structlog.get_logger(__name__)

_SYMBOL_TO_CB = {
    "btc": "BTC-USD",
    "eth": "ETH-USD",
    "sol": "SOL-USD",
    "xrp": "XRP-USD",
    "bnb": "BNB-USD",
    "doge": "DOGE-USD",
    "hype": "HYPE-USD",
}


async def get_spot(session: aiohttp.ClientSession, symbol: str) -> Optional[float]:
    """Spot quote in USD. Returns None on error."""
    pair = _SYMBOL_TO_CB.get(symbol.lower())
    if pair is None:
        return None
    base = get_settings().coinbase_base_url
    try:
        data = await get_json(session, f"{base}/v2/prices/{pair}/spot")
    except aiohttp.ClientResponseError:
        return None
    amount = data.get("data", {}).get("amount") if isinstance(data, dict) else None
    if amount is None:
        return None
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None
