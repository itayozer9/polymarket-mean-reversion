"""Sharded Polymarket-WS consumption (fix for the per-connection rate-limit kill).

2026-06-12: Polymarket's WS server silently drops any single connection whose
combined book-update rate exceeds a per-connection cap (~1.6k msg/s observed).
With 15m+5m markets on ONE connection production exceeded it and got killed
every ~25-30s, leaving the engine's books 10-25s stale (zero live fills).

Fix under test: WsCollector splits the desired asset set across `n_shards`
independent WS connections (stable int(token_id) % n_shards), each with an
app-level "PING" keepalive. All shards feed the same `_books` dict, so the
aggregator and engine see one unified view.

Tests run a REAL local websockets server (no mocking, repo convention).
"""
from __future__ import annotations
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
import websockets

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.collectors.spot_collector import SpotPriceCache  # noqa: E402
from mean_reversion_live.collectors.tick_writer import CrashSafeCsvGzAppender  # noqa: E402
from mean_reversion_live.collectors.ws_collector import WsCollector  # noqa: E402
from mean_reversion_live.markets.discovery import MarketDiscovery  # noqa: E402


# ── local recording server ───────────────────────────────────────────────────

class RecordingServer:
    """Real websockets server: records each connection's subscribe message and
    PING frames; lets tests push book payloads down a specific connection."""

    def __init__(self):
        self.conns = []  # [{"subscribe": [...], "ws": proto, "pings": int, "closed": bool}]

    async def handler(self, ws):
        first = await ws.recv()
        sub = json.loads(first)
        rec = {"subscribe": sorted(sub["assets_ids"]), "ws": ws, "pings": 0, "closed": False}
        self.conns.append(rec)
        try:
            async for msg in ws:
                if msg == "PING":
                    rec["pings"] += 1
                    await ws.send("PONG")
        except Exception:
            pass
        finally:
            rec["closed"] = True

    def subscribes(self):
        return [c["subscribe"] for c in self.conns]

    async def push(self, conn_idx: int, payload) -> None:
        await self.conns[conn_idx]["ws"].send(json.dumps(payload))


