"""One-shot backfill of MISSING settlements into the live executor's per-strategy books.

WHY: per-strategy settlement booking + the settlements ledger only started 2026-06-08 ~12:19.
Filled windows that resolved BEFORE that (06-05 -> 06-08 morning) were never folded into any
strategy's `realized_total`, so the balance the status table shows UNDERSTATES true P&L. This
script books each of those windows from the executor's OWN fill records (fills.jsonl: actual
filled_shares + usdc_paid, per strategy_id) and the WIN/LOSS confirmed by the Polymarket
data-api (redeem activity / position curPrice), using the SAME pnl convention the executor uses
for live settlements (win -> shares - usdc ; loss -> -usdc).

SAFETY:
  * REFUSES to run with --execute while the live executor is alive (it would clobber the edit).
  * Backs up executor_state.json + settlements.jsonl before writing.
  * Idempotent: a window already in settlements.jsonl or in a book's `pending` is skipped, so
    re-running never double-books.
  * Only mutates `realized_total` (the balance/bankroll driver). It does NOT touch
    `realized_by_day` — that drives the live per-UTC-day loss cap, and retroactively injecting
    old P&L into today's bucket could perturb a risk control mid-day. Appends audit lines to
    settlements.jsonl (marked "backfill": true) at each window's true end time.

Run:
  uv run python scripts/backfill_settlements.py            # dry-run (no writes)
  # stop executor first, then:
  uv run python scripts/backfill_settlements.py --execute
"""
from __future__ import annotations
import argparse
import asyncio
import datetime as dt
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import aiohttp

from mean_reversion_live.clients.data_api import fetch_activity, fetch_positions

REPO = Path(__file__).resolve().parents[1]
STATE = REPO / "data" / "live" / "executor_state.json"
FILLS = REPO / "data" / "live" / "fills.jsonl"
SETTLE = REPO / "data" / "live" / "settlements.jsonl"
LEGACY_OWNER = "det_lwd_live"


def _proxy() -> str:
    for ln in open(REPO / ".env"):
        m = re.match(r'\s*POLYMARKET_PROXY_(?:ADDRESS|WALLET)\s*=\s*["\']?([0-9a-fA-Fx]+)', ln)
        if m:
            return m.group(1)
    raise SystemExit("no POLYMARKET_PROXY_ADDRESS in .env")


