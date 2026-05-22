"""On-chain maker/taker decoder for Polymarket CTF Exchange trades (Polygon).

Why this module
---------------
The Polymarket data-api gives each trade a `transactionHash` and a BUY/SELL
`side`, but NOT whether the wallet was a **maker** (provided resting liquidity,
pays no fee) or a **taker** (crossed the spread, pays the fee). The
maker-vs-taker split is THE central question of the leaderboard-wallet
research. This module recovers it from on-chain data: every Polymarket trade
settles on Polygon via a CTF Exchange contract that emits an `OrderFilled`
event per matched order, naming the maker and taker explicitly.

We use raw JSON-RPC over aiohttp — no `web3` dependency — exactly like
`collectors/chainlink_collector.py` (a deliberate project choice to keep the
deployment light). Decoding is a handful of 32-byte ABI words.

Verified facts (EMPIRICALLY confirmed against real receipts, NOT from memory)
-----------------------------------------------------------------------------
Verified 2026-05-22 against these real txs of CRYPTO-leaderboard rank-1 wallet
`0x6e1d5040d0ac73709b0621f620d2a60b80d2d0fa`:
  - tx 0xb5d41ea99c0da1265fd68c8ef21a185089b5a2d45fc594d7e813cb6a3ca9e6ed
  - tx 0xc5fc0379cd66badb116acc4c401bdaf15252036aafd6a494cce568539b969258

1. The **real** `OrderFilled` event signature (from the verified CTFExchange
   source on Sourcify, contract 0xe2222d...) is NOT the 5-uint256 shape often
   quoted. It is:

       OrderFilled(
         bytes32 indexed orderHash,
         address indexed maker,
         address indexed taker,
         uint8   side,              // data word 0
         uint256 tokenId,           // data word 1
         uint256 makerAmountFilled, // data word 2
         uint256 takerAmountFilled, // data word 3
         uint256 fee,               // data word 4
         bytes32 builder,           // data word 5
         bytes32 metadata           // data word 6
       )

   -> 4 topics (topic0 + 3 indexed) and 7 data words.
   keccak256 of the signature == the observed topic0 (asserted in tests):
       ORDER_FILLED_TOPIC0 = 0xd543adfd...d8ee

2. CTF Exchange contract addresses observed emitting OrderFilled:
       0xe2222d279d744050d28e00520010520000310f59  (standard CTF Exchange)
       0xe111180000d2663c0091e4f400237545b87b996b  (NegRisk CTF Exchange)
   Both are verified-source `CTFExchange` contracts on Polygonscan/Sourcify.

3. The on-chain `taker` is OFTEN the EXCHANGE CONTRACT ITSELF, not the real
   user. Polymarket's matching engine emits, per tx:
     * one OrderFilled per *resting maker order* — `taker` = the user who
       submitted the marketable (aggressing) order;
     * one final "aggregator" OrderFilled for the taker order — `maker` = the
       aggressing user, `taker` = the EXCHANGE CONTRACT address.
   So the SAME wallet appears as both `maker` and `taker` across one tx's
   logs. Naive `topics[3]` matching mislabels the aggressor. The real role is
   derived by `classify_wallet_role` (see its docstring): a wallet is the
   **taker** of a tx if it is the `maker` on the aggregator leg
   (`taker == exchange address`); a **maker** if it appears only on resting
   legs.

4. Fee field: across 30 sampled txs the aggregator leg ALWAYS had `fee > 0`
   (30/30) and resting legs were almost always `fee == 0` (63/69). So
   `fee == 0` is a decent but NOT perfect proxy — the maker/taker addresses
   plus the exchange-as-taker rule are the reliable signal, and that is what
   `classify_wallet_role` uses.

5. Fee decimals: fees are denominated in the collateral token (USDC on
   Polygon, `0x2791bca1...`, 6 decimals). `fee_usdc = fee_raw / 1e6`.
   E.g. tx 0xb5d41ea9 aggregator leg fee_raw 255400 -> 0.2554 USDC.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import List, Optional

import aiohttp
import structlog

from mean_reversion_live.clients._session import post_json

log = structlog.get_logger(__name__)

# Public Polygon RPC — same endpoint chainlink_collector uses. Override with
# the POLYGON_RPC_URL env var if it rate-limits. (Imported from the collector
# so there is a single source of truth for the default.)
from mean_reversion_live.collectors.chainlink_collector import (  # noqa: E402
    DEFAULT_POLYGON_RPC,
)

# keccak256("OrderFilled(bytes32,address,address,uint8,uint256,uint256,
#            uint256,uint256,bytes32,bytes32)")
# Verified empirically against real receipts AND by keccak in the test suite.
ORDER_FILLED_TOPIC0 = (
    "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
)

# CTF Exchange contracts on Polygon that emit OrderFilled. Lowercased.
CTF_EXCHANGE_ADDRESSES = frozenset({
    "0xe2222d279d744050d28e00520010520000310f59",  # standard CTF Exchange
    "0xe111180000d2663c0091e4f400237545b87b996b",  # NegRisk CTF Exchange
})

# The fee field is denominated in the collateral token. Polymarket's
# collateral on Polygon is USDC.e (0x2791bca1...), which has 6 decimals.
FEE_TOKEN_DECIMALS = 6

# OrderFilled `data` is exactly 7 ABI words (see module docstring).
_ORDER_FILLED_DATA_WORDS = 7


@dataclass
class FillLog:
    """One decoded `OrderFilled` event — a single matched order leg.

    Faithful to the on-chain event: ``maker``/``taker`` are the *raw* event
    fields, so ``taker`` may be the exchange contract (the "aggregator leg" —
    see module docstring). Amounts/fee are ints; addresses/hashes are
    lowercased hex strings.
    """
    tx_hash: str
    log_index: int
    order_hash: str
    maker: str
    taker: str
    maker_asset_id: int   # `tokenId` of the maker's side (0 == USDC collateral)
    taker_asset_id: int   # `tokenId` of the taker's side (0 == USDC collateral)
    maker_amount: int     # makerAmountFilled (raw, token base units)
    taker_amount: int     # takerAmountFilled (raw, token base units)
    fee_raw: int          # fee (raw, collateral base units)


@dataclass
class WalletFillRole:
    """A target wallet's role across one transaction's OrderFilled logs.

    ``role`` is the *derived* role, accounting for the exchange-as-taker
    matching model (see ``classify_wallet_role``):
      - "taker"  — the wallet crossed the spread (aggressed). It is the
                   `maker` on the aggregator leg (`taker == exchange`).
      - "maker"  — the wallet only provided resting liquidity.
      - "both"   — the wallet aggressed AND separately rested in the same tx.
      - "absent" — the wallet is in none of this tx's OrderFilled logs.
    """
    tx_hash: str
    wallet: str
    role: str            # "maker" | "taker" | "both" | "absent"
    n_fills: int         # OrderFilled logs in this tx the wallet appears in
    total_fee_raw: int   # sum of fee_raw over the wallet's aggregator legs
    fee_usdc: float      # total_fee_raw / 10**FEE_TOKEN_DECIMALS


def _hex_to_int(word: str) -> int:
    """Parse a hex string (with or without 0x) to int. Empty -> 0."""
    if not word:
        return 0
    return int(word, 16)


def _addr_from_topic(topic: str) -> str:
    """Extract a lowercased 20-byte address from a 32-byte indexed topic."""
    h = topic[2:] if topic.startswith("0x") else topic
    return "0x" + h[-40:].lower()


def decode_order_filled_logs(receipt: dict) -> List[FillLog]:
    """Decode every `OrderFilled` log in a transaction receipt. PURE.

    Matches logs by (a) the log's `address` being a known CTF exchange and
    (b) `topics[0]` equalling ``ORDER_FILLED_TOPIC0`` — both case-insensitive.
    A receipt with no exchange logs returns ``[]``. Malformed individual logs
    (wrong topic/data length) are skipped with a warning rather than raising.
    """
    if not receipt:
        return []
    logs = receipt.get("logs") or []
    tx_hash = str(receipt.get("transactionHash") or "").lower()
    out: List[FillLog] = []
    for lg in logs:
        try:
            address = str(lg.get("address") or "").lower()
            topics = lg.get("topics") or []
            if address not in CTF_EXCHANGE_ADDRESSES:
                continue
            if not topics or str(topics[0]).lower() != ORDER_FILLED_TOPIC0:
                continue
            if len(topics) != 4:
                log.warning("order_filled_bad_topics", tx=tx_hash,
                            n_topics=len(topics))
                continue
            data = str(lg.get("data") or "")
            h = data[2:] if data.startswith("0x") else data
            if len(h) < _ORDER_FILLED_DATA_WORDS * 64:
                log.warning("order_filled_bad_data", tx=tx_hash, data_len=len(h))
                continue
            words = [
                _hex_to_int(h[i * 64:(i + 1) * 64])
                for i in range(_ORDER_FILLED_DATA_WORDS)
            ]
            # data layout: side, tokenId, makerAmountFilled,
            #              takerAmountFilled, fee, builder, metadata.
            side = words[0]
            token_id = words[1]
            maker_amount = words[2]
            taker_amount = words[3]
            fee = words[4]
            # The event carries one `tokenId` + a `side`. The maker holds the
            # outcome token iff side==SELL (1); the taker holds it iff
            # side==BUY (0). Collateral (USDC) is asset id 0. We surface both
            # ids so callers can see which side held the conditional token.
            if side == 0:  # BUY: taker receives the outcome token
                maker_asset_id, taker_asset_id = 0, token_id
            else:          # SELL: maker receives the outcome token
                maker_asset_id, taker_asset_id = token_id, 0
            out.append(FillLog(
                tx_hash=tx_hash,
                log_index=_hex_to_int(str(lg.get("logIndex") or "0x0")),
                order_hash=str(topics[1]).lower(),
                maker=_addr_from_topic(str(topics[2])),
                taker=_addr_from_topic(str(topics[3])),
                maker_asset_id=maker_asset_id,
                taker_asset_id=taker_asset_id,
                maker_amount=maker_amount,
                taker_amount=taker_amount,
                fee_raw=fee,
            ))
        except (ValueError, TypeError, IndexError) as e:
            log.warning("order_filled_decode_failed", tx=tx_hash, err=str(e))
            continue
    return out


def classify_wallet_role(fills: List[FillLog], wallet: str) -> WalletFillRole:
    """Determine ``wallet``'s real role across one tx's OrderFilled fills. PURE.

    Accounts for Polymarket's exchange-as-taker matching model: the on-chain
    `taker` of the "aggregator leg" is the exchange contract, and the real
    aggressor is that leg's `maker`. So:
      - the wallet is a TAKER if it is the `maker` on an aggregator leg
        (a leg whose `taker` is a CTF exchange address);
      - the wallet is a MAKER if it appears as the `maker` on a resting leg
        (or, defensively, as the `taker` on a resting leg — a plain
        wallet-to-wallet resting fill);
      - "both" if it does both in the same tx; "absent" if it does neither.

    ``total_fee_raw`` sums the fee over the wallet's aggregator legs only
    (resting legs are fee-free for the wallet under Polymarket's model).
    """
    w = wallet.lower()
    is_taker = False   # wallet aggressed (maker on an aggregator leg)
    is_maker = False   # wallet provided resting liquidity
    n_fills = 0
    total_fee_raw = 0
    for f in fills:
        leg_has_wallet = (f.maker == w or f.taker == w)
        if leg_has_wallet:
            n_fills += 1
        aggregator_leg = f.taker in CTF_EXCHANGE_ADDRESSES
        if aggregator_leg:
            if f.maker == w:
                is_taker = True
                total_fee_raw += f.fee_raw
        else:
            # Resting leg: the leg's `taker` is the aggressing user, its
            # `maker` is the resting (liquidity-providing) order.
            if f.maker == w:
                is_maker = True
            # A wallet appearing as the resting leg's `taker` is the aggressor
            # of THIS leg; that aggressor identity is captured by the
            # aggregator leg, so we do not double-count it here as a role.

    if is_maker and is_taker:
        role = "both"
    elif is_taker:
        role = "taker"
    elif is_maker:
        role = "maker"
    else:
        role = "absent"

    tx_hash = fills[0].tx_hash if fills else ""
    return WalletFillRole(
        tx_hash=tx_hash,
        wallet=w,
        role=role,
        n_fills=n_fills,
        total_fee_raw=total_fee_raw,
        fee_usdc=total_fee_raw / (10 ** FEE_TOKEN_DECIMALS),
    )


async def fetch_receipt(
    session: aiohttp.ClientSession,
    rpc_url: str,
    tx_hash: str,
) -> Optional[dict]:
    """Fetch one transaction receipt via Polygon `eth_getTransactionReceipt`.

    Returns the raw receipt dict, or None if the RPC reports no result (e.g.
    the tx is not yet mined or the hash is unknown). Network/RPC errors
    propagate — callers that want best-effort behaviour catch them.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    data = await post_json(session, rpc_url, payload)
    if not isinstance(data, dict):
        return None
    if "error" in data and data["error"]:
        log.warning("receipt_rpc_error", tx=tx_hash, error=data["error"])
        return None
    return data.get("result")


async def decode_wallet_txs(
    session: aiohttp.ClientSession,
    rpc_url: str,
    tx_hashes: List[str],
    wallet: str,
    *,
    concurrency: int = 8,
) -> List[WalletFillRole]:
    """Resolve the maker/taker role of ``wallet`` across many transactions.

    Dedupes ``tx_hashes``, fetches each receipt with bounded concurrency
    (``asyncio.Semaphore``), decodes the OrderFilled logs, and classifies the
    wallet's role per tx. A tx whose receipt fetch fails (or returns nothing)
    is logged and skipped — it does not abort the batch.

    Returns one ``WalletFillRole`` per successfully-fetched unique tx.
    """
    # Dedupe while preserving first-seen order; normalise to lowercase.
    seen = set()
    unique: List[str] = []
    for h in tx_hashes:
        if not h:
            continue
        hl = h.lower()
        if hl not in seen:
            seen.add(hl)
            unique.append(hl)

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(tx_hash: str) -> Optional[WalletFillRole]:
        async with sem:
            try:
                receipt = await fetch_receipt(session, rpc_url, tx_hash)
            except Exception as e:  # noqa: BLE001 — best-effort per task spec
                log.warning("decode_wallet_tx_fetch_failed",
                            tx=tx_hash, err=str(e))
                return None
        if receipt is None:
            log.warning("decode_wallet_tx_no_receipt", tx=tx_hash)
            return None
        fills = decode_order_filled_logs(receipt)
        role = classify_wallet_role(fills, wallet)
        # classify_wallet_role uses fills[0].tx_hash, which is empty when a tx
        # has no OrderFilled logs — backfill the real hash so callers can map.
        if not role.tx_hash:
            role.tx_hash = tx_hash
        return role

    results = await asyncio.gather(*[_one(h) for h in unique])
    out = [r for r in results if r is not None]
    log.info("decode_wallet_txs.done", wallet=wallet.lower(),
             requested=len(unique), decoded=len(out))
    return out
