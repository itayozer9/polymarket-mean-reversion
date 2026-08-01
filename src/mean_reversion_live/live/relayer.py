"""Gasless redemption via Polymarket's meta-transaction relayer.

Instead of broadcasting our own Polygon transaction and paying MATIC gas from the owner EOA
(which keeps running dry — every redeem then fails at broadcast with "insufficient funds for
gas"), we submit the SAME inner calls (redeemPositions / wrap / approve) through Polymarket's
relayer (``relayer-v2.polymarket.com``). The relayer broadcasts on-chain and PAYS THE GAS —
this is the exact path the Polymarket web UI uses for "Redeem", so it works with a gas-empty
EOA.

We use the official ``py_builder_relayer_client`` SDK, which (verified by reading its source):
  * derives our Safe proxy from the owner via the Polymarket Safe factory
    (``derive(owner, factory)`` == our ``POLYMARKET_PROXY_ADDRESS`` — asserted below),
  * checks the proxy is deployed, fetches the Safe nonce from the relayer,
  * builds + signs the EIP-712 Safe struct hash with our owner key, and
  * POSTs to the relayer with HMAC builder auth, returning a transactionID + transactionHash.

We provide ONLY the inner ``(to, data)`` call, reusing claimer's calldata encoders + the
on-chain redeemability gate. The SDK is imported LAZILY so this module (and the package) still
import without it — only the gasless run needs it on the path.

Credentials (auto-derive first): prefer explicit ``.env`` BUILDER_API_KEY/BUILDER_SECRET/
BUILDER_PASS_PHRASE; otherwise derive HMAC creds from the private key via the same
py-clob-client-v2 ``create_or_derive_api_key`` call the live executor uses (clob_trade.py). If
the relayer rejects derived creds, ``relay_calls`` returns ``auth_failed`` and the caller tells
the user to create a Relayer API key (Settings -> API Keys) and set the BUILDER_* env vars.

Run env (claim_loop.sh):
  uv run --python 3.11 --no-project --with py-builder-relayer-client --with poly-eip712-structs
    --with py-builder-signing-sdk --with py-clob-client-v2 --with web3 --with eth-abi
    --with requests --with structlog --with python-dotenv ...
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import structlog
from web3 import Web3

import claimer  # same-dir module (sys.path is set by the caller / live_claim.py)

log = structlog.get_logger("relayer")

PROD_RELAYER_URL = "https://relayer-v2.polymarket.com"
CHAIN_ID = 137

# Cached singletons keyed on nothing (one wallet per process).
_client = None
_creds_source: Optional[str] = None


@dataclass
class RelayResult:
    """Outcome of a gasless relayer submission. Mirrors claimer.RedeemResult's
    success/tx_hash/reason so live_claim can treat both paths uniformly."""
    success: bool
    tx_hash: Optional[str] = None
    tx_id: Optional[str] = None
    state: Optional[str] = None
    reason: Optional[str] = None
    auth_failed: bool = False


def _normalize_pk(private_key: str) -> str:
    return private_key if private_key.startswith("0x") else "0x" + private_key


def _derive_builder_creds(private_key: str):
    """Return (BuilderApiKeyCreds, source). Prefer explicit .env builder/relayer API keys
    (manual override / fallback); else derive HMAC creds from the private key via the same
    py-clob-client-v2 call the live executor uses."""
    from py_builder_signing_sdk.config import BuilderApiKeyCreds

    key = os.getenv("BUILDER_API_KEY")
    secret = os.getenv("BUILDER_SECRET")
    passphrase = os.getenv("BUILDER_PASS_PHRASE")
    if key and secret and passphrase:
        return BuilderApiKeyCreds(key=key, secret=secret, passphrase=passphrase), "env"

    # Derive from the private key (no static key needed) — identical to clob_trade.py.
    from py_clob_client_v2 import ClobClient
    host = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
    temp = ClobClient(host, key=_normalize_pk(private_key), chain_id=CHAIN_ID)
    try:
        creds = temp.create_or_derive_api_key()
    except AttributeError:                       # older SDK method name
        creds = temp.create_or_derive_api_creds()
    k = getattr(creds, "api_key", None) or getattr(creds, "key", None)
    s = getattr(creds, "api_secret", None) or getattr(creds, "secret", None)
    p = getattr(creds, "api_passphrase", None) or getattr(creds, "passphrase", None)
    if not (k and s and p):
        raise RuntimeError("could not derive builder API creds from private key")
    return BuilderApiKeyCreds(key=k, secret=s, passphrase=p), "derived"


def get_client(private_key: str):
    """Cached RelayClient (SAFE tx type, prod relayer + chain 137 unless overridden)."""
    global _client, _creds_source
    if _client is not None:
        return _client
    from py_builder_relayer_client.client import RelayClient
    from py_builder_relayer_client.models import RelayerTxType
    from py_builder_signing_sdk.config import BuilderConfig

    creds, source = _derive_builder_creds(private_key)
    _creds_source = source
    url = os.getenv("RELAYER_URL", PROD_RELAYER_URL)
    rpc = os.getenv("POLYGON_RPC")
    _client = RelayClient(url, CHAIN_ID, _normalize_pk(private_key),
                          BuilderConfig(local_builder_creds=creds),
                          RelayerTxType.SAFE, rpc)
    log.info("relay_client_ready", relayer_url=url, creds_source=source)
    return _client


def creds_source() -> Optional[str]:
    return _creds_source


def expected_proxy(private_key: str) -> str:
    """The Safe proxy the relayer SDK will target, derived from the owner via the Polymarket
    Safe factory."""
    return get_client(private_key).get_expected_safe()


def assert_proxy(private_key: str, must_equal: str) -> str:
    """SAFETY: refuse to relay unless the SDK-derived proxy is exactly our known wallet —
    so a misconfig can never sign/redeem for a different Safe."""
    got = expected_proxy(private_key)
    if got.lower() != must_equal.lower():
        raise RuntimeError(
            f"relayer would target proxy {got}, not our wallet {must_equal} — refusing to relay")
    return got


def _parse_response(resp, mined) -> Tuple[Optional[str], Optional[str]]:
    """Pull (tx_hash, state) out of the SDK response + the polled result, defensively
    (the SDK may hand back a dict or an object)."""
    tx_hash = getattr(resp, "transaction_hash", None) or getattr(resp, "transactionHash", None)
    state = None
    if isinstance(mined, dict):
        tx_hash = mined.get("transactionHash") or mined.get("transaction_hash") or tx_hash
        state = mined.get("state")
    elif mined is not None:
        tx_hash = (getattr(mined, "transactionHash", None)
                   or getattr(mined, "transaction_hash", None) or tx_hash)
        state = getattr(mined, "state", None)
    return tx_hash, state


def relay_calls(private_key: str, calls: List[Tuple[str, str]], description: str) -> RelayResult:
    """Submit one or more inner calls [(to, data_hex), ...] through the relayer (gasless) and
    wait for a terminal state. Never raises — failures (auth/network/revert) come back as a
    RelayResult with a reason (+ auth_failed flag for credential problems)."""
    try:
        from py_builder_relayer_client.models import Transaction
        client = get_client(private_key)
        txns = [
            Transaction(
                to=Web3.to_checksum_address(to),
                data=data if data.startswith("0x") else "0x" + data,
                value="0",
            )
            for (to, data) in calls
        ]
        resp = client.execute(txns, description)
        tx_id = getattr(resp, "transaction_id", None) or getattr(resp, "transactionID", None)
        mined = resp.wait()                       # polls to STATE_MINED/CONFIRMED (or None)
        tx_hash, state = _parse_response(resp, mined)
        terminal_ok = state in ("STATE_MINED", "STATE_CONFIRMED")
        # If polling timed out (mined is None / no terminal state) but we DID submit, report
        # not-success so the daemon retries next cycle — the on-chain redeemability gate makes
        # that idempotent (a landed redeem zeroes the winning balance), so no double-claim.
        if terminal_ok:
            return RelayResult(success=True, tx_hash=tx_hash, tx_id=tx_id, state=state)
        if state in ("STATE_FAILED", "STATE_INVALID"):
            return RelayResult(success=False, tx_hash=tx_hash, tx_id=tx_id, state=state,
                               reason=f"relayer state={state}")
        return RelayResult(success=False, tx_hash=tx_hash, tx_id=tx_id, state=state,
                           reason=f"unconfirmed (tx_id={tx_id}, state={state}); retry next cycle")
    except Exception as e:                         # noqa: BLE001 — surface, don't crash the run
        msg = str(e)
        low = msg.lower()
        auth = any(t in low for t in
                   ("401", "403", "unauthorized", "forbidden", "invalid api", "api key",
                    "signature", "passphrase", "auth"))
        log.warning("relay_call_failed", description=description, error=msg, auth_failed=auth)
        return RelayResult(success=False, reason=msg, auth_failed=auth)


# ── call builders (pure: reuse claimer's calldata encoders + constants; SDK-free, testable) ──

def build_redeem_binary_call(condition_id: str, collateral: str = claimer.USDC_E,
                             index_sets=(1, 2)) -> Tuple[str, str]:
    """(to, data) for a plain binary CTF redeem (the 15m up/down markets)."""
    data = "0x" + claimer._encode_redeem_binary_inner(condition_id, collateral, index_sets).hex()
    return claimer.CONDITIONAL_TOKENS, data


def build_redeem_negrisk_call(condition_id: str, yes_amount: int, no_amount: int) -> Tuple[str, str]:
    """(to, data) for a neg-risk binary redeem (NegRiskAdapter). 15m markets are never neg-risk."""
    data = "0x" + claimer._encode_redeem_inner(condition_id, yes_amount, no_amount).hex()
    return claimer.NEG_RISK_ADAPTER, data


def build_wrap_call(proxy_address: str, amount_raw: int) -> Tuple[str, str]:
    """(to, data) for wrapping USDC.e -> pUSD (the 'Confirm pending deposit' step)."""
    data = "0x" + claimer._encode_wrap_inner(claimer.USDC_E, proxy_address, amount_raw).hex()
    return claimer.COLLATERAL_ONRAMP, data


def build_approve_call(amount: int = None) -> Tuple[str, str]:
    """(to, data) for approving USDC.e -> CollateralOnramp (enables the wrap)."""
    amt = claimer.MAX_UINT256 if amount is None else amount
    data = "0x" + claimer._encode_approve_inner(claimer.COLLATERAL_ONRAMP, amt).hex()
    return claimer.USDC_E, data


# ── high-level gasless actions ───────────────────────────────────────────────────────────

def relay_redeem_binary_ctf(private_key: str, condition_id: str,
                            collateral: str = claimer.USDC_E, index_sets=(1, 2)) -> RelayResult:
    """Gaslessly redeem a plain binary CTF winner (the 15m up/down markets)."""
    return relay_calls(private_key, [build_redeem_binary_call(condition_id, collateral, index_sets)],
                       f"redeem {condition_id[:10]}")


def relay_redeem_negrisk(private_key: str, condition_id: str,
                         yes_amount: int, no_amount: int) -> RelayResult:
    """Gaslessly redeem a neg-risk binary winner. Kept for parity with the EOA path."""
    return relay_calls(private_key, [build_redeem_negrisk_call(condition_id, yes_amount, no_amount)],
                       f"redeem-negrisk {condition_id[:10]}")


def relay_wrap_usdce(private_key: str, proxy_address: str, amount_raw: int) -> RelayResult:
    """Gaslessly wrap USDC.e -> pUSD (the 'Confirm pending deposit' step)."""
    return relay_calls(private_key, [build_wrap_call(proxy_address, amount_raw)], "wrap USDC.e->pUSD")


def relay_approve_onramp(private_key: str, amount: int = None) -> RelayResult:
    """Gaslessly approve USDC.e -> CollateralOnramp (enables the wrap). One-time/idempotent."""
    return relay_calls(private_key, [build_approve_call(amount)], "approve USDC.e->Onramp")