def _executor_alive() -> bool:
    r = subprocess.run(["pgrep", "-f", "live_executor.py --live"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _window_end(slug: str) -> int:
    return int(slug.rsplit("-", 1)[-1]) + 900


def _utc_day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def _atomic_write_json(path: Path, obj) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


async def _win_loss_by_slug(proxy: str):
    """Return (redeem_by_slug, curpx_by_slug) from the Polymarket data-api — the same ground
    truth the status cross-check uses."""
    async with aiohttp.ClientSession() as s:
        # No max_records: a truncated history strands old REDEEMs, and this
        # function reads "no redeem" as "the market lost" — capping it would
        # backfill real wins as losses.
        acts = await fetch_activity(s, proxy)
        pos = await fetch_positions(s, proxy)
    redeem = {}
    for a in acts:
        if a.slug and a.type in ("REDEEM", "MERGE"):
            redeem[a.slug] = redeem.get(a.slug, 0.0) + a.usdc_size
    curpx = {p.get("slug", ""): float(p.get("curPrice") or 0) for p in pos if p.get("slug")}
    return redeem, curpx


def main(execute: bool):
    if execute and _executor_alive():
        raise SystemExit("REFUSING: live executor is running — stop it first (it would clobber "
                         "the edit). Kill `live_executor.py --live`, run --execute, then relaunch.")
    state = json.loads(STATE.read_text())
    if "strategies" not in state:
        raise SystemExit("state is not v2 (per-strategy) — migrate first by starting the executor once")
    fills = [json.loads(l) for l in FILLS.read_text().splitlines() if l.strip()]
    setts = [json.loads(l) for l in SETTLE.read_text().splitlines() if l.strip()] if SETTLE.exists() else []

    booked = {(x.get("strategy_id") or LEGACY_OWNER, x["slug"]) for x in setts}
    for sid, b in state["strategies"].items():
        for p in (b.get("pending") or []):
            booked.add((sid, p["slug"]))

    agg = {}
    for f in fills:
        if not f.get("ok"):
            continue
        k = (f.get("strategy_id") or LEGACY_OWNER, f["slug"])
        a = agg.setdefault(k, {"shares": 0.0, "usdc": 0.0, "side": f.get("side")})
        a["shares"] += float(f.get("filled_shares", 0))
        a["usdc"] += float(f.get("usdc_paid", 0))

    proxy = _proxy()
    redeem, curpx = asyncio.run(_win_loss_by_slug(proxy))

    def won(slug):
        if redeem.get(slug, 0) > 0:
            return True
        if slug in curpx and curpx[slug] <= 0.01:
            return False
        if slug in curpx and curpx[slug] >= 0.99:
            return True
        return None

    missing = sorted(((k, v) for k, v in agg.items() if k not in booked), key=lambda kv: kv[0][1])
    new_lines, per = [], {}
    unknown = []
    for (sid, slug), v in missing:
        w = won(slug)
        if w is None:
            unknown.append(slug)
            continue
        pnl = (v["shares"] - v["usdc"]) if w else (-v["usdc"])
        wend = _window_end(slug)
        per.setdefault(sid, {"pnl": 0.0, "n": 0, "w": 0, "l": 0})
        per[sid]["pnl"] += pnl
        per[sid]["n"] += 1
        per[sid]["w"] += 1 if w else 0
        per[sid]["l"] += 0 if w else 1
        new_lines.append({"ts": float(wend), "window_end": wend, "strategy_id": sid,
                          "slug": slug, "side": v["side"], "outcome": ("UP" if (w == (v["side"] == "UP")) else v["side"]),
                          "won": w, "usdc": round(v["usdc"], 6), "shares": round(v["shares"], 6),
                          "pnl": round(pnl, 6), "backfill": True})

    mode = "EXECUTE" if execute else "DRY-RUN"
    print(f"[{mode}] backfill candidates: {len(missing)}  classifiable: {len(new_lines)}  unknown: {len(unknown)}")
    for sid in sorted(set(list(per) + list(state["strategies"]))):
        cur = float(state["strategies"].get(sid, {}).get("realized_total", 0.0))
        bf = per.get(sid, {"pnl": 0.0, "n": 0, "w": 0, "l": 0})
        print(f"  {sid:18} realized_now={cur:+.2f}  +backfill={bf['pnl']:+.2f} "
              f"({bf['n']}: {bf['w']}W/{bf['l']}L)  -> realized_after={cur + bf['pnl']:+.2f}")
    if unknown:
        print(f"  UNKNOWN (left unbooked, investigate): {unknown}")

    if not execute:
        print("  (dry-run — no writes. Stop the executor and re-run with --execute to apply.)")
        return

    # backups
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (STATE.with_suffix(f".json.bak.{stamp}")).write_text(STATE.read_text())
    if SETTLE.exists():
        (SETTLE.with_suffix(f".jsonl.bak.{stamp}")).write_text(SETTLE.read_text())

    # apply: realized_total only (NOT realized_by_day) + append audit ledger lines
    for sid, bf in per.items():
        state["strategies"].setdefault(sid, {}).setdefault("realized_total", 0.0)
        state["strategies"][sid]["realized_total"] = round(
            float(state["strategies"][sid]["realized_total"]) + bf["pnl"], 6)
    _atomic_write_json(STATE, state)
    with open(SETTLE, "a") as f:
        for ln in new_lines:
            f.write(json.dumps(ln) + "\n")
    print(f"  WROTE: {len(new_lines)} settlement lines + updated realized_total. Backups: *.bak.{stamp}")
    print("  Now relaunch the executor; it will load the corrected balances.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply (default: dry-run)")
    main(ap.parse_args().execute)
