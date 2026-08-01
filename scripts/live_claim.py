"""Standalone win-claimer for the live probe. Redeems resolved WINNING positions
(Polymarket data-api redeemable=true) to USDC.e, then wraps to pUSD. Runs on Python
3.11, SEPARATE from the paper bot:

  uv run --python 3.11 --no-project --with web3 --with eth-account --with eth-abi \
    --with requests --with structlog --with python-dotenv scripts/live_claim.py [--execute]

Three ordered steps (each gas-capped, eth_call-sim-gated, audit-ledgered to
data/live/claims.jsonl):
  1. APPROVE  USDC.e -> CollateralOnramp (idempotent; the prerequisite for the wrap /
     Polymarket "Confirm pending deposit"). No-op once set.
  2. REDEEM   resolved WINNING -updown-15m- positions -> USDC.e.
  3. WRAP     ALL held USDC.e -> pUSD ("confirm deposit"), so it re-enters tradeable
     balance. Runs EVERY cycle (not only after a redeem), so leftover USDC.e never strands.

Modes:
  (default)   DRY-RUN: simulate each step (no broadcast, $0); reports pending USDC.e.
  --execute   actually approve/redeem/wrap (broadcasts; gas-capped, sim-gated).

Safety: each tx simulates via eth_call first and ABORTS on revert, so a wrong index /
stale position / missing approval can never spend gas on a failing tx. Gas ceiling 0.15
MATIC. Multi-RPC failover. Intended to run periodically (e.g. via claim_loop.sh).
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
import structlog
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")
sys.path.insert(0, str(REPO / "src" / "mean_reversion_live" / "live"))
import claimer  # noqa: E402
import relayer  # noqa: E402  (gasless path; lazily imports the relayer SDK only when used)

log = structlog.get_logger("live_claim")
GAMMA = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
DATA_API = "https://data-api.polymarket.com"
_cache: dict = {}


def candidate_positions(proxy):
    """Every current holding of the proxy — the pool we scan for redeemable winners.

    We DELIBERATELY do NOT filter on the data-api `redeemable=true` flag. That flag lags
    on-chain settlement by minutes, so filtering on it made the bot SKIP genuine winners
    that were already resolved + claimable on-chain (a won BTC 15m position sat unclaimed
    on 2026-06-08 for exactly this reason — the flag was still false). Instead we pull all
    holdings and let the winner filter (currentValue>0) + the authoritative on-chain gate
    (claimer._is_redeemable: payoutDenominator + winning-token balance) decide. Unresolved,
    losing, and already-claimed positions are all rejected at the gate, so over-fetching is
    safe.

    sizeThreshold=0 is LOAD-BEARING: the endpoint defaults to 1.0, which silently drops
    sub-1-share positions. Partial fills (e.g. a $5 order that only filled 0.75 shares)
    produce winning positions under 1 share — without this they never appear and never get
    redeemed (a $0.75 XRP win sat unclaimed on 2026-06-08 for exactly this reason)."""
    r = requests.get(f"{DATA_API}/positions",
                     params={"user": proxy, "limit": 500, "sizeThreshold": "0"}, timeout=20)
    r.raise_for_status()
    return r.json() or []


def gamma_both(slug):
    if not slug:
        return None
    if slug in _cache:
        return _cache[slug]
    r = requests.get(f"{GAMMA}/markets", params={"slug": slug}, timeout=10)
    r.raise_for_status()
    d = r.json()
    doc = (d[0] if isinstance(d, list) else d) if d else None
    if not doc or not doc.get("clobTokenIds"):
        return None
    tok = json.loads(doc["clobTokenIds"]) if isinstance(doc["clobTokenIds"], str) else doc["clobTokenIds"]
    outs = doc.get("outcomes")
    outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
    yi, ni = 0, 1
    for i, o in enumerate(outs):
        if str(o).lower() in ("up", "yes"):
            yi = i
        elif str(o).lower() in ("down", "no"):
            ni = i
    _cache[slug] = (str(tok[yi]), str(tok[ni]))
    return _cache[slug]


CLAIMS_LEDGER = REPO / "data" / "live" / "claims.jsonl"
DUST_USDCE = 0.10  # don't spend gas wrapping less than this many USDC.e


def _ledger(action: str, **fields):
    """Append one durable audit line per on-chain action (approve/redeem/wrap)."""
    try:
        CLAIMS_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(CLAIMS_LEDGER, "a") as f:
            f.write(json.dumps({"ts": time.time(), "action": action, **fields}) + "\n")
    except Exception as e:  # never let logging break the claim run
        print(f"  [warn] ledger write failed: {e}")


def _usdce_balance(proxy: str) -> float:
    rpc = os.environ.get("POLYGON_RPC") or claimer.DEFAULT_RPC
    return claimer._get_erc20_balance_raw(rpc, claimer.USDC_E, proxy) / 1e6


GAS_LOW_MATIC = 0.30  # below this the EOA may not afford a redeem; warn loudly (see preflight)


def _gas_preflight(pk: str):
    """Read the gas-paying EOA's MATIC and warn LOUDLY + ledger if it's too low to redeem.
    Low gas was a silent failure mode: redeems failed at broadcast ('insufficient funds for
    gas') and wins piled up unnoticed. Surfacing it here (and in /mean-rev-status, which
    tails claims.jsonl) means we learn BEFORE winners strand, not after."""
    try:
        eoa = claimer.eoa_address(pk)
        matic = claimer.get_matic_balance(eoa)
    except Exception as e:
        print(f"  [warn] gas preflight read failed: {e}")
        return
    if matic < GAS_LOW_MATIC:
        msg = (f"EOA {eoa} GAS LOW: {matic:.4f} MATIC (< {GAS_LOW_MATIC}). Redeems will FAIL "
               f"at broadcast until topped up — send POL to {eoa}")
        print(f"  [GAS-LOW] {msg}")
        _ledger("gas_low", success=False, eoa=eoa, matic=round(matic, 6), reason=msg)
    else:
        print(f"  gas OK: EOA {eoa[:10]}… {matic:.4f} MATIC")


def _via_relayer() -> bool:
    """Gasless relayer is the DEFAULT. Set CLAIM_VIA_RELAYER=0 to use the gas-paying EOA path."""
    return os.getenv("CLAIM_VIA_RELAYER", "1").strip().lower() not in ("0", "false", "no", "off")


def _print_auth_help():
    print("  [ACTION NEEDED] the relayer rejected the API credentials. Create a Relayer API key "
          "at https://polymarket.com/settings?tab=api-keys and set BUILDER_API_KEY / BUILDER_SECRET "
          "/ BUILDER_PASS_PHRASE in .env, then re-run. (Until then, set CLAIM_VIA_RELAYER=0 + fund "
          "EOA gas to fall back to direct broadcast.)")


def _relayer_preflight(pk: str, proxy: str) -> bool:
    """Confirm the gasless path is ready: SDK importable, creds derivable, and the relayer
    targets OUR proxy. Prints + ledgers on failure and returns False (skip cycle, retry later)
    so a setup problem never silently falls back to paying gas."""
    try:
        got = relayer.assert_proxy(pk, proxy)   # derives creds (builds client) + checks proxy
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        print(f"  [RELAYER-PREFLIGHT-FAIL] {msg}")
        _ledger("relay_preflight_failed", success=False, reason=msg)
        if any(t in msg.lower() for t in ("api key", "unauthorized", "passphrase", "auth", "creds")):
            _print_auth_help()
        return False
    print(f"  relayer mode (GASLESS): proxy {got[:10]}… creds={relayer.creds_source()} — no MATIC needed")
    return True


def main(execute: bool):
    proxy = os.environ["POLYMARKET_PROXY_ADDRESS"]
    pk = os.environ["POLYMARKET_PRIVATE_KEY"]
    rpc = os.environ.get("POLYGON_RPC") or claimer.DEFAULT_RPC
    mode = "EXECUTE" if execute else "DRY-RUN (simulate only)"
    via_relayer = _via_relayer()

    # Preflight. Relayer mode is GASLESS (no MATIC needed); if its preflight fails we skip the
    # cycle (retry next time) rather than silently fall back to paying gas.
    if via_relayer:
        if not _relayer_preflight(pk, proxy):
            print(f"done. redeemed=0 mode={mode} via=relayer (preflight failed)")
            return
    else:
        _gas_preflight(pk)

    # === Step 1: ensure USDC.e -> Onramp approval (enables wrap / "confirm deposit") ===
    if via_relayer:
        try:
            allow = claimer._get_erc20_allowance_raw(rpc, claimer.USDC_E, proxy, claimer.COLLATERAL_ONRAMP)
        except Exception as e:  # noqa: BLE001
            allow = 0
            print(f"  [warn] allowance read failed: {e}")
        if allow >= claimer.APPROVE_MIN_RAW:
            print(f"[{mode}] approve USDC.e->Onramp: already set")
        elif not execute:
            print(f"[{mode}] approve USDC.e->Onramp: WOULD relay (allowance={allow}, gasless)")
        else:
            r = relayer.relay_approve_onramp(pk)
            print(f"[{mode}] approve USDC.e->Onramp (relayer): {'OK' if r.success else 'FAIL'} "
                  f"{r.reason or ''} {r.tx_hash or ''}")
            _ledger("approve", via="relayer", success=r.success, tx_hash=r.tx_hash,
                    tx_id=r.tx_id, reason=r.reason)
            if r.auth_failed:
                _print_auth_help()
    else:
        ap = claimer.approve_usdce_for_onramp(proxy_address=proxy, private_key=pk,
                                              simulate_only=not execute)
        if ap.already_set:
            print(f"[{mode}] approve USDC.e->Onramp: already set")
        else:
            tag = "OK" if ap.success else "FAIL"
            print(f"[{mode}] approve USDC.e->Onramp: [{tag}] {ap.reason or ''} {ap.tx_hash or ''}")
            if execute:
                _ledger("approve", via="eoa", success=ap.success, tx_hash=ap.tx_hash, reason=ap.reason)

    # === Step 2: redeem resolved WINNING 15m positions ===
    pos = candidate_positions(proxy)
    print(f"[{mode}] candidate positions for {proxy[:10]}…: {len(pos)} (via={'relayer' if via_relayer else 'eoa'})")
    done = 0
    for p in pos:
        cid = p.get("conditionId")
        slug = p.get("slug") or ""
        size = p.get("size")
        title = (p.get("title") or slug or "?")[:46]
        # SHARED WALLET SAFETY: only ever touch THIS probe's crypto 15m positions —
        # never the elon-tweets bot's markets (it claims its own).
        if "-updown-15m-" not in slug:
            print(f"  SKIP (not a probe 15m market): {title}")
            continue
        # Only redeem WINNERS. A resolved loser shows curPrice/currentValue 0, so redeeming
        # would just waste a tx for nothing.
        cur_val = float(p.get("currentValue") or 0)
        cur_px = float(p.get("curPrice") or 0)
        if not (cur_val > 0 or cur_px >= 0.99):
            print(f"  skip ($0 — loss or already claimed): {title}")
            continue
        if not cid:
            print(f"  SKIP (no conditionId on position): {title}")
            continue
        # Since we scan ALL holdings (not just data-api redeemable=true), a candidate winner
        # may still be an OPEN, not-yet-resolved position (worth >$0 but the window hasn't
        # settled on-chain). Check resolution on-chain FIRST so those read as "pending", not
        # as a redeem failure — and we don't waste a submission on them.
        try:
            if claimer._payout_denominator(rpc, cid) == 0:
                print(f"  pending (not resolved on-chain yet): {title}")
                continue
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] resolution check failed for {title}: {e}")
        is_negrisk = bool(p.get("negativeRisk"))

        if via_relayer:
            # On-chain redeemability gate BEFORE relaying — avoids submitting a no-op redeem
            # (loser / already-claimed). Idempotent across cycles (balance -> 0 after a claim).
            try:
                if not claimer._is_redeemable(rpc, proxy, cid):
                    print(f"  skip (not redeemable on-chain): {title}")
                    continue
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] redeemability gate failed for {title}: {e}")
            if not execute:
                print(f"  [WOULD RELAY] redeem {title}  (~${cur_val:.2f}, gasless)")
                continue
            if is_negrisk:
                asset = str(p.get("asset") or ""); opp = str(p.get("oppositeAsset") or "")
                idx = p.get("outcomeIndex")
                if not (asset and opp and idx in (0, 1)):
                    print(f"  SKIP (missing tokens on neg-risk position): {title}")
                    continue
                yes_token, no_token = (asset, opp) if idx == 0 else (opp, asset)
                yb = claimer._get_token_balance_raw(rpc, proxy, int(yes_token))
                nb = claimer._get_token_balance_raw(rpc, proxy, int(no_token))
                r = relayer.relay_redeem_negrisk(pk, cid, yb, nb)
            else:
                r = relayer.relay_redeem_binary_ctf(pk, cid)
            tag = "OK" if r.success else "FAIL"
            print(f"  [{tag}] {title}  (~${cur_val:.2f}, relayer)  {r.reason or ''}  {r.tx_hash or ''}")
            if r.success:
                done += 1
            _ledger("redeem", via="relayer", slug=slug, condition_id=cid,
                    amount_usd=round(cur_val, 4), success=r.success, tx_hash=r.tx_hash,
                    tx_id=r.tx_id, reason=r.reason or r.state)
            if r.auth_failed:
                _print_auth_help()
            continue

        # --- EOA (gas-paying) fallback path ---
        if is_negrisk:
            asset = str(p.get("asset") or ""); opp = str(p.get("oppositeAsset") or "")
            idx = p.get("outcomeIndex")
            if not (asset and opp and idx in (0, 1)):
                print(f"  SKIP (missing tokens on neg-risk position): {title}")
                continue
            yes_token, no_token = (asset, opp) if idx == 0 else (opp, asset)
            res = claimer.redeem_both(condition_id=cid, yes_token_id=yes_token, no_token_id=no_token,
                                      proxy_address=proxy, private_key=pk, simulate_only=not execute)
        else:
            res = claimer.redeem_binary_ctf(condition_id=cid, proxy_address=proxy,
                                            private_key=pk, simulate_only=not execute)
        tag = "OK" if res.success else "FAIL"
        print(f"  [{tag}] {title}  size={size} (~${cur_val:.2f})  {res.reason or ''}  {res.tx_hash or ''}")
        if res.success and execute:
            done += 1
            _ledger("redeem", via="eoa", slug=slug, condition_id=cid, amount_usd=round(cur_val, 4),
                    success=True, tx_hash=res.tx_hash, reason=res.reason)

    # === Step 3: wrap ALL held USDC.e -> pUSD ("confirm deposit"). EVERY cycle, not just
    # after a redeem — so already-redeemed-but-unwrapped USDC.e never strands as a pending
    # deposit. No-op when the balance is below dust. ===
    try:
        usdce_raw = claimer._get_erc20_balance_raw(rpc, claimer.USDC_E, proxy)
        usdce = usdce_raw / 1e6
    except Exception as e:  # noqa: BLE001
        usdce_raw = 0
        usdce = -1.0
        print(f"  [warn] USDC.e balance read failed: {e}")
    if not execute:
        if usdce >= 0:
            print(f"[{mode}] USDC.e pending wrap: ${usdce:.2f} "
                  f"({'would wrap (gasless)' if usdce >= DUST_USDCE else 'below dust, skip'})")
    elif usdce >= DUST_USDCE:
        if via_relayer:
            r = relayer.relay_wrap_usdce(pk, proxy, usdce_raw)
            print(f"wrap USDC.e->pUSD (relayer): {'OK' if r.success else 'FAIL'} "
                  f"${usdce:.2f} {r.reason or ''} {r.tx_hash or ''}")
            _ledger("wrap", via="relayer", amount_usd=round(usdce, 4), success=r.success,
                    tx_hash=r.tx_hash, tx_id=r.tx_id, reason=r.reason or r.state)
            if r.auth_failed:
                _print_auth_help()
        else:
            wr = claimer.wrap_usdce_to_pusd(proxy_address=proxy, private_key=pk)
            print(f"wrap USDC.e->pUSD: {'OK' if wr.success else 'FAIL'} "
                  f"${wr.wrapped_amount_usd:.2f} {wr.reason or ''} {wr.tx_hash or ''}")
            _ledger("wrap", via="eoa", amount_usd=round(wr.wrapped_amount_usd, 4),
                    success=wr.success, tx_hash=wr.tx_hash, reason=wr.reason)
    else:
        print(f"[{mode}] USDC.e to wrap: ${max(usdce,0):.2f} (below dust ${DUST_USDCE}, skip)")

    print(f"done. redeemed={done} mode={mode} via={'relayer' if via_relayer else 'eoa'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="broadcast real redemptions (default: dry-run simulate)")
    main(ap.parse_args().execute)
