"""On-chain redemption of winning Polymarket positions via NegRiskAdapter.

Safety design:
- Hard-coded contract addresses (NegRiskAdapter, ConditionalTokens, pUSD)
- Hard-coded function selectors (can never accidentally call transfer/approve)
- eth_call simulation BEFORE any broadcast — never sends a tx that reverts
- Gas ceiling (0.15 MATIC) — refuses to send if estimate exceeds
- Idempotency check via data-api `redeemable=true` before constructing tx
- Verifies receipt status==1 before marking claimed
- Logs every tx hash for audit

Flow for a single redemption:
1. Verify position is still redeemable on-chain (data-api)
2. Get the actual YES/NO token balances from ConditionalTokens contract
3. Build inner calldata: NegRiskAdapter.redeemPositions(conditionId, [yes, no])
4. Wrap in Safe execTransaction (proxy signs as owner)
5. Simulate via eth_call — abort if reverts
6. Estimate gas — abort if > ceiling
7. Sign + broadcast EOA tx
8. Wait for receipt; verify status==1
9. Update unclaimed_positions[].claimed=True + tx_hash

The Gnosis Safe proxy used by Polymarket-issued accounts accepts the
"approved-hash" pre-validated signature pattern where the EOA submitter
acts as owner: r=owner_address (32B), s=0 (32B), v=1.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

import requests
import structlog
from eth_abi import encode
from eth_account import Account
from web3 import Web3

log = structlog.get_logger()

# V2 contracts on Polygon
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # bridged USDC.e — what redemption returns
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
CHAIN_ID = 137

# Default public Polygon RPC (override via POLYGON_RPC env). The user's
# old polymarket-arb repo used quiknode; it's reliable + no auth required.
DEFAULT_RPC = "https://rpc-mainnet.matic.quiknode.pro"

# Function selectors (keccak256(signature)[:4])
# redeemPositions(bytes32,uint256[])  → NegRiskAdapter
REDEEM_NEGRISK_SELECTOR = Web3.keccak(text="redeemPositions(bytes32,uint256[])")[:4]
# wrap(address,address,uint256)  → CollateralOnramp (USDC.e → pUSD)
WRAP_SELECTOR = Web3.keccak(text="wrap(address,address,uint256)")[:4]
# execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)
SAFE_EXEC_SELECTOR = Web3.keccak(
    text="execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)"
)[:4]
# balanceOf(address,uint256)  → ERC1155
BALANCE_OF_SELECTOR = Web3.keccak(text="balanceOf(address,uint256)")[:4]
# balanceOf(address)  → ERC20
BALANCE_OF_ERC20_SELECTOR = Web3.keccak(text="balanceOf(address)")[:4]
# approve(address,uint256) / allowance(address,address)  → ERC20 (USDC.e → Onramp)
APPROVE_SELECTOR = Web3.keccak(text="approve(address,uint256)")[:4]
ALLOWANCE_SELECTOR = Web3.keccak(text="allowance(address,address)")[:4]
MAX_UINT256 = (1 << 256) - 1
# Treat an allowance of >= 1e6 USDC.e (1e12 raw, 6dp) as "effectively unlimited / already set".
APPROVE_MIN_RAW = 10 ** 12
# ConditionalTokens views used to decide redeemability ON-CHAIN (authoritative — the
# data-api `redeemable` flag lags settlement by minutes; see _is_redeemable below).
PAYOUT_DENOMINATOR_SELECTOR = Web3.keccak(text="payoutDenominator(bytes32)")[:4]
PAYOUT_NUMERATOR_SELECTOR = Web3.keccak(text="payoutNumerators(bytes32,uint256)")[:4]
GET_COLLECTION_ID_SELECTOR = Web3.keccak(text="getCollectionId(bytes32,bytes32,uint256)")[:4]
GET_POSITION_ID_SELECTOR = Web3.keccak(text="getPositionId(address,bytes32)")[:4]

# Gas safety ceiling — refuse if estimate exceeds this (in MATIC).
# 0.15 MATIC ≈ $0.07 at current prices — trivial relative to a typical
# $50-100 redemption. Set this low enough to catch infinite-gas bugs but
# high enough to cover legitimate ~$0.05-0.10 redemption gas.
GAS_CEILING_MATIC = 0.15


@dataclass
class RedeemResult:
    success: bool
    event_id: int
    tx_hash: Optional[str] = None
    gas_used: Optional[int] = None
    reason: Optional[str] = None       # human-readable on failure


def _rpc_endpoints(primary: Optional[str] = None) -> list:
    """Ordered, de-duplicated list of Polygon RPC endpoints to try. `primary` (the
    caller's chosen rpc) goes first, then POLYGON_RPCS (comma-sep), then POLYGON_RPC,
    then well-known public fallbacks. A single dead RPC must not strand a redemption."""
    candidates = [primary]
    env_multi = os.environ.get("POLYGON_RPCS", "")
    candidates += [e.strip() for e in env_multi.split(",") if e.strip()]
    candidates += [os.environ.get("POLYGON_RPC"), DEFAULT_RPC,
                   "https://polygon-rpc.com", "https://polygon-bor-rpc.publicnode.com"]
    out: list = []
    for e in candidates:
        if e and e not in out:
            out.append(e)
    return out


def _rpc_call(rpc_url: str, method: str, params: list, timeout: int = 15) -> dict:
    """JSON-RPC with multi-endpoint failover. Only TRANSPORT failures (connection error,
    timeout, 5xx) fall through to the next endpoint; a 200 response is returned as-is —
    including a JSON-RPC ``{"error": ...}`` body, because callers rely on that to detect
    legitimate eth_call reverts / gas-estimate failures (the sim-gate). Raises only if
    every endpoint is unreachable."""
    last_err = None
    for ep in _rpc_endpoints(rpc_url):
        try:
            resp = requests.post(ep,
                                 json={"jsonrpc": "2.0", "method": method,
                                       "params": params, "id": 1},
                                 timeout=timeout)
        except Exception as e:                       # connection error / timeout
            last_err = e
            continue
        if resp.status_code >= 500:                  # transient server error
            last_err = RuntimeError(f"{ep} HTTP {resp.status_code}")
            continue
        try:
            resp.raise_for_status()
        except Exception as e:
            last_err = e
            continue
        return resp.json()                           # 200 (incl. revert {"error":...})
    raise RuntimeError(f"all RPC endpoints failed for {method}: {last_err}")


def _is_redeemable_on_polymarket(proxy: str, condition_id: str) -> bool:
    """FALLBACK ONLY. Confirm via the public Polymarket data-api that this proxy has a
    redeemable position for the given condition.

    NOTE: the data-api `redeemable` flag LAGS on-chain settlement by minutes — a window
    can be fully resolved on-chain (and claimable in the UI) while this still returns
    False, which previously made the bot skip genuine winners (`not_redeemable_on_data_api`).
    The authoritative gate is now `_is_redeemable` (on-chain payoutDenominator + winning-token
    balance); this data-api check is used only as a fallback when the RPC is unreachable."""
    # sizeThreshold=0 is load-bearing: without it the data-api defaults to 1.0 and hides
    # sub-1-share positions (partial fills), so this double-check would falsely return
    # False for a small winning position and block its redemption. See redeemable() in
    # scripts/live_claim.py for the full rationale.
    url = (f"https://data-api.polymarket.com/positions"
           f"?user={proxy}&redeemable=true&limit=200&sizeThreshold=0")
    try:
        r = requests.get(url, timeout=15)
        if not r.ok:
            return False
        for p in r.json():
            if str(p.get("conditionId", "")).lower() == condition_id.lower():
                return True
    except Exception as e:
        log.warning("redeemable_check_failed", error=str(e))
    return False


def _ct_eth_call(rpc_url: str, calldata: bytes) -> str:
    """eth_call into ConditionalTokens; raise on revert/transport error."""
    resp = _rpc_call(rpc_url, "eth_call", [{
        "to": CONDITIONAL_TOKENS, "data": "0x" + calldata.hex()}, "latest"])
    if "error" in resp:
        raise RuntimeError(f"ct eth_call failed: {resp['error']}")
    return resp.get("result") or "0x"


def _condition_bytes(condition_id: str) -> bytes:
    b = bytes.fromhex(condition_id.replace("0x", "").lower())
    if len(b) != 32:
        raise ValueError(f"bad conditionId length: {condition_id!r}")
    return b


def _payout_denominator(rpc_url: str, condition_id: str) -> int:
    """ConditionalTokens.payoutDenominator(conditionId). >0 IFF the oracle has reported
    and the condition is RESOLVED on-chain. This is the authoritative settlement signal —
    unlike the data-api `redeemable` flag, it flips the instant resolution lands."""
    raw = _ct_eth_call(rpc_url, PAYOUT_DENOMINATOR_SELECTOR + encode(
        ['bytes32'], [_condition_bytes(condition_id)]))
    return int(raw, 16) if raw and raw != "0x" else 0


def _payout_numerator(rpc_url: str, condition_id: str, outcome_index: int) -> int:
    """ConditionalTokens.payoutNumerators(conditionId, i) — >0 for the WINNING outcome."""
    raw = _ct_eth_call(rpc_url, PAYOUT_NUMERATOR_SELECTOR + encode(
        ['bytes32', 'uint256'], [_condition_bytes(condition_id), int(outcome_index)]))
    return int(raw, 16) if raw and raw != "0x" else 0


def _ctf_position_id(rpc_url: str, condition_id: str, index_set: int,
                     collateral: str = USDC_E) -> int:
    """positionId (ERC-1155 token id) for a plain binary CTF outcome: getPositionId(
    collateral, getCollectionId(0, conditionId, indexSet)). indexSet 1 = outcome 0,
    2 = outcome 1."""
    coll_raw = _ct_eth_call(rpc_url, GET_COLLECTION_ID_SELECTOR + encode(
        ['bytes32', 'bytes32', 'uint256'],
        [b"\x00" * 32, _condition_bytes(condition_id), int(index_set)]))
    collection_id = bytes.fromhex(coll_raw[2:].rjust(64, "0"))[-32:]
    pid_raw = _ct_eth_call(rpc_url, GET_POSITION_ID_SELECTOR + encode(
        ['address', 'bytes32'], [Web3.to_checksum_address(collateral), collection_id]))
    return int(pid_raw, 16) if pid_raw and pid_raw != "0x" else 0


def _onchain_winner_ctf(rpc_url: str, proxy: str, condition_id: str,
                        collateral: str = USDC_E, index_sets=(1, 2)):
    """Authoritative on-chain redeemability for a plain binary CTF position. Returns:
      True  — resolved (payoutDenominator>0) AND the proxy still holds a WINNING outcome
              token (balance>0): redeemable right now, regardless of the data-api flag.
      False — resolved but no winning balance (losing side, or already claimed → would
              just waste gas), or not yet resolved.
      None  — could not read on-chain (RPC down) → caller falls back to the data-api flag.
    The winning-token balance check is what keeps this idempotent: it goes to 0 after a
    successful claim, so we never re-redeem."""
    try:
        if _payout_denominator(rpc_url, condition_id) == 0:
            return False  # not resolved on-chain yet
        for index_set in index_sets:
            outcome_index = index_set.bit_length() - 1  # 1->0, 2->1
            if _payout_numerator(rpc_url, condition_id, outcome_index) == 0:
                continue  # losing slot
            pid = _ctf_position_id(rpc_url, condition_id, index_set, collateral)
            if _get_token_balance_raw(rpc_url, proxy, pid) > 0:
                return True
        return False
    except Exception as e:
        log.warning("onchain_redeemable_check_failed", condition_id=condition_id, error=str(e))
        return None


def _onchain_winner_tokens(rpc_url: str, proxy: str, condition_id: str,
                           yes_token_id: str, no_token_id: str):
    """Authoritative on-chain redeemability for a position whose outcome ERC-1155 token ids
    are already known (neg-risk path: ids come straight off the data-api position). Same
    semantics as `_onchain_winner_ctf` (True/False/None)."""
    try:
        if _payout_denominator(rpc_url, condition_id) == 0:
            return False
        for outcome_index, tid in ((0, yes_token_id), (1, no_token_id)):
            if _payout_numerator(rpc_url, condition_id, outcome_index) == 0:
                continue
            if _get_token_balance_raw(rpc_url, proxy, int(tid)) > 0:
                return True
        return False
    except Exception as e:
        log.warning("onchain_redeemable_check_failed", condition_id=condition_id, error=str(e))
        return None


def _is_redeemable(rpc_url: str, proxy: str, condition_id: str,
                   collateral: str = USDC_E, index_sets=(1, 2)) -> bool:
    """Authoritative redeemability gate for a binary CTF position: trust ON-CHAIN state
    (resolution + winning-token balance), and fall back to the laggy data-api flag only
    when the RPC is unreachable (rather than strand a claim)."""
    onchain = _onchain_winner_ctf(rpc_url, proxy, condition_id, collateral, index_sets)
    if onchain is not None:
        return onchain
    return _is_redeemable_on_polymarket(proxy, condition_id)


def _get_token_balance_raw(rpc_url: str, owner: str, token_id_int: int) -> int:
    """Read ERC-1155 balanceOf(owner, tokenId) on ConditionalTokens (raw u256)."""
    calldata = BALANCE_OF_SELECTOR + encode(
        ['address', 'uint256'], [Web3.to_checksum_address(owner), token_id_int]
    )
    resp = _rpc_call(rpc_url, "eth_call", [{
        "to": CONDITIONAL_TOKENS,
        "data": "0x" + calldata.hex(),
    }, "latest"])
    if "error" in resp:
        raise RuntimeError(f"balanceOf call failed: {resp['error']}")
    raw = resp["result"]
    return int(raw, 16) if raw else 0


def _get_erc20_balance_raw(rpc_url: str, token_addr: str, owner: str) -> int:
    """Read ERC-20 balanceOf(owner) (raw u256)."""
    calldata = BALANCE_OF_ERC20_SELECTOR + encode(['address'],
                                                  [Web3.to_checksum_address(owner)])
    resp = _rpc_call(rpc_url, "eth_call", [{
        "to": Web3.to_checksum_address(token_addr),
        "data": "0x" + calldata.hex(),
    }, "latest"])
    if "error" in resp:
        raise RuntimeError(f"ERC20 balanceOf failed: {resp['error']}")
    raw = resp["result"]
    return int(raw, 16) if raw else 0


def get_matic_balance(address: str, rpc_url: Optional[str] = None) -> float:
    """Native POL/MATIC balance of `address`, in MATIC. The EOA pays gas for every
    redeem/wrap/approve, so when it runs dry those txs fail at broadcast ('insufficient
    funds for gas') — this powers the low-gas preflight + status alert."""
    rpc = rpc_url or os.environ.get("POLYGON_RPC") or DEFAULT_RPC
    resp = _rpc_call(rpc, "eth_getBalance", [Web3.to_checksum_address(address), "latest"])
    if "error" in resp:
        raise RuntimeError(f"eth_getBalance failed: {resp['error']}")
    raw = resp.get("result")
    return (int(raw, 16) / 1e18) if raw and raw != "0x" else 0.0


def eoa_address(private_key: str) -> str:
    """The gas-paying EOA derived from the signing key."""
    pk = private_key if private_key.startswith("0x") else "0x" + private_key
    return Account.from_key(pk).address


def _get_erc20_allowance_raw(rpc_url: str, token_addr: str, owner: str, spender: str) -> int:
    """Read ERC-20 allowance(owner, spender) (raw u256)."""
    calldata = ALLOWANCE_SELECTOR + encode(
        ['address', 'address'],
        [Web3.to_checksum_address(owner), Web3.to_checksum_address(spender)])
    resp = _rpc_call(rpc_url, "eth_call", [{
        "to": Web3.to_checksum_address(token_addr),
        "data": "0x" + calldata.hex(),
    }, "latest"])
    if "error" in resp:
        raise RuntimeError(f"allowance call failed: {resp['error']}")
    raw = resp.get("result")
    return int(raw, 16) if raw and raw != "0x" else 0


def _encode_approve_inner(spender: str, amount: int) -> bytes:
    """Encode ERC20 approve(spender, amount)."""
    args = encode(['address', 'uint256'], [Web3.to_checksum_address(spender), amount])
    return APPROVE_SELECTOR + args


def _encode_redeem_inner(condition_id_hex: str, yes_amount: int, no_amount: int) -> bytes:
    """Encode NegRiskAdapter.redeemPositions(conditionId, [yes, no])."""
    cid_bytes = bytes.fromhex(condition_id_hex.replace("0x", "").lower())
    if len(cid_bytes) != 32:
        raise ValueError(f"conditionId must be 32 bytes, got {len(cid_bytes)}")
    args = encode(['bytes32', 'uint256[]'], [cid_bytes, [yes_amount, no_amount]])
    return REDEEM_NEGRISK_SELECTOR + args


# ConditionalTokens.redeemPositions(collateral, parentCollectionId, conditionId, indexSets) —
# the plain binary CTF redeem (what the 15m up/down markets use). Shared by the EOA-broadcast
# path (redeem_binary_ctf) and the gasless relayer path (relayer.relay_redeem_binary_ctf) so the
# exact calldata is identical and test-locked.
REDEEM_CTF_SELECTOR = Web3.keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]


def _encode_redeem_binary_inner(condition_id: str, collateral: str = USDC_E,
                                index_sets=(1, 2)) -> bytes:
    """Encode ConditionalTokens.redeemPositions(collateral, 0, conditionId, indexSets)."""
    return REDEEM_CTF_SELECTOR + encode(
        ['address', 'bytes32', 'bytes32', 'uint256[]'],
        [Web3.to_checksum_address(collateral), b"\x00" * 32,
         _condition_bytes(condition_id), list(index_sets)])


def _encode_safe_exec(eoa_owner: str, to_address: str, inner_calldata: bytes) -> bytes:
    """Encode the Gnosis-Safe-proxy execTransaction wrapper.

    Pre-validated signature format (owner direct call):
        r = owner address (32B left-padded)
        s = 0 (32B)
        v = 1 (1B)
    Total = 65 bytes.
    """
    owner_bytes = bytes.fromhex(eoa_owner.lower().replace("0x", "").zfill(64))
    s_bytes = b"\x00" * 32
    v_byte = b"\x01"
    sig = owner_bytes + s_bytes + v_byte

    args = encode(
        ['address', 'uint256', 'bytes', 'uint8',
         'uint256', 'uint256', 'uint256',
         'address', 'address', 'bytes'],
        [
            Web3.to_checksum_address(to_address),  # to
            0,                                      # value
            inner_calldata,                         # data
            0,                                      # operation = CALL
            0, 0, 0,                                # safeTxGas, baseGas, gasPrice
            "0x0000000000000000000000000000000000000000",  # gasToken
            "0x0000000000000000000000000000000000000000",  # refundReceiver
            sig,
        ]
    )
    return SAFE_EXEC_SELECTOR + args


def redeem_one(
    *,
    event_id: int,
    condition_id: str,
    token_id_yes: str,
    proxy_address: str,
    private_key: str,
    rpc_url: Optional[str] = None,
    gas_ceiling_matic: float = GAS_CEILING_MATIC,
) -> RedeemResult:
    """Redeem ONE position. Pure function — caller updates portfolio state.

    Synchronous (web3.py is sync). Wrap in asyncio.to_thread if called from
    an async context.
    """
    rpc = rpc_url or os.environ.get("POLYGON_RPC") or DEFAULT_RPC
    pk = private_key if private_key.startswith("0x") else "0x" + private_key
    eoa = Account.from_key(pk).address

    # 1) Idempotency: is this still redeemable? (on-chain authoritative + data-api fallback)
    if not _is_redeemable(rpc, proxy_address, condition_id):
        return RedeemResult(success=False, event_id=event_id,
                            reason="not_redeemable (unresolved on-chain / already claimed)")

    # 2) Read actual on-chain balance of the WINNING token
    try:
        yes_balance = _get_token_balance_raw(rpc, proxy_address, int(token_id_yes))
    except Exception as e:
        return RedeemResult(success=False, event_id=event_id,
                            reason=f"balanceOf failed: {e}")
    if yes_balance == 0:
        return RedeemResult(success=False, event_id=event_id,
                            reason="zero balance of winning token (already redeemed?)")
    log.info("redeem_balance_read", event_id=event_id,
             yes_balance_raw=yes_balance,
             yes_balance_shares=yes_balance / 1e6)

    # 3) Encode redeemPositions call. amounts = [yes_balance, 0].
    inner_calldata = _encode_redeem_inner(condition_id, yes_balance, 0)
    safe_calldata = _encode_safe_exec(eoa, NEG_RISK_ADAPTER, inner_calldata)

    tx_template = {
        "from": eoa,
        "to": Web3.to_checksum_address(proxy_address),
        "value": "0x0",
        "data": "0x" + safe_calldata.hex(),
    }

    # 4) Simulate via eth_call — must succeed before we broadcast
    sim = _rpc_call(rpc, "eth_call", [tx_template, "latest"])
    if "error" in sim:
        return RedeemResult(success=False, event_id=event_id,
                            reason=f"eth_call sim failed: {sim['error']}")
    log.info("redeem_simulation_ok", event_id=event_id, sim_result=sim.get("result"))

    # 5) Gas estimate + ceiling check
    gas_est_resp = _rpc_call(rpc, "eth_estimateGas", [tx_template])
    if "error" in gas_est_resp:
        return RedeemResult(success=False, event_id=event_id,
                            reason=f"gas estimate failed: {gas_est_resp['error']}")
    gas_estimate = int(gas_est_resp["result"], 16)
    gas_price_resp = _rpc_call(rpc, "eth_gasPrice", [])
    gas_price = int(gas_price_resp["result"], 16)
    cost_wei = gas_estimate * gas_price
    cost_matic = cost_wei / 1e18
    log.info("redeem_gas_estimate", event_id=event_id,
             gas=gas_estimate, gas_price_gwei=gas_price / 1e9,
             cost_matic=round(cost_matic, 6))
    if cost_matic > gas_ceiling_matic:
        return RedeemResult(success=False, event_id=event_id,
                            reason=f"gas cost {cost_matic:.6f} MATIC exceeds ceiling {gas_ceiling_matic}")

    # 6) Build, sign, broadcast
    nonce_resp = _rpc_call(rpc, "eth_getTransactionCount", [eoa, "latest"])
    nonce = int(nonce_resp["result"], 16)
    # Add 20% buffer to gas to avoid out-of-gas reverts
    gas_with_buffer = int(gas_estimate * 1.2)
    tx = {
        "to": Web3.to_checksum_address(proxy_address),
        "value": 0,
        "data": "0x" + safe_calldata.hex(),
        "gas": gas_with_buffer,
        "gasPrice": int(gas_price * 1.1),  # 10% bid for inclusion
        "nonce": nonce,
        "chainId": CHAIN_ID,
    }
    signed = Account.sign_transaction(tx, pk)
    raw = "0x" + signed.raw_transaction.hex()
    send_resp = _rpc_call(rpc, "eth_sendRawTransaction", [raw])
    if "error" in send_resp:
        return RedeemResult(success=False, event_id=event_id,
                            reason=f"send failed: {send_resp['error']}")
    tx_hash = send_resp["result"]
    log.info("redeem_tx_sent", event_id=event_id, tx_hash=tx_hash)

    # 7) Wait for receipt
    receipt = None
    for _ in range(60):
        r = _rpc_call(rpc, "eth_getTransactionReceipt", [tx_hash])
        if r.get("result"):
            receipt = r["result"]
            break
        import time as _t
        _t.sleep(2)
    if receipt is None:
        return RedeemResult(success=False, event_id=event_id, tx_hash=tx_hash,
                            reason="timeout waiting for receipt (tx may still confirm later)")
    status_hex = receipt.get("status", "0x0")
    status = int(status_hex, 16) if isinstance(status_hex, str) else int(status_hex)
    gas_used = int(receipt.get("gasUsed", "0x0"), 16)
    if status != 1:
        return RedeemResult(success=False, event_id=event_id, tx_hash=tx_hash,
                            gas_used=gas_used,
                            reason=f"tx reverted (status={status})")

    return RedeemResult(success=True, event_id=event_id,
                        tx_hash=tx_hash, gas_used=gas_used)


def redeem_both(
    *,
    condition_id: str,
    yes_token_id: str,
    no_token_id: str,
    proxy_address: str,
    private_key: str,
    rpc_url: Optional[str] = None,
    gas_ceiling_matic: float = GAS_CEILING_MATIC,
    simulate_only: bool = False,
) -> RedeemResult:
    """Redeem a BINARY position passing BOTH outcome balances [yes_bal, no_bal] at
    their correct indices — correct whether we hold the Up or Down side (unlike
    redeem_one, which assumes the held token is index 0). simulate_only=True stops
    after the eth_call simulation (NO broadcast) for a safe dry-run."""
    rpc = rpc_url or os.environ.get("POLYGON_RPC") or DEFAULT_RPC
    pk = private_key if private_key.startswith("0x") else "0x" + private_key
    eoa = Account.from_key(pk).address
    onchain = _onchain_winner_tokens(rpc, proxy_address, condition_id, yes_token_id, no_token_id)
    redeemable_now = onchain if onchain is not None else \
        _is_redeemable_on_polymarket(proxy_address, condition_id)
    if not redeemable_now:
        return RedeemResult(success=False, event_id=0, reason="not_redeemable (unresolved/already claimed)")
    try:
        yes_bal = _get_token_balance_raw(rpc, proxy_address, int(yes_token_id))
        no_bal = _get_token_balance_raw(rpc, proxy_address, int(no_token_id))
    except Exception as e:
        return RedeemResult(success=False, event_id=0, reason=f"balanceOf failed: {e}")
    if yes_bal == 0 and no_bal == 0:
        return RedeemResult(success=False, event_id=0, reason="zero balance both sides")
    log.info("redeem_both_balances", yes_shares=yes_bal / 1e6, no_shares=no_bal / 1e6)
    cid_bytes = bytes.fromhex(condition_id.replace("0x", "").lower())
    if len(cid_bytes) != 32:
        return RedeemResult(success=False, event_id=0, reason="bad conditionId length")
    inner = REDEEM_NEGRISK_SELECTOR + encode(['bytes32', 'uint256[]'], [cid_bytes, [yes_bal, no_bal]])
    safe_calldata = _encode_safe_exec(eoa, NEG_RISK_ADAPTER, inner)
    tx_template = {"from": eoa, "to": Web3.to_checksum_address(proxy_address),
                   "value": "0x0", "data": "0x" + safe_calldata.hex()}
    sim = _rpc_call(rpc, "eth_call", [tx_template, "latest"])
    if "error" in sim:
        return RedeemResult(success=False, event_id=0, reason=f"sim reverted: {sim['error']}")
    if simulate_only:
        return RedeemResult(success=True, event_id=0, reason="simulated_ok (no broadcast)")
    gas_resp = _rpc_call(rpc, "eth_estimateGas", [tx_template])
    if "error" in gas_resp:
        return RedeemResult(success=False, event_id=0, reason=f"gas est failed: {gas_resp['error']}")
    gas_estimate = int(gas_resp["result"], 16)
    gas_price = int(_rpc_call(rpc, "eth_gasPrice", [])["result"], 16)
    if gas_estimate * gas_price / 1e18 > gas_ceiling_matic:
        return RedeemResult(success=False, event_id=0, reason="gas over ceiling")
    nonce = int(_rpc_call(rpc, "eth_getTransactionCount", [eoa, "latest"])["result"], 16)
    tx = {"to": Web3.to_checksum_address(proxy_address), "value": 0,
          "data": "0x" + safe_calldata.hex(), "gas": int(gas_estimate * 1.2),
          "gasPrice": int(gas_price * 1.1), "nonce": nonce, "chainId": CHAIN_ID}
    signed = Account.sign_transaction(tx, pk)
    send = _rpc_call(rpc, "eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
    if "error" in send:
        return RedeemResult(success=False, event_id=0, reason=f"send failed: {send['error']}")
    tx_hash = send["result"]
    log.info("redeem_both_tx_sent", tx_hash=tx_hash)
    import time as _t
    for _ in range(60):
        r = _rpc_call(rpc, "eth_getTransactionReceipt", [tx_hash])
        if r.get("result"):
            status = int(r["result"].get("status", "0x0"), 16)
            gas_used = int(r["result"].get("gasUsed", "0x0"), 16)
            return RedeemResult(success=(status == 1), event_id=0, tx_hash=tx_hash,
                                gas_used=gas_used,
                                reason=None if status == 1 else "reverted")
        _t.sleep(2)
    return RedeemResult(success=False, event_id=0, tx_hash=tx_hash, reason="receipt timeout")


def redeem_binary_ctf(
    *,
    condition_id: str,
    proxy_address: str,
    private_key: str,
    collateral: str = USDC_E,
    index_sets: tuple = (1, 2),
    rpc_url: Optional[str] = None,
    gas_ceiling_matic: float = GAS_CEILING_MATIC,
    simulate_only: bool = False,
) -> RedeemResult:
    """Redeem a NON-neg-risk binary position via the standard Gnosis ConditionalTokens
    contract: redeemPositions(collateral, parentCollectionId=0, conditionId, indexSets).

    Polymarket's 15m up/down markets are plain binary CTF markets (negativeRisk=False),
    collateralised in USDC.e — they must be redeemed HERE, not via the NegRiskAdapter
    (which reverts GS013 for them). Pays out the held winning outcome in USDC.e.
    index_sets=(1,2) settles both outcome slots (you're paid for whichever you hold).
    simulate_only=True stops after the eth_call simulation (NO broadcast)."""
    rpc = rpc_url or os.environ.get("POLYGON_RPC") or DEFAULT_RPC
    pk = private_key if private_key.startswith("0x") else "0x" + private_key
    eoa = Account.from_key(pk).address
    if not _is_redeemable(rpc, proxy_address, condition_id, collateral, index_sets):
        return RedeemResult(success=False, event_id=0,
                            reason="not_redeemable (unresolved on-chain / already claimed)")
    try:
        inner = _encode_redeem_binary_inner(condition_id, collateral, index_sets)
    except ValueError as e:
        return RedeemResult(success=False, event_id=0, reason=f"bad conditionId: {e}")
    safe_calldata = _encode_safe_exec(eoa, CONDITIONAL_TOKENS, inner)
    tx_template = {"from": eoa, "to": Web3.to_checksum_address(proxy_address),
                   "value": "0x0", "data": "0x" + safe_calldata.hex()}
    # Simulate — abort before broadcast if it would revert (wrong index / already claimed)
    sim = _rpc_call(rpc, "eth_call", [tx_template, "latest"])
    if "error" in sim:
        return RedeemResult(success=False, event_id=0, reason=f"sim reverted: {sim['error']}")
    if simulate_only:
        return RedeemResult(success=True, event_id=0, reason="simulated_ok (no broadcast)")
    gas_resp = _rpc_call(rpc, "eth_estimateGas", [tx_template])
    if "error" in gas_resp:
        return RedeemResult(success=False, event_id=0, reason=f"gas est failed: {gas_resp['error']}")
    gas_estimate = int(gas_resp["result"], 16)
    gas_price = int(_rpc_call(rpc, "eth_gasPrice", [])["result"], 16)
    if gas_estimate * gas_price / 1e18 > gas_ceiling_matic:
        return RedeemResult(success=False, event_id=0, reason="gas over ceiling")
    nonce = int(_rpc_call(rpc, "eth_getTransactionCount", [eoa, "latest"])["result"], 16)
    tx = {"to": Web3.to_checksum_address(proxy_address), "value": 0,
          "data": "0x" + safe_calldata.hex(), "gas": int(gas_estimate * 1.2),
          "gasPrice": int(gas_price * 1.1), "nonce": nonce, "chainId": CHAIN_ID}
    signed = Account.sign_transaction(tx, pk)
    send = _rpc_call(rpc, "eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
    if "error" in send:
        return RedeemResult(success=False, event_id=0, reason=f"send failed: {send['error']}")
    tx_hash = send["result"]
    log.info("redeem_ctf_tx_sent", tx_hash=tx_hash, condition_id=condition_id)
    import time as _t
    for _ in range(60):
        r = _rpc_call(rpc, "eth_getTransactionReceipt", [tx_hash])
        if r.get("result"):
            status = int(r["result"].get("status", "0x0"), 16)
            gas_used = int(r["result"].get("gasUsed", "0x0"), 16)
            return RedeemResult(success=(status == 1), event_id=0, tx_hash=tx_hash,
                                gas_used=gas_used, reason=None if status == 1 else "reverted")
        _t.sleep(2)
    return RedeemResult(success=False, event_id=0, tx_hash=tx_hash, reason="receipt timeout")


def _encode_wrap_inner(asset: str, recipient: str, amount: int) -> bytes:
    """Encode CollateralOnramp.wrap(asset, to, amount)."""
    args = encode(
        ['address', 'address', 'uint256'],
        [Web3.to_checksum_address(asset), Web3.to_checksum_address(recipient), amount]
    )
    return WRAP_SELECTOR + args


@dataclass
class WrapResult:
    success: bool
    tx_hash: Optional[str] = None
    wrapped_amount_usd: float = 0.0
    gas_used: Optional[int] = None
    reason: Optional[str] = None


def wrap_usdce_to_pusd(
    *,
    proxy_address: str,
    private_key: str,
    rpc_url: Optional[str] = None,
    gas_ceiling_matic: float = GAS_CEILING_MATIC,
) -> WrapResult:
    """Wrap all USDC.e the proxy holds into pUSD via the Collateral Onramp.
    No-op if the proxy holds no USDC.e.

    The USDC.e allowance to the Onramp must already be set (one-time UI setup).
    """
    rpc = rpc_url or os.environ.get("POLYGON_RPC") or DEFAULT_RPC
    pk = private_key if private_key.startswith("0x") else "0x" + private_key
    eoa = Account.from_key(pk).address

    # Read current USDC.e balance
    try:
        usdce_balance = _get_erc20_balance_raw(rpc, USDC_E, proxy_address)
    except Exception as e:
        return WrapResult(success=False, reason=f"USDC.e balanceOf failed: {e}")
    if usdce_balance == 0:
        return WrapResult(success=True, wrapped_amount_usd=0.0,
                          reason="no USDC.e to wrap")

    log.info("wrap_balance_read", usdce_raw=usdce_balance,
             usdce_human=usdce_balance / 1e6)

    inner = _encode_wrap_inner(USDC_E, proxy_address, usdce_balance)
    safe_calldata = _encode_safe_exec(eoa, COLLATERAL_ONRAMP, inner)

    tx_template = {
        "from": eoa,
        "to": Web3.to_checksum_address(proxy_address),
        "value": "0x0",
        "data": "0x" + safe_calldata.hex(),
    }

    # Simulate
    sim = _rpc_call(rpc, "eth_call", [tx_template, "latest"])
    if "error" in sim:
        return WrapResult(success=False, reason=f"sim failed: {sim['error']}")
    log.info("wrap_simulation_ok", sim_result=sim.get("result"))

    # Gas check
    gas_est_resp = _rpc_call(rpc, "eth_estimateGas", [tx_template])
    if "error" in gas_est_resp:
        return WrapResult(success=False, reason=f"gas estimate failed: {gas_est_resp['error']}")
    gas_estimate = int(gas_est_resp["result"], 16)
    gas_price = int(_rpc_call(rpc, "eth_gasPrice", [])["result"], 16)
    cost_matic = gas_estimate * gas_price / 1e18
    log.info("wrap_gas_estimate", gas=gas_estimate,
             cost_matic=round(cost_matic, 6))
    if cost_matic > gas_ceiling_matic:
        return WrapResult(success=False,
                          reason=f"gas {cost_matic:.6f} > ceiling {gas_ceiling_matic}")

    # Sign + broadcast
    nonce = int(_rpc_call(rpc, "eth_getTransactionCount", [eoa, "latest"])["result"], 16)
    tx = {
        "to": Web3.to_checksum_address(proxy_address),
        "value": 0,
        "data": "0x" + safe_calldata.hex(),
        "gas": int(gas_estimate * 1.2),
        "gasPrice": int(gas_price * 1.1),
        "nonce": nonce,
        "chainId": CHAIN_ID,
    }
    signed = Account.sign_transaction(tx, pk)
    raw = "0x" + signed.raw_transaction.hex()
    send_resp = _rpc_call(rpc, "eth_sendRawTransaction", [raw])
    if "error" in send_resp:
        return WrapResult(success=False, reason=f"send failed: {send_resp['error']}")
    tx_hash = send_resp["result"]
    log.info("wrap_tx_sent", tx_hash=tx_hash)

    import time as _t
    receipt = None
    for _ in range(60):
        r = _rpc_call(rpc, "eth_getTransactionReceipt", [tx_hash])
        if r.get("result"):
            receipt = r["result"]
            break
        _t.sleep(2)
    if receipt is None:
        return WrapResult(success=False, tx_hash=tx_hash,
                          reason="timeout waiting for receipt")
    status = int(receipt.get("status", "0x0"), 16)
    gas_used = int(receipt.get("gasUsed", "0x0"), 16)
    if status != 1:
        return WrapResult(success=False, tx_hash=tx_hash, gas_used=gas_used,
                          reason=f"tx reverted (status={status})")
    return WrapResult(success=True, tx_hash=tx_hash,
                      wrapped_amount_usd=usdce_balance / 1e6,
                      gas_used=gas_used)


@dataclass
class ApproveResult:
    success: bool
    tx_hash: Optional[str] = None
    already_set: bool = False
    allowance_raw: int = 0
    gas_used: Optional[int] = None
    reason: Optional[str] = None


def approve_usdce_for_onramp(
    *,
    proxy_address: str,
    private_key: str,
    rpc_url: Optional[str] = None,
    gas_ceiling_matic: float = GAS_CEILING_MATIC,
    amount: int = MAX_UINT256,
    simulate_only: bool = False,
) -> ApproveResult:
    """Approve the CollateralOnramp to spend the proxy's USDC.e — the on-chain prerequisite
    for wrap_usdce_to_pusd ("Confirm pending deposit"). IDEMPOTENT: if the allowance is already
    set (>= APPROVE_MIN_RAW) this is a $0 no-op. Otherwise it approves `amount` (default
    unbounded) via the Safe execTransaction pattern, with the same safety gates as redeem/wrap:
    eth_call simulate → gas ceiling → broadcast → verify receipt status==1.
    simulate_only=True stops after the eth_call simulation (NO broadcast)."""
    rpc = rpc_url or os.environ.get("POLYGON_RPC") or DEFAULT_RPC
    pk = private_key if private_key.startswith("0x") else "0x" + private_key
    eoa = Account.from_key(pk).address

    # Idempotency: skip if the allowance is already effectively unlimited.
    try:
        cur = _get_erc20_allowance_raw(rpc, USDC_E, proxy_address, COLLATERAL_ONRAMP)
    except Exception as e:
        return ApproveResult(success=False, reason=f"allowance read failed: {e}")
    if cur >= APPROVE_MIN_RAW:
        return ApproveResult(success=True, already_set=True, allowance_raw=cur,
                             reason="allowance already set")
    log.info("approve_needed", usdce_onramp_allowance_raw=cur)

    inner = _encode_approve_inner(COLLATERAL_ONRAMP, amount)
    safe_calldata = _encode_safe_exec(eoa, USDC_E, inner)
    tx_template = {"from": eoa, "to": Web3.to_checksum_address(proxy_address),
                   "value": "0x0", "data": "0x" + safe_calldata.hex()}

    sim = _rpc_call(rpc, "eth_call", [tx_template, "latest"])
    if "error" in sim:
        return ApproveResult(success=False, reason=f"sim reverted: {sim['error']}")
    if simulate_only:
        return ApproveResult(success=True, allowance_raw=cur, reason="simulated_ok (no broadcast)")

    gas_resp = _rpc_call(rpc, "eth_estimateGas", [tx_template])
    if "error" in gas_resp:
        return ApproveResult(success=False, reason=f"gas est failed: {gas_resp['error']}")
    gas_estimate = int(gas_resp["result"], 16)
    gas_price = int(_rpc_call(rpc, "eth_gasPrice", [])["result"], 16)
    if gas_estimate * gas_price / 1e18 > gas_ceiling_matic:
        return ApproveResult(success=False, reason="gas over ceiling")

    nonce = int(_rpc_call(rpc, "eth_getTransactionCount", [eoa, "latest"])["result"], 16)
    tx = {"to": Web3.to_checksum_address(proxy_address), "value": 0,
          "data": "0x" + safe_calldata.hex(), "gas": int(gas_estimate * 1.2),
          "gasPrice": int(gas_price * 1.1), "nonce": nonce, "chainId": CHAIN_ID}
    signed = Account.sign_transaction(tx, pk)
    send = _rpc_call(rpc, "eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
    if "error" in send:
        return ApproveResult(success=False, reason=f"send failed: {send['error']}")
    tx_hash = send["result"]
    log.info("approve_tx_sent", tx_hash=tx_hash, spender=COLLATERAL_ONRAMP)

    import time as _t
    for _ in range(60):
        r = _rpc_call(rpc, "eth_getTransactionReceipt", [tx_hash])
        if r.get("result"):
            status = int(r["result"].get("status", "0x0"), 16)
            gas_used = int(r["result"].get("gasUsed", "0x0"), 16)
            return ApproveResult(success=(status == 1), tx_hash=tx_hash, gas_used=gas_used,
                                 reason=None if status == 1 else "reverted")
        _t.sleep(2)
    return ApproveResult(success=False, tx_hash=tx_hash, reason="receipt timeout")


# ----- Async wrapper for the live tick loop --------------------------

async def redeem_all_unclaimed(portfolio, *, dry_run: bool = False) -> list[RedeemResult]:
    """Iterate the portfolio's unclaimed_positions and try to redeem each.

    `portfolio` is a LivePortfolio. On success, marks claimed + records tx_hash.
    """
    results: list[RedeemResult] = []
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
    proxy = os.environ.get("POLYMARKET_PROXY_ADDRESS")
    if not private_key or not proxy:
        log.warning("redeem_skipped_no_env")
        return results
    for u in list(portfolio.unclaimed_positions):
        if u.claimed or not u.condition_id:
            continue
        if dry_run:
            log.info("redeem_dry_run",
                     event_id=u.event_id, condition_id=u.condition_id,
                     shares=u.shares, expected_payout=u.expected_payout_usd)
            continue
        log.info("redeem_attempting", event_id=u.event_id,
                 condition_id=u.condition_id, shares=u.shares)
        result = await asyncio.to_thread(
            redeem_one,
            event_id=u.event_id,
            condition_id=u.condition_id,
            token_id_yes=u.token_id,
            proxy_address=proxy,
            private_key=private_key,
        )
        results.append(result)
        if result.success:
            log.info("redeem_success", event_id=u.event_id,
                     tx_hash=result.tx_hash, gas_used=result.gas_used)
            portfolio.mark_claimed(u.event_id, u.token_id, tx_hash=result.tx_hash)
            portfolio.record_tx(
                kind="claim",
                detail=f"redeemed {u.bracket_label} ({u.shares} shares, ~${u.expected_payout_usd:.2f})",
                tx_hash=result.tx_hash,
                event_id=u.event_id,
            )
        else:
            log.warning("redeem_failed",
                        event_id=u.event_id, reason=result.reason,
                        tx_hash=result.tx_hash)
    return results
