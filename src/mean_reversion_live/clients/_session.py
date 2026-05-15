"""Shared aiohttp session with retry."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from typing import Any, Optional

import aiohttp
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = structlog.get_logger(__name__)


@asynccontextmanager
async def http_session(timeout_sec: int = 10):
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        yield session


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
)
async def get_json(session: aiohttp.ClientSession, url: str, params: Optional[dict] = None) -> Any:
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json()
