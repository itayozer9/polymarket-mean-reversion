"""Tests for the Polymarket data-api client.

Hits the real public data-api with tiny limits — no mocking, per CLAUDE.md.
Network-dependent; if data-api is unreachable the tests skip rather than fail
so a flaky public endpoint never blocks the suite.
"""
from __future__ import annotations
import sys
from pathlib import Path

import aiohttp
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.clients._session import http_session  # noqa: E402
from mean_reversion_live.clients.data_api import (  # noqa: E402
    ActivityRecord,
    LeaderboardEntry,
    fetch_activity,
    fetch_leaderboard,
    fetch_positions,
)
from mean_reversion_live.config import get_settings  # noqa: E402


_NET_ERRORS = (aiohttp.ClientError, TimeoutError)


def test_config_has_data_api_base_url():
    s = get_settings()
    assert s.data_api_base_url == "https://data-api.polymarket.com"


@pytest.mark.asyncio
async def test_fetch_leaderboard_returns_entries():
    async with http_session(timeout_sec=15) as session:
        try:
            entries = await fetch_leaderboard(session, max_entries=5)
        except _NET_ERRORS:
            pytest.skip("data-api unreachable — skipping live leaderboard test")
    assert len(entries) == 5
    for e in entries:
        assert isinstance(e, LeaderboardEntry)
        assert e.proxy_wallet  # non-empty wallet address
        assert e.proxy_wallet.startswith("0x")
        assert isinstance(e.pnl, float)
        assert isinstance(e.vol, float)
        assert isinstance(e.rank, int)
    # ranks should be ascending and distinct
    ranks = [e.rank for e in entries]
    assert ranks == sorted(ranks)


@pytest.mark.asyncio
async def test_fetch_leaderboard_paginates_across_pages():
    # page_size=2 with max_entries=5 forces 3 requests (2 + 2 + 1), exercising
    # the offset-increment + out.extend pagination loop across pages.
    async with http_session(timeout_sec=20) as session:
        try:
            entries = await fetch_leaderboard(
                session, page_size=2, max_entries=5
            )
        except _NET_ERRORS:
            pytest.skip("data-api unreachable — skipping pagination test")
    assert len(entries) == 5
    wallets = [e.proxy_wallet for e in entries]
    assert len(set(wallets)) == 5, "expected distinct proxy_wallet across pages"
    ranks = [e.rank for e in entries]
    assert ranks == sorted(ranks), "ranks should be ascending across pages"


@pytest.mark.asyncio
async def test_fetch_activity_returns_records():
    async with http_session(timeout_sec=20) as session:
        try:
            board = await fetch_leaderboard(session, max_entries=1)
        except _NET_ERRORS:
            pytest.skip("data-api unreachable — skipping live activity test")
        assert board, "expected at least one leaderboard entry"
        wallet = board[0].proxy_wallet
        try:
            records = await fetch_activity(session, wallet, max_records=50)
        except _NET_ERRORS:
            pytest.skip("data-api unreachable — skipping live activity test")
    assert len(records) <= 50
    if not records:
        pytest.skip("top wallet has no activity — nothing to assert")
    for r in records:
        assert isinstance(r, ActivityRecord)
        assert isinstance(r.timestamp, int) and r.timestamp > 1_000_000_000
        assert r.type  # non-empty type enum
    # at least one TRADE with a valid side + tx hash
    trades = [r for r in records if r.type == "TRADE"]
    assert trades, "expected at least one TRADE record"
    for t in trades:
        assert t.side in ("BUY", "SELL")
        assert t.transaction_hash.startswith("0x")


@pytest.mark.asyncio
async def test_fetch_positions_returns_list():
    async with http_session(timeout_sec=20) as session:
        try:
            board = await fetch_leaderboard(session, max_entries=1)
            wallet = board[0].proxy_wallet
            positions = await fetch_positions(session, wallet)
        except _NET_ERRORS:
            pytest.skip("data-api unreachable — skipping live positions test")
    assert isinstance(positions, list)
    for p in positions:
        assert isinstance(p, dict)
        assert p.get("proxyWallet")
