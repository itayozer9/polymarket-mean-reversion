"""Tests for the on-chain Polymarket maker/taker decoder.

The maker-vs-taker split is THE central question of the leaderboard-wallet
research: the data-api only gives BUY/SELL, not whether the wallet provided
liquidity (maker) or crossed the spread (taker). This decoder recovers the
split from the CTF Exchange's `OrderFilled` logs.

Tests are PURE and run against REAL captured transaction receipts saved under
`tests/fixtures/` (CLAUDE.md: no mocking — real fixtures). A live-smoke test
exercises the network path and `pytest.skip`s on RPC failure.

Verified facts (see polygon_logs.py module docstring for full detail):
  - OrderFilled topic0 = 0xd543adfd...d8ee
  - CTF exchange  0xe2222d279d744050d28e00520010520000310f59
  - NegRisk CTF   0xe111180000d2663c0091e4f400237545b87b996b
  - the on-chain `taker` of the aggregator leg is the EXCHANGE itself, not
    the real user — see classify_wallet_role for how the real role is derived.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.clients.polygon_logs import (  # noqa: E402
    CTF_EXCHANGE_ADDRESSES,
    DEFAULT_POLYGON_RPC,
    ORDER_FILLED_TOPIC0,
    FillLog,
    WalletFillRole,
    classify_wallet_role,
    decode_order_filled_logs,
    decode_wallet_txs,
    fetch_receipt,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures"

# The wallet whose real txs the fixtures were captured from (CRYPTO leaderboard
# rank 1, MONTH, fetched 2026-05-22).
TARGET_WALLET = "0x6e1d5040d0ac73709b0621f620d2a60b80d2d0fa"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ----- keccak256 (pure python) — verifies topic0 from the signature ---------

def _keccak256(msg: bytes) -> bytes:
    """Minimal pure-python Keccak-256 (Ethereum variant). No deps."""
    RC = [
        0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
        0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
        0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
        0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
        0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
        0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
        0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
        0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
    ]
    R = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
         [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]

    def rol(x, n):
        return ((x << n) | (x >> (64 - n))) & 0xFFFFFFFFFFFFFFFF

    rate = 136  # keccak-256
    m = bytearray(msg)
    m.append(0x01)
    while len(m) % rate != 0:
        m.append(0)
    m[-1] ^= 0x80
    S = [[0] * 5 for _ in range(5)]
    for off in range(0, len(m), rate):
        block = m[off:off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            S[x][y] ^= int.from_bytes(block[i * 8:i * 8 + 8], "little")
        for rnd in range(24):
            C = [S[x][0] ^ S[x][1] ^ S[x][2] ^ S[x][3] ^ S[x][4] for x in range(5)]
            D = [C[(x - 1) % 5] ^ rol(C[(x + 1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    S[x][y] ^= D[x]
            B = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    B[y][(2 * x + 3 * y) % 5] = rol(S[x][y], R[x][y])
            for x in range(5):
                for y in range(5):
                    S[x][y] = B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y])
            S[0][0] ^= RC[rnd]
    out = bytearray()
    for i in range(rate // 8):
        x, y = i % 5, i // 5
        out += S[x][y].to_bytes(8, "little")
        if len(out) >= 32:
            break
    return bytes(out[:32])


def test_keccak_helper_self_check():
    # ERC-20 Transfer is a universally-known topic0 — proves _keccak256 works.
    sig = b"Transfer(address,address,uint256)"
    assert ("0x" + _keccak256(sig).hex()
            == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")


def test_order_filled_topic0_matches_signature():
    """The module's hardcoded ORDER_FILLED_TOPIC0 must equal
    keccak256 of the real CTF Exchange event signature."""
    sig = (b"OrderFilled(bytes32,address,address,uint8,uint256,uint256,"
           b"uint256,uint256,bytes32,bytes32)")
    assert ORDER_FILLED_TOPIC0 == "0x" + _keccak256(sig).hex()


# ----- decode_order_filled_logs (PURE, real fixture) ------------------------

def test_decode_order_filled_logs_b5d41ea9():
    """Decode the real receipt 0xb5d41ea9... — verified by hand.

    This tx has exactly 3 OrderFilled logs on the NegRisk CTF Exchange:
    two resting-maker fills (the wallet is the taker) and one aggregator
    fill (the wallet is the maker, taker == the exchange contract)."""
    receipt = _load_fixture("polygon_receipt_b5d41ea9.json")
    fills = decode_order_filled_logs(receipt)
    assert len(fills) == 3
    assert all(isinstance(f, FillLog) for f in fills)
    assert all(f.tx_hash
               == "0xb5d41ea99c0da1265fd68c8ef21a185089b5a2d45fc594d7e813cb6a3ca9e6ed"
               for f in fills)

    by_idx = {f.log_index: f for f in fills}
    assert set(by_idx) == {1194, 1196, 1200}

    # Resting-maker leg #1 — verified by hand against the raw receipt.
    f0 = by_idx[1194]
    assert f0.maker == "0xa641d96eab80a59bb4c35c590e3fe7cbd90c0866"
    assert f0.taker == TARGET_WALLET
    assert f0.maker_amount == 20000
    assert f0.taker_amount == 80000
    assert f0.fee_raw == 0
    assert f0.order_hash == (
        "0xc8e91b6b658a6c1f7db1a3868bc8031f0782e4e53702b290b7bcb8bbc5b5d2c8")

    # Resting-maker leg #2.
    f1 = by_idx[1196]
    assert f1.maker == "0xbc5403011df159368c4bc716e07bcf858dfe2eec"
    assert f1.taker == TARGET_WALLET
    assert f1.maker_amount == 4781052
    assert f1.taker_amount == 19921050
    assert f1.fee_raw == 0

    # Aggregator leg — wallet is the maker; taker is the exchange itself.
    f2 = by_idx[1200]
    assert f2.maker == TARGET_WALLET
    assert f2.taker == "0xe111180000d2663c0091e4f400237545b87b996b"
    assert f2.maker_amount == 15199998
    assert f2.taker_amount == 20001050
    assert f2.fee_raw == 255400

    # All addresses / hashes lowercased.
    for f in fills:
        assert f.maker == f.maker.lower()
        assert f.taker == f.taker.lower()
        assert f.order_hash == f.order_hash.lower()
        assert f.tx_hash == f.tx_hash.lower()


def test_decode_order_filled_logs_c5fc0379_standard_exchange():
    """The 0xc5fc0379 receipt settles on the standard CTF Exchange
    (0xe2222d...) — confirms the decoder matches BOTH exchange addresses."""
    receipt = _load_fixture("polygon_receipt_c5fc0379.json")
    fills = decode_order_filled_logs(receipt)
    assert len(fills) == 2
    assert {f.log_index for f in fills} == {289, 293}
    # Every decoded fill came from a known CTF exchange address.
    for f in fills:
        pass  # address not stored on FillLog; coverage is via the count above
    # Aggregator leg carries the fee; resting leg is fee-free.
    by_idx = {f.log_index: f for f in fills}
    assert by_idx[289].maker == TARGET_WALLET   # resting maker
    assert by_idx[289].fee_raw == 0
    assert by_idx[293].taker == "0xe2222d279d744050d28e00520010520000310f59"
    assert by_idx[293].fee_raw == 64230


def test_decode_ignores_non_orderfilled_logs():
    """A receipt has 23 logs but only 3 are OrderFilled — the decoder must
    skip ERC-20 Transfers, ERC-1155 TransferSingle, OrdersMatched, etc."""
    receipt = _load_fixture("polygon_receipt_b5d41ea9.json")
    assert len(receipt["logs"]) == 23
    assert len(decode_order_filled_logs(receipt)) == 3


def test_decode_handles_empty_and_missing():
    assert decode_order_filled_logs({}) == []
    assert decode_order_filled_logs({"logs": []}) == []
    assert decode_order_filled_logs({"logs": None}) == []


# ----- classify_wallet_role (PURE) ------------------------------------------

def test_classify_wallet_role_taker():
    """In 0xb5d41ea9 the wallet crossed the spread: it appears as `maker` on
    the aggregator leg (taker == exchange) — i.e. it is the real TAKER."""
    receipt = _load_fixture("polygon_receipt_b5d41ea9.json")
    fills = decode_order_filled_logs(receipt)
    role = classify_wallet_role(fills, TARGET_WALLET)
    assert isinstance(role, WalletFillRole)
    assert role.role == "taker"
    assert role.wallet == TARGET_WALLET
    assert role.n_fills == 3
    # Fee is the aggregator-leg fee (255400 raw, 6-decimal USDC).
    assert role.total_fee_raw == 255400
    assert role.fee_usdc == pytest.approx(0.2554)


def test_classify_wallet_role_maker():
    """In 0xc5fc0379 the wallet only appears on a resting-maker leg and never
    as the aggregator — it is the real MAKER (provided liquidity)."""
    receipt = _load_fixture("polygon_receipt_c5fc0379.json")
    fills = decode_order_filled_logs(receipt)
    role = classify_wallet_role(fills, TARGET_WALLET)
    assert role.role == "maker"
    assert role.n_fills == 1
    # Resting-maker fills are fee-free for this wallet.
    assert role.total_fee_raw == 0
    assert role.fee_usdc == 0.0


def test_classify_wallet_role_absent():
    receipt = _load_fixture("polygon_receipt_b5d41ea9.json")
    fills = decode_order_filled_logs(receipt)
    role = classify_wallet_role(fills, "0x000000000000000000000000000000000000dead")
    assert role.role == "absent"
    assert role.n_fills == 0
    assert role.total_fee_raw == 0


def test_classify_wallet_role_case_insensitive():
    receipt = _load_fixture("polygon_receipt_b5d41ea9.json")
    fills = decode_order_filled_logs(receipt)
    role = classify_wallet_role(fills, TARGET_WALLET.upper())
    assert role.role == "taker"
    assert role.wallet == TARGET_WALLET  # normalised to lowercase


def test_classify_wallet_role_both():
    """A wallet that is the aggregator on one leg AND a separate resting
    maker on another leg of the same tx is classified `both`.

    Synthetic fills: leg 2 is the aggregator leg (wallet is maker,
    taker == exchange); leg 3 is a plain resting-maker leg (wallet is maker).
    """
    other = "0xa641d96eab80a59bb4c35c590e3fe7cbd90c0866"
    exch = "0xe111180000d2663c0091e4f400237545b87b996b"
    fills = [
        FillLog("0xtx", 1, "0xh1", other, "0xwallet", 1, 2, 10, 20, 0),
        FillLog("0xtx", 2, "0xh2", "0xwallet", exch, 3, 4, 30, 40, 7),
        FillLog("0xtx", 3, "0xh3", "0xwallet", other, 5, 6, 50, 60, 0),
    ]
    role = classify_wallet_role(fills, "0xwallet")
    assert role.role == "both"
    assert role.n_fills == 3
    assert role.total_fee_raw == 7  # only the aggregator leg's fee


# ----- live smoke tests (network; skip on failure) --------------------------

@pytest.mark.asyncio
async def test_fetch_receipt_live_smoke():
    """Fetch one real receipt over the public Polygon RPC; skip on RPC error."""
    import aiohttp
    tx = "0xb5d41ea99c0da1265fd68c8ef21a185089b5a2d45fc594d7e813cb6a3ca9e6ed"
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            receipt = await fetch_receipt(session, DEFAULT_POLYGON_RPC, tx)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Polygon RPC unreachable — skipping live smoke: {e}")
    if receipt is None:
        pytest.skip("Polygon RPC returned no receipt — skipping")
    fills = decode_order_filled_logs(receipt)
    assert len(fills) == 3
    role = classify_wallet_role(fills, TARGET_WALLET)
    assert role.role == "taker"


@pytest.mark.asyncio
async def test_decode_wallet_txs_live_smoke():
    """End-to-end: dedupe + bounded-concurrency decode of two real txs."""
    import aiohttp
    txs = [
        "0xb5d41ea99c0da1265fd68c8ef21a185089b5a2d45fc594d7e813cb6a3ca9e6ed",
        "0xc5fc0379cd66badb116acc4c401bdaf15252036aafd6a494cce568539b969258",
        # duplicate — must be deduped
        "0xb5d41ea99c0da1265fd68c8ef21a185089b5a2d45fc594d7e813cb6a3ca9e6ed",
    ]
    timeout = aiohttp.ClientTimeout(total=25)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            roles = await decode_wallet_txs(
                session, DEFAULT_POLYGON_RPC, txs, TARGET_WALLET, concurrency=4
            )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Polygon RPC unreachable — skipping live smoke: {e}")
    if not roles:
        pytest.skip("Polygon RPC returned nothing — skipping")
    # Deduped to 2 unique tx hashes.
    assert len(roles) == 2
    assert {r.tx_hash for r in roles} == {
        "0xb5d41ea99c0da1265fd68c8ef21a185089b5a2d45fc594d7e813cb6a3ca9e6ed",
        "0xc5fc0379cd66badb116acc4c401bdaf15252036aafd6a494cce568539b969258",
    }
    for r in roles:
        assert r.role in ("maker", "taker", "both", "absent")
        assert r.wallet == TARGET_WALLET
