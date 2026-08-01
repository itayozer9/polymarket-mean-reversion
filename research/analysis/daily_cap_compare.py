"""Compare daily-loss-cap modes on the stale-quote edge over the clean window.

Replays the real sq trade ledger (data/research/ledgers/sq_{dev,holdout}.parquet,
built by loss_patterns.py) through the REDESIGNED DailyLossGuard (imported from the
live engine) in each mode, and reports the EV-vs-safety tradeoff:

    none(uncapped)        -- baseline, no cap
    soft_settled          -- today's leaky cap (settled-only, re-arms, open-blind)
    hard_worstcase        -- open-exposure reservation, no latch (resume when safe)
    hard_worstcase_latch  -- same hard bound + stop-for-the-day latch

For each: trades taken/skipped, total PnL, EV/trade, worst single UTC-day realized
PnL, and how many UTC days breach the nominal -cap (should be 0 for hard modes).
The guard is the exact engine class, so this predicts live behaviour.

Run: uv run python -m research.analysis.daily_cap_compare [cap_usd]
Spec: docs/research/daily_cap_redesign.md
"""
from __future__ import annotations
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "src"))
from mean_reversion_live.engine.determinism_state import DailyLossGuard  # noqa: E402

STAKE = 10.0
FEE_RATE = 0.07
GATE = STAKE * (1.0 + FEE_RATE)   # engine would_block reservation (ask unknown at the gate)
LEDGERS = os.path.join(REPO, "data", "research", "ledgers")


def _utc_day(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def _load() -> pd.DataFrame:
    parts = []
    for split in ("dev", "holdout"):
        p = os.path.join(LEDGERS, f"sq_{split}.parquet")
        if os.path.exists(p):
            d = pd.read_parquet(p)
            d["split"] = split
            parts.append(d)
    if not parts:
        raise SystemExit("no sq ledgers — run: uv run python -m research.analysis.loss_patterns")
    df = pd.concat(parts, ignore_index=True)
    ws = df["window_start_ts"].astype("int64")
    df["ws_ms"] = np.where(ws < 1_000_000_000_000, ws * 1000, ws)
    df["entry_ts"] = df["ws_ms"] + df["entry_sec"].astype("int64") * 1000
    df["exit_ts"] = df["ws_ms"] + 900 * 1000
    # exact engine on_entry reservation: STAKE + entry fee = STAKE*(1 + fee*(1-ask))
    df["max_loss"] = STAKE + FEE_RATE * (1.0 - df["entry_ask"]) * STAKE
    return df.sort_values("entry_ts").reset_index(drop=True)


def simulate(df: pd.DataFrame, cap: float, mode):
    guard = DailyLossGuard(max_daily_loss_usd=cap, mode=mode) if mode else None
    # event timeline: settle(0) before entry(1) at equal ts (resolutions land first)
    ev = []
    for i in range(len(df)):
        ev.append((int(df.entry_ts[i]), 1, i))
        ev.append((int(df.exit_ts[i]), 0, i))
    ev.sort(key=lambda e: (e[0], e[1]))

    taken_ids = set()
    realized_by_day: dict = {}
    n_taken = n_skip = 0
    for ts, kind, i in ev:
        slug = df.slug[i]
        if kind == 1:  # entry decision
            if guard is None or not guard.would_block(ts, GATE):
                if guard is not None:
                    guard.on_entry(ts, slug, float(df.max_loss[i]))
                taken_ids.add(i)
                n_taken += 1
            else:
                n_skip += 1
        else:          # settle (only books PnL if the entry was taken)
            if i in taken_ids:
                pnl = float(df.pnl[i])
                if guard is not None:
                    guard.on_settle(ts, slug, pnl)
                day = _utc_day(ts)
                realized_by_day[day] = realized_by_day.get(day, 0.0) + pnl

    total = sum(realized_by_day.values())
    worst_day = min(realized_by_day.values()) if realized_by_day else 0.0
    breach_days = sum(1 for v in realized_by_day.values() if cap and v < -cap - 1e-6)
    ev_tr = total / n_taken if n_taken else 0.0
    return dict(mode=(mode or "none(uncapped)"), taken=n_taken, skipped=n_skip,
                total=total, ev_tr=ev_tr, worst_day=worst_day,
                n_days=len(realized_by_day), breach_days=breach_days)


def main():
    cap = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
    df = _load()
    ndays = df["entry_ts"].map(_utc_day).nunique()
    print(f"sq ledger: n={len(df)} trades over {ndays} UTC days "
          f"({_utc_day(int(df.entry_ts.min()))}..{_utc_day(int(df.entry_ts.max()))})  "
          f"cap=${cap:.0f}/day\n")
    modes = (None, "soft_settled", "hard_worstcase", "hard_worstcase_latch")
    rows = [simulate(df, cap, m) for m in modes]
    hdr = (f"{'mode':22} {'taken':>6} {'skip':>5} {'total$':>9} {'$/tr':>7} "
           f"{'worstDay$':>10} {'breachDays':>11}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['mode']:22} {r['taken']:>6} {r['skipped']:>5} {r['total']:>+9.1f} "
              f"{r['ev_tr']:>+7.2f} {r['worst_day']:>+10.1f} {r['breach_days']:>11}")
    print(f"\nworstDay$ = most-negative single UTC-day realized PnL.  breachDays = days "
          f"realized < -${cap:.0f}\n(should be 0 for hard modes; >0 for soft_settled if the "
          f"cap leaked). EV cost of safety = (uncapped $/tr) - (hard $/tr).")


if __name__ == "__main__":
    main()
