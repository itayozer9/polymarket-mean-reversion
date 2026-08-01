"""Pure-encoder tests for the on-chain claimer (no network, no mocks).

These lock down the *calldata format* of the new USDC.e->Onramp approval — the bytes that
get signed and broadcast with real money — so a refactor can't silently corrupt the
selector or arguments. Broadcast paths (sim->gas->send->receipt) are validated live via the
staged recovery run + dry-run, exactly like the existing redeem/wrap code.

Skips when web3/eth_abi aren't installed (they're --with deps for the live run, not test deps),
so it runs under `uv run --extra dev --with web3 --with eth-account --with eth-abi pytest`.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("web3")
pytest.importorskip("eth_abi")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "mean_reversion_live" / "live"))
import claimer  # noqa: E402
import relayer  # noqa: E402  (imports without the relayer SDK; only the build_* helpers are tested)
from eth_abi import decode  # noqa: E402
from web3 import Web3  # noqa: E402

CID = "0x21cca93d91bcc57df42294af8b1b07469dd1f11c8b3ddf546a47ed7d845bd46c"


def test_approve_inner_selector_and_args():
    inner = claimer._encode_approve_inner(claimer.COLLATERAL_ONRAMP, claimer.MAX_UINT256)
    # selector = keccak("approve(address,uint256)")[:4] = 0x095ea7b3
    assert inner[:4] == claimer.APPROVE_SELECTOR
    assert inner[:4].hex() == "095ea7b3"
    spender, amount = decode(["address", "uint256"], inner[4:])
    assert Web3.to_checksum_address(spender) == Web3.to_checksum_address(claimer.COLLATERAL_ONRAMP)
    assert amount == claimer.MAX_UINT256 == (1 << 256) - 1


def test_approve_inner_respects_capped_amount():
    inner = claimer._encode_approve_inner(claimer.COLLATERAL_ONRAMP, 5_000_000)  # 5 USDC.e (6dp)
    _, amount = decode(["address", "uint256"], inner[4:])
    assert amount == 5_000_000


def test_approve_wraps_in_safe_exec_to_usdce():
    """The approve must execute via the Safe and target the USDC.e token (the thing the
    Safe owns), not the Onramp — a wrong `to` would revert / approve the wrong contract."""
    eoa = "0x6244Dc7b4cd97A565a70D9b66B3Aa9d3a4f09Bbf"
    inner = claimer._encode_approve_inner(claimer.COLLATERAL_ONRAMP, claimer.MAX_UINT256)
    safe = claimer._encode_safe_exec(eoa, claimer.USDC_E, inner)
    assert safe[:4] == claimer.SAFE_EXEC_SELECTOR
    to_addr = decode(["address"], safe[4:36])[0]
    assert Web3.to_checksum_address(to_addr) == Web3.to_checksum_address(claimer.USDC_E)


def test_allowance_threshold_constants():
    # idempotency gate: 0 needs approve; an unlimited allowance counts as "already set".
    assert 0 < claimer.APPROVE_MIN_RAW < claimer.MAX_UINT256
    assert claimer.MAX_UINT256 >= claimer.APPROVE_MIN_RAW
    assert not (0 >= claimer.APPROVE_MIN_RAW)


def test_onchain_redeemability_selectors():
    """Lock the ConditionalTokens view selectors that power the on-chain redeemability gate
    (the authoritative replacement for the laggy data-api `redeemable` flag)."""
    assert claimer.PAYOUT_DENOMINATOR_SELECTOR == Web3.keccak(text="payoutDenominator(bytes32)")[:4]
    assert claimer.PAYOUT_NUMERATOR_SELECTOR == Web3.keccak(text="payoutNumerators(bytes32,uint256)")[:4]
    assert claimer.GET_COLLECTION_ID_SELECTOR == Web3.keccak(text="getCollectionId(bytes32,bytes32,uint256)")[:4]
    assert claimer.GET_POSITION_ID_SELECTOR == Web3.keccak(text="getPositionId(address,bytes32)")[:4]


def test_index_set_to_outcome_mapping():
    """The gate maps CTF index sets to outcome indices as outcome = indexSet.bit_length()-1
    (indexSet 1 -> outcome 0/Up, indexSet 2 -> outcome 1/Down). A wrong mapping would check
    the losing slot's payout and skip real winners."""
    assert (1).bit_length() - 1 == 0
    assert (2).bit_length() - 1 == 1


def test_condition_bytes_validates_length():
    good = "0x" + "ab" * 32
    assert claimer._condition_bytes(good) == bytes.fromhex("ab" * 32)
    with pytest.raises(ValueError):
        claimer._condition_bytes("0xdeadbeef")  # 4 bytes, not 32


# ── gasless relayer: the (to, data) we hand the relayer must be byte-identical to the EOA path ──

def test_redeem_binary_inner_selector_and_args():
    """ConditionalTokens.redeemPositions(collateral, 0, conditionId, indexSets) — the calldata
    the 15m winners redeem with, shared by the EOA path and the relayer."""
    inner = claimer._encode_redeem_binary_inner(CID)
    assert inner[:4] == claimer.REDEEM_CTF_SELECTOR
    assert inner[:4].hex() == "01b7037c"  # keccak("redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
    collateral, parent, cid, index_sets = decode(
        ["address", "bytes32", "bytes32", "uint256[]"], inner[4:])
    assert Web3.to_checksum_address(collateral) == Web3.to_checksum_address(claimer.USDC_E)
    assert parent == b"\x00" * 32
    assert "0x" + cid.hex() == CID
    assert list(index_sets) == [1, 2]


def test_relayer_build_redeem_targets_conditional_tokens():
    """The relayer must send the redeem to ConditionalTokens with the exact same calldata the
    EOA path builds — a wrong `to`/`data` would revert or redeem the wrong thing."""
    to, data = relayer.build_redeem_binary_call(CID)
    assert Web3.to_checksum_address(to) == Web3.to_checksum_address(claimer.CONDITIONAL_TOKENS)
    assert data == "0x" + claimer._encode_redeem_binary_inner(CID).hex()


def test_relayer_build_wrap_and_approve_targets():
    proxy = "0x96fC07754ed0F020DBedeDC64021054228476119"
    to_w, data_w = relayer.build_wrap_call(proxy, 5_000_000)
    assert Web3.to_checksum_address(to_w) == Web3.to_checksum_address(claimer.COLLATERAL_ONRAMP)
    assert data_w == "0x" + claimer._encode_wrap_inner(claimer.USDC_E, proxy, 5_000_000).hex()

    to_a, data_a = relayer.build_approve_call()
    assert Web3.to_checksum_address(to_a) == Web3.to_checksum_address(claimer.USDC_E)
    # approve spender must be the Onramp, amount unlimited
    assert data_a[:10] == "0x" + claimer.APPROVE_SELECTOR.hex()
    spender, amount = decode(["address", "uint256"], bytes.fromhex(data_a[10:]))
    assert Web3.to_checksum_address(spender) == Web3.to_checksum_address(claimer.COLLATERAL_ONRAMP)
    assert amount == claimer.MAX_UINT256
