"""Fetch driver — pull Polymarket crypto-leaderboard wallets to an on-disk cache.

This is the data-collection step of the leaderboard-wallet research. It:

1. Fetches CRYPTO leaderboards for one or more time periods.
2. Builds the deduped union of `proxy_wallet` addresses across those boards.
3. For each wallet, caches the full activity history + current open positions.
4. (Optional) Decodes a random sample of each wallet's TRADE transactions
   on-chain to recover the maker/taker role per tx.
5. Writes a `manifest.json` indexing everything Phase 2 will read.

Everything lands under ``data/wallets/`` (gitignored). The cache is
incremental: a wallet whose files already exist is skipped unless
``--refresh`` is passed. The leaderboard snapshots are always re-written
(they are small and date-stamped).

Run (smoke test)::

    uv run python -m research.wallets.fetch_wallets \\
        --top 5 --limit-wallets 3 --onchain-max-txs 8

A full run is ~150-200 wallets and takes ~1h (mostly on-chain receipts).

Pure helpers (``build_wallet_union``, ``board_appearances``) are unit-tested;
the network orchestration is covered by the smoke run.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import gzip
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
import structlog

from mean_reversion_live.clients._session import http_session
from mean_reversion_live.clients.data_api import (
    LeaderboardEntry,
    fetch_activity,
    fetch_leaderboard,
    fetch_positions,
)
from mean_reversion_live.clients.polygon_logs import (
    DEFAULT_POLYGON_RPC,
    decode_wallet_txs,
)

log = structlog.get_logger(__name__)

# Repo root: research/wallets/fetch_wallets.py -> parents[2] == repo root.
# Same resolution idea as research/data/loader.py.
REPO_ROOT = Path(__file__).resolve().parents[2]
WALLETS_DIR = REPO_ROOT / "data" / "wallets"
LEADERBOARD_DIR = WALLETS_DIR / "leaderboard"
RAW_DIR = WALLETS_DIR / "raw"
ACTIVITY_DIR = RAW_DIR / "activity"
POSITIONS_DIR = RAW_DIR / "positions"
ONCHAIN_DIR = RAW_DIR / "onchain"
MANIFEST_PATH = WALLETS_DIR / "manifest.json"

# Fixed seed so the random tx sample is reproducible run-to-run.
ONCHAIN_SAMPLE_SEED = 1337

# The data-api `/activity` endpoint hard-caps pagination: requesting an offset
# above 3000 returns HTTP 400 ("max historical activity offset of 3000
# exceeded"). With a 1000-row page size the deepest reachable page is
# offset=3000, so at most ~4000 records are retrievable per wallet. We cap
# fetch_activity at that bound so a deep-history wallet does not 400 and abort
# the run — the shared client is left untouched (per CLAUDE.md). Most
# leaderboard wallets have far fewer than 4000 events; this only truncates the
# very oldest activity of the most prolific traders.
MAX_ACTIVITY_RECORDS = 4000


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------
def board_appearances(
    boards: Dict[str, List[LeaderboardEntry]],
) -> Dict[str, List[dict]]:
    """Map each wallet to the list of (period, rank) boards it appeared on.

    Args:
        boards: period name -> ranked leaderboard entries for that period.

    Returns:
        proxy_wallet -> list of ``{"period": ..., "rank": ...}`` dicts,
        ordered by the iteration order of ``boards`` then by entry order.
        Wallets with an empty address are dropped.
    """
    out: Dict[str, List[dict]] = {}
    for period, entries in boards.items():
        for e in entries:
            w = (e.proxy_wallet or "").lower()
            if not w:
                continue
            out.setdefault(w, []).append({"period": period, "rank": e.rank})
    return out


def build_wallet_union(
    boards: Dict[str, List[LeaderboardEntry]],
    limit: Optional[int] = None,
) -> List[str]:
    """Deduped, order-preserving union of wallet addresses across all boards.

    First-seen order is preserved (boards are iterated in dict order, entries
    in rank order). Empty addresses are dropped. If ``limit`` is given, only
    the first ``limit`` wallets are returned (for smoke testing).
    """
    seen: set = set()
    union: List[str] = []
    for entries in boards.values():
        for e in entries:
            w = (e.proxy_wallet or "").lower()
            if not w or w in seen:
                continue
            seen.add(w)
            union.append(w)
    if limit is not None:
        return union[:limit]
    return union


# ---------------------------------------------------------------------------
# Disk I/O helpers
# ---------------------------------------------------------------------------
def _ensure_dirs() -> None:
    for d in (LEADERBOARD_DIR, ACTIVITY_DIR, POSITIONS_DIR, ONCHAIN_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj) -> None:
    """Write JSON to ``path`` (pretty, UTF-8)."""
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _write_jsonl_gz(path: Path, rows: List[dict]) -> None:
    """Write a list of dicts as gzip'd JSONL (one JSON object per line)."""
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str))
            fh.write("\n")


