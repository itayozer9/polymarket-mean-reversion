"""Nightly reconciliation: executor book P&L vs the honest official-label ledger.

Found 2026-08-08: executor_state.json carried det_lwd_live realized_total +$28.23 while
fills.jsonl x official labels gives +$14.27 (a $13.96 silent overstatement; the five other
books reconciled to the cent). The state file drives the daily-loss caps and bankroll
gates, so silent drift there is a real-money risk. This script diffs every book's
realized_total against the honest recomputation and exits nonzero past the allowance, so
nightly_honest.sh (set -e) surfaces it exactly like a fetch failure.

Honest side = same convention as score_gates live mode: labelled non-dry-run fills,
win -> shares*1.0 - usdc, loss -> -usdc. Unlabelled (pending) fills are excluded, which
mirrors the executor's own not-yet-settled pending list; a freshly-settled window can
transiently diverge by one trade, absorbed by the $5 tolerance.

Usage:
  uv run python -m research.analysis.reconcile_executor            # nightly (allowances)
  uv run python -m research.analysis.reconcile_executor --strict   # ignore allowances
"""
from __future__ import annotations
import argparse
import json
import os
import sys

import numpy as np

from research.analysis.score_gates import REPO, load_fills, official_up_map

STATE = os.path.join(REPO, "data", "live", "executor_state.json")
TOLERANCE = 5.0
# Known, explained, frozen divergences (book minus honest). det_lwd_live retired
# 2026-08-08 with this drift on its books; it can no longer grow. Do NOT add a new
# allowance without a ledger entry explaining the mechanism.
ALLOWANCE = {"det_lwd_live": 13.97}


def reconcile(state_path: str = STATE, strict: bool = False) -> list[dict]:
    state = json.loads(open(state_path).read())
    df = load_fills()
    df = df[~df["dry_run"] & df["ok"] & (df["filled_shares"] > 0)].copy()
    up = official_up_map()
    df["official_up"] = df["slug"].map(up)
    df = df.dropna(subset=["official_up"])
    won = (df["side"] == "UP") == (df["official_up"] == 1.0)
    df["pnl"] = np.where(won, df["filled_shares"] * 1.0 - df["usdc_paid"],
                         -df["usdc_paid"])
    honest = df.groupby("strategy_id")["pnl"].sum()

    rows = []
    for sid, book in sorted(state.get("strategies", {}).items()):
        state_total = float(book.get("realized_total", 0.0))
        honest_total = float(honest.get(sid, 0.0))
        div = state_total - honest_total
        allowed = 0.0 if strict else ALLOWANCE.get(sid, 0.0)
        rows.append({"sid": sid, "state": state_total, "honest": honest_total,
                     "divergence": div, "alarm": abs(div - allowed) > TOLERANCE})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--strict", action="store_true",
                    help="ignore the frozen allowances (acceptance / audit runs)")
    args = ap.parse_args(argv)
    rows = reconcile(strict=args.strict)
    bad = [r for r in rows if r["alarm"]]
    for r in rows:
        flag = "  <-- ALARM" if r["alarm"] else ""
        print(f"[reconcile] {r['sid']:24s} book ${r['state']:+9.2f}  "
              f"honest ${r['honest']:+9.2f}  div ${r['divergence']:+7.2f}{flag}")
    if bad:
        print(f"[reconcile] FAIL: {len(bad)} book(s) diverge past allowance+${TOLERANCE:.0f}")
        return 1
    print("[reconcile] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