async def _start_server(srv: RecordingServer):
    server = await websockets.serve(srv.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


async def _wait_until(cond, timeout=4.0, what="condition"):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def _mk_collector(tmp_path, ws_url: str, n_shards: int, keepalive_s: float = 5.0):
    async def _noop_sub(_):
        return None

    async def _noop_close(*_):
        return None

    disc = MarketDiscovery(on_subscribe=_noop_sub, on_close=_noop_close)
    spot = SpotPriceCache(["btc"])
    writer = CrashSafeCsvGzAppender(tmp_path / "live")
    c = WsCollector(disc, spot, writer, out_queue=asyncio.Queue(maxsize=100),
                    ws_url=ws_url, n_shards=n_shards, keepalive_s=keepalive_s)
    return c


def _book_payload(token_id: str, bid: float, ask: float):
    return {
        "event_type": "book",
        "asset_id": token_id,
        "bids": [{"price": str(bid), "size": "10"}],
        "asks": [{"price": str(ask), "size": "10"}],
    }


# ── tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subscribe_split_across_shards(tmp_path):
    """6 tokens, 2 shards -> two connections, subscribe lists split by
    int(token) % 2, disjoint, union == everything."""
    srv = RecordingServer()
    server, url = await _start_server(srv)
    c = _mk_collector(tmp_path, url, n_shards=2)
    run = asyncio.create_task(c.run())
    try:
        await c.update_subscriptions(["0", "1", "2", "3", "4", "5"])
        await _wait_until(lambda: len(srv.conns) >= 2, what="2 shard connections")
        subs = sorted(srv.subscribes())
        assert subs == [["0", "2", "4"], ["1", "3", "5"]]
    finally:
        c.stop()
        await asyncio.wait_for(run, timeout=3.0)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_books_from_all_shards_feed_one_collector(tmp_path):
    """A snapshot arriving on shard-0's connection and one on shard-1's both
    land in the same collector's books."""
    srv = RecordingServer()
    server, url = await _start_server(srv)
    c = _mk_collector(tmp_path, url, n_shards=2)
    run = asyncio.create_task(c.run())
    try:
        await c.update_subscriptions(["0", "1"])
        await _wait_until(lambda: len(srv.conns) >= 2, what="2 shard connections")
        # find which connection carries which token
        idx0 = next(i for i, s in enumerate(srv.subscribes()) if s == ["0"])
        idx1 = next(i for i, s in enumerate(srv.subscribes()) if s == ["1"])
        await srv.push(idx0, _book_payload("0", 0.40, 0.42))
        await srv.push(idx1, _book_payload("1", 0.55, 0.58))
        await _wait_until(lambda: "0" in c._books and "1" in c._books,
                          what="both books populated")
        assert c._books["0"].best_bid_ask()[:2] == (0.40, 0.42)
        assert c._books["1"].best_bid_ask()[:2] == (0.55, 0.58)
    finally:
        c.stop()
        await asyncio.wait_for(run, timeout=3.0)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_keepalive_ping_sent_on_each_connection(tmp_path):
    """Every shard connection sends an app-level "PING" text frame within a few
    keepalive intervals (Polymarket idle-kills silent clients)."""
    srv = RecordingServer()
    server, url = await _start_server(srv)
    c = _mk_collector(tmp_path, url, n_shards=2, keepalive_s=0.1)
    run = asyncio.create_task(c.run())
    try:
        await c.update_subscriptions(["0", "1"])
        await _wait_until(lambda: len(srv.conns) >= 2, what="2 shard connections")
        await _wait_until(lambda: all(rec["pings"] >= 1 for rec in srv.conns),
                          what="PING on every connection")
    finally:
        c.stop()
        await asyncio.wait_for(run, timeout=3.0)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_subset_change_reconnects_only_affected_shard(tmp_path):
    """Adding a token that lands in shard 1 must reconnect ONLY shard 1; the
    shard-0 connection stays up (15m books must not churn when 5m windows
    rotate into a different shard)."""
    srv = RecordingServer()
    server, url = await _start_server(srv)
    c = _mk_collector(tmp_path, url, n_shards=2)
    run = asyncio.create_task(c.run())
    try:
        await c.update_subscriptions(["0", "1"])
        await _wait_until(lambda: len(srv.conns) >= 2, what="2 shard connections")
        shard0_conn = next(r for r in srv.conns if r["subscribe"] == ["0"])
        # "3" -> int % 2 == 1 -> shard 1 only
        await c.update_subscriptions(["0", "1", "3"])
        await _wait_until(lambda: ["1", "3"] in srv.subscribes(),
                          what="shard-1 resubscribe with the new token")
        assert srv.subscribes().count(["0"]) == 1, "shard-0 must NOT have reconnected"
        assert shard0_conn["closed"] is False, "shard-0 connection must stay open"
    finally:
        c.stop()
        await asyncio.wait_for(run, timeout=3.0)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_shard_reconnects_after_server_drop(tmp_path):
    """If the server kills one shard's connection, that shard reconnects and
    resubscribes by itself (and only itself)."""
    srv = RecordingServer()
    server, url = await _start_server(srv)
    c = _mk_collector(tmp_path, url, n_shards=2)
    run = asyncio.create_task(c.run())
    try:
        await c.update_subscriptions(["0", "1"])
        await _wait_until(lambda: len(srv.conns) >= 2, what="2 shard connections")
        victim = next(r for r in srv.conns if r["subscribe"] == ["1"])
        await victim["ws"].close()
        await _wait_until(lambda: srv.subscribes().count(["1"]) >= 2,
                          what="shard-1 reconnect + resubscribe")
        assert srv.subscribes().count(["0"]) == 1, "shard-0 must NOT have reconnected"
    finally:
        c.stop()
        await asyncio.wait_for(run, timeout=3.0)
        server.close()
        await server.wait_closed()