def _read_jsonl_gz(path: Path) -> List[dict]:
    """Read a gzip'd JSONL file back to a list of dicts."""
    out: List[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _count_jsonl_gz(path: Path) -> int:
    """Count non-empty lines in a gzip'd JSONL file."""
    n = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


# ---------------------------------------------------------------------------
# Fetch steps
# ---------------------------------------------------------------------------
async def fetch_all_leaderboards(
    session: aiohttp.ClientSession,
    periods: List[str],
    top: int,
    fetch_date: str,
) -> Dict[str, List[LeaderboardEntry]]:
    """Fetch + cache the CRYPTO leaderboard for each period.

    Snapshots are always (re-)written — they are small and date-stamped.
    """
    boards: Dict[str, List[LeaderboardEntry]] = {}
    for period in periods:
        entries = await fetch_leaderboard(
            session,
            category="CRYPTO",
            time_period=period,
            order_by="PNL",
            max_entries=top,
        )
        boards[period] = entries
        snapshot = [dataclasses.asdict(e) for e in entries]
        path = LEADERBOARD_DIR / f"CRYPTO_{period}_{fetch_date}.json"
        _write_json(path, snapshot)
        log.info(
            "leaderboard.snapshot_written",
            period=period,
            entries=len(entries),
            path=str(path),
        )
    return boards


async def fetch_wallet_activity_positions(
    session: aiohttp.ClientSession,
    wallets: List[str],
    *,
    concurrency: int,
    refresh: bool,
) -> Dict[str, dict]:
    """Fetch + cache full activity history and open positions per wallet.

    Returns a dict ``wallet -> {"activity": n, "positions": n, "skipped": bool}``
    for the manifest. Wallets whose activity file already exists are skipped
    unless ``refresh`` is set. Activity is capped at ``MAX_ACTIVITY_RECORDS``
    (the data-api's pagination ceiling — see that constant).
    """
    sem = asyncio.Semaphore(max(1, concurrency))
    counts: Dict[str, dict] = {}
    done = 0
    started = time.monotonic()

    async def _one(wallet: str) -> None:
        nonlocal done
        async with sem:
            act_path = ACTIVITY_DIR / f"{wallet}.jsonl.gz"
            pos_path = POSITIONS_DIR / f"{wallet}.json"
            if act_path.exists() and pos_path.exists() and not refresh:
                counts[wallet] = {
                    "activity": _count_jsonl_gz(act_path),
                    "positions": len(json.loads(pos_path.read_text())),
                    "skipped": True,
                }
            else:
                records = await fetch_activity(
                    session, wallet, max_records=MAX_ACTIVITY_RECORDS
                )
                if len(records) >= MAX_ACTIVITY_RECORDS:
                    log.warning(
                        "activity.truncated_at_api_cap",
                        wallet=wallet,
                        records=len(records),
                    )
                _write_jsonl_gz(
                    act_path, [dataclasses.asdict(r) for r in records]
                )
                positions = await fetch_positions(session, wallet)
                _write_json(pos_path, positions)
                counts[wallet] = {
                    "activity": len(records),
                    "positions": len(positions),
                    "skipped": False,
                }
            done += 1
            if done % 10 == 0 or done == len(wallets):
                log.info(
                    "activity.progress",
                    done=done,
                    total=len(wallets),
                    elapsed_s=round(time.monotonic() - started, 1),
                )

    await asyncio.gather(*[_one(w) for w in wallets])
    return counts


def _sample_trade_txs(wallet: str, max_txs: int) -> List[str]:
    """Load a wallet's cached activity, return a reproducible random sample of
    unique TRADE transaction hashes (up to ``max_txs``)."""
    act_path = ACTIVITY_DIR / f"{wallet}.jsonl.gz"
    if not act_path.exists():
        return []
    records = _read_jsonl_gz(act_path)
    seen: set = set()
    tx_hashes: List[str] = []
    for r in records:
        if r.get("type") != "TRADE":
            continue
        h = (r.get("transaction_hash") or "").lower()
        if h and h not in seen:
            seen.add(h)
            tx_hashes.append(h)
    if len(tx_hashes) <= max_txs:
        return tx_hashes
    # Reproducible sample: a fixed seed mixed with the wallet so different
    # wallets get different (but stable) samples.
    rng = random.Random(f"{ONCHAIN_SAMPLE_SEED}:{wallet}")
    return rng.sample(tx_hashes, max_txs)


async def decode_wallet_onchain(
    session: aiohttp.ClientSession,
    wallets: List[str],
    *,
    rpc_url: str,
    max_txs: int,
    concurrency: int,
    refresh: bool,
) -> Dict[str, dict]:
    """Decode a sampled set of each wallet's TRADE txs on-chain.

    Runs wallets sequentially (each wallet already fans out internally via
    ``decode_wallet_txs``'s own concurrency). Returns
    ``wallet -> {"onchain": n_roles, "sampled_txs": n, "skipped": bool}``.
    """
    counts: Dict[str, dict] = {}
    for i, wallet in enumerate(wallets, start=1):
        onchain_path = ONCHAIN_DIR / f"{wallet}.jsonl.gz"
        if onchain_path.exists() and not refresh:
            counts[wallet] = {
                "onchain": _count_jsonl_gz(onchain_path),
                "sampled_txs": _count_jsonl_gz(onchain_path),
                "skipped": True,
            }
            log.info(
                "onchain.skip_cached",
                wallet=wallet,
                i=i,
                total=len(wallets),
            )
            continue
        sampled = _sample_trade_txs(wallet, max_txs)
        started = time.monotonic()
        roles = await decode_wallet_txs(
            session, rpc_url, sampled, wallet, concurrency=concurrency
        )
        _write_jsonl_gz(
            onchain_path, [dataclasses.asdict(r) for r in roles]
        )
        counts[wallet] = {
            "onchain": len(roles),
            "sampled_txs": len(sampled),
            "skipped": False,
        }
        log.info(
            "onchain.progress",
            wallet=wallet,
            i=i,
            total=len(wallets),
            n_txs=len(sampled),
            decoded=len(roles),
            elapsed_s=round(time.monotonic() - started, 1),
        )
    return counts


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
async def run(args: argparse.Namespace) -> None:
    """Run the full fetch pipeline per the parsed CLI args."""
    _ensure_dirs()
    fetch_started = datetime.now(timezone.utc)
    fetch_date = fetch_started.strftime("%Y-%m-%d")
    periods = [p.strip().upper() for p in args.periods.split(",") if p.strip()]
    rpc_url = args.rpc_url or os.environ.get("POLYGON_RPC_URL") or DEFAULT_POLYGON_RPC

    log.info(
        "fetch.start",
        periods=periods,
        top=args.top,
        limit_wallets=args.limit_wallets,
        refresh=args.refresh,
        skip_onchain=args.skip_onchain,
    )

    # Network timeout is generous: activity histories can be large and the
    # public Polygon RPC is slow.
    async with http_session(timeout_sec=60) as session:
        # 1. Leaderboards.
        boards = await fetch_all_leaderboards(session, periods, args.top, fetch_date)

        # 2. Wallet union.
        appearances = board_appearances(boards)
        wallets = build_wallet_union(boards, limit=args.limit_wallets)
        log.info(
            "fetch.wallet_union",
            total_unique=len(build_wallet_union(boards)),
            working_set=len(wallets),
        )

        # 3. Activity + positions.
        activity_counts = await fetch_wallet_activity_positions(
            session,
            wallets,
            concurrency=args.onchain_concurrency,
            refresh=args.refresh,
        )

        # 4. On-chain maker/taker decode (sampled).
        if args.skip_onchain:
            log.info("onchain.skipped_by_flag")
            onchain_counts = {}
        else:
            onchain_counts = await decode_wallet_onchain(
                session,
                wallets,
                rpc_url=rpc_url,
                max_txs=args.onchain_max_txs,
                concurrency=args.onchain_concurrency,
                refresh=args.refresh,
            )

    # 5. Manifest.
    per_wallet: Dict[str, dict] = {}
    for w in wallets:
        ac = activity_counts.get(w, {})
        oc = onchain_counts.get(w, {})
        per_wallet[w] = {
            "boards": appearances.get(w, []),
            "activity_records": ac.get("activity", 0),
            "positions": ac.get("positions", 0),
            "onchain_roles": oc.get("onchain", 0),
            "onchain_sampled_txs": oc.get("sampled_txs", 0),
        }
    manifest = {
        "fetch_timestamp": fetch_started.isoformat(),
        "fetch_date": fetch_date,
        "params": {
            "top": args.top,
            "periods": periods,
            "onchain_max_txs": args.onchain_max_txs,
            "onchain_concurrency": args.onchain_concurrency,
            "rpc_url": rpc_url,
            "limit_wallets": args.limit_wallets,
            "refresh": args.refresh,
            "skip_onchain": args.skip_onchain,
            "onchain_sample_seed": ONCHAIN_SAMPLE_SEED,
        },
        "leaderboard_sizes": {p: len(e) for p, e in boards.items()},
        "total_wallets": len(wallets),
        "wallets": per_wallet,
    }
    _write_json(MANIFEST_PATH, manifest)
    log.info(
        "fetch.done",
        wallets=len(wallets),
        manifest=str(MANIFEST_PATH),
        elapsed_s=round(
            (datetime.now(timezone.utc) - fetch_started).total_seconds(), 1
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch Polymarket crypto-leaderboard wallets to disk cache.",
    )
    p.add_argument("--top", type=int, default=100,
                   help="Max leaderboard entries per period (default 100).")
    p.add_argument("--periods", default="MONTH,WEEK,ALL",
                   help="Comma-separated leaderboard periods (default MONTH,WEEK,ALL).")
    p.add_argument("--onchain-max-txs", type=int, default=150,
                   help="Max TRADE txs to on-chain decode per wallet (default 150).")
    p.add_argument("--onchain-concurrency", type=int, default=8,
                   help="Concurrent receipt fetches / wallet fetches (default 8).")
    p.add_argument("--rpc-url", default=None,
                   help="Polygon RPC URL (default: $POLYGON_RPC_URL or built-in).")
    p.add_argument("--limit-wallets", type=int, default=None,
                   help="Cap the working set to the first N wallets (smoke test).")
    p.add_argument("--refresh", action="store_true",
                   help="Force refetch even if a wallet is already cached.")
    p.add_argument("--skip-onchain", action="store_true",
                   help="Skip the slow on-chain decode (activity-only run).")
    return p


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
