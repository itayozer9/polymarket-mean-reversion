"""READ-ONLY live preflight — validates the Polymarket wallet + CLOB connectivity.
Places NO orders. Run on Python 3.11 (py-clob-client-v2 needs >=3.9.10):

  uv run --python 3.11 --no-project \
    --with py-clob-client-v2 --with python-dotenv --with structlog \
    scripts/live_preflight.py

Reports: signer EOA, proxy (funder), pUSD collateral balance, and the V2-contract
allowance status — so we know if the wallet is funded + approved for the $100 probe.
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")
# import the vendored, standalone executor directly (no package __init__ chain)
sys.path.insert(0, str(REPO / "src" / "mean_reversion_live" / "live"))
import clob_trade  # noqa: E402

# The 3 Polymarket V2 collateral contracts that must be approved (from elon guards).
V2_CONTRACTS = {
    "CTF Exchange V2": "0xE111180000d2663C0091e4f400237545B87B996B",
    "NegRisk Adapter": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
    "NegRisk Exchange V2": "0xe2222d279d744050d28e00520010520000310F59",
}
APPROVED_MIN = 1e9  # a "set" allowance is effectively unbounded (>> any $ amount)


async def main():
    pk = os.environ.get("POLYMARKET_PRIVATE_KEY")
    proxy = os.environ.get("POLYMARKET_PROXY_ADDRESS")
    if not pk or not proxy:
        print("FAIL: POLYMARKET_PRIVATE_KEY / POLYMARKET_PROXY_ADDRESS not in .env")
        return
    host = os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com")
    sig = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "2"))

    print(f"host={host}  signature_type={sig}")
    c = clob_trade.ClobTradeClient(private_key=pk, proxy_address=proxy, host=host, signature_type=sig)
    signer = await asyncio.to_thread(c._client.get_address)
    print(f"signer EOA : {signer}")
    print(f"proxy/funder: {proxy}")

    bal = await c.get_collateral_balance()
    print(f"\npUSD collateral balance: ${bal:.2f}")

    allow = await c.get_collateral_allowance()
    print(f"raw allowance response: {allow}")
    # interpret: pydantic/dict — find the allowance amount(s)
    amt = None
    if isinstance(allow, dict):
        amt = allow.get("allowance") or allow.get("allowances")
    print(f"collateral allowance (parsed): {amt}")

    print("\n=== PREFLIGHT VERDICT ===")
    funded = bal >= 100.0
    print(f"  funded for $100 probe: {'YES' if funded else f'NO (need +${100-bal:.2f} pUSD)'}")
    try:
        approved = float(amt) >= APPROVED_MIN if amt is not None else False
    except (TypeError, ValueError):
        approved = bool(amt)
    print(f"  collateral allowance set: {'YES' if approved else 'NO / unclear — approve via Polymarket UI'}")
    print("  (NOTE: per-side neg_risk/tick are checked at order time; this is read-only.)")


if __name__ == "__main__":
    asyncio.run(main())
