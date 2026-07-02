"""Re-settle every paper ledger on the OFFICIAL on-chain outcome — the honest record.

The live paper engine still settles on reconstructed Chainlink (paper_engine.py:181,
deliberately unchanged), which is ~4:1 optimistic near-strike vs the official resolution.
This module streams each strategy's trades_detailed.jsonl, joins the official label
(data/research/official_outcomes.parquet via official_only_by_slug — works for 15m AND 5m
slugs), recomputes won/pnl, and writes the honest per-trade ledgers + a scoreboard.
Trades whose slug has no official label yet are marked pending, never imputed.

Eras (entry_ts_ms):  devhold < 06-05 | degraded 06-05..06-12 11:00 UTC (excluded from
scoring) | clean 06-12 11:00..06-19 | virgin >= 06-19 (never revealed to any sweep).

Run:  uv run python -m research.analysis.resettle_official [--sids a,b,...]
Out:  data/research/paper_official/<sid>.parquet
      data/research/paper_official/daily_scores.parquet
      data/research/paper_official/scoreboard.md
"""
from __future__ import annotations
import argparse
import json
import os

import numpy as np
import pandas as pd

from research.lib.stats import window_clustered_bootstrap
from research.dataset.official_outcomes import official_only_by_slug

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSONL_ROOT = os.path.join(REPO, "data", "jsonl")
OUT_ROOT = os.path.join(REPO, "data", "research", "paper_official")

# Era boundaries, UTC ms (see docs/research/test_ledger.md data-quality epochs).
T_DEGRADED = int(pd.Timestamp("2026-06-05", tz="UTC").timestamp() * 1000)
T_CLEAN = int(pd.Timestamp("2026-06-12 11:00", tz="UTC").timestamp() * 1000)
T_VIRGIN = int(pd.Timestamp("2026-06-19", tz="UTC").timestamp() * 1000)


def era_of(entry_ts_ms: pd.Series) -> pd.Series:
    return pd.cut(entry_ts_ms,
                  bins=[0, T_DEGRADED, T_CLEAN, T_VIRGIN, np.inf],
                  labels=["devhold", "degraded", "clean", "virgin"],
                  right=False)


def load_trades_detailed(sid: str, root: str = JSONL_ROOT) -> pd.DataFrame:
    path = os.path.join(root, sid, "trades_detailed.jsonl")
    if not os.path.exists(path):
        return pd.DataFrame()
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # torn tail line from a crash mid-write
    return pd.DataFrame(rows)


def resettle_sid(sid: str, official: pd.DataFrame, root: str = JSONL_ROOT) -> pd.DataFrame:
    """Honest per-trade ledger for one strategy. official: [slug, official_up]."""
    t = load_trades_detailed(sid, root)
    if t.empty:
        return t
    need = {"slug", "side", "entry_ts_ms", "entry_price", "shares", "fee_total", "pnl", "won"}
    missing = need - set(t.columns)
    if missing:
        raise ValueError(f"{sid}: trades_detailed missing columns {sorted(missing)}")
    t = t.merge(official.rename(columns={"official_up": "official_up"}), on="slug", how="left")
    has = t["official_up"].notna()
    bought_up = t["side"].astype(str).str.upper().isin(["UP", "YES"])
    t["won_official"] = np.where(
        has, np.where(bought_up, t["official_up"] == 1, t["official_up"] == 0), np.nan)
    stake = t["shares"] * t["entry_price"]
    t["pnl_official"] = np.where(
        t["won_official"] == 1, t["shares"] * (1.0 - t["entry_price"]) - t["fee_total"],
        -stake - t["fee_total"])
    t.loc[~has, "pnl_official"] = np.nan
    t["label_status"] = np.where(has, "official", "pending")
    t["era"] = era_of(t["entry_ts_ms"])
    t["utc_date"] = pd.to_datetime(t["entry_ts_ms"], unit="ms", utc=True).dt.date.astype(str)
    t["strategy_id"] = sid
    return t


def _ci(sub: pd.DataFrame) -> tuple[float, float, float]:
    if len(sub) < 2:
        v = sub["pnl_official"].mean() if len(sub) else float("nan")
        return (float("nan"), float(v) if pd.notna(v) else float("nan"), float("nan"))
    return window_clustered_bootstrap(sub["pnl_official"].values, sub["slug"].values)


def main() -> None:
    ap = argparse.ArgumentParser(description="Honest re-settle of paper ledgers")
    ap.add_argument("--sids", default=None, help="comma list; default = every dir in data/jsonl")
    ap.add_argument("--jsonl-root", default=JSONL_ROOT)
    ap.add_argument("--out-root", default=OUT_ROOT)
    args = ap.parse_args()

    official = official_only_by_slug()
    sids = (args.sids.split(",") if args.sids
            else sorted(d for d in os.listdir(args.jsonl_root)
                        if os.path.isdir(os.path.join(args.jsonl_root, d))))
    os.makedirs(args.out_root, exist_ok=True)

    daily, board = [], []
    board.append("# Honest scoreboard — paper ledgers re-settled on OFFICIAL outcomes\n")
    board.append(f"Generated over {len(official):,} official labels. "
                 "Engine pnl = recon-Chainlink (biased); official = money truth. "
                 "`degraded` era excluded from all verdicts.\n")
    board.append("| sid | era | n | pending | EV/fill official | CI5 | CI95 | EV/fill engine | inflation |")
    board.append("|---|---|--:|--:|--:|--:|--:|--:|--:|")

    for sid in sids:
        t = resettle_sid(sid, official, args.jsonl_root)
        if t.empty:
            continue
        t.to_parquet(os.path.join(args.out_root, f"{sid}.parquet"), index=False)
        lab = t[t["label_status"] == "official"]
        g = lab.groupby(["utc_date"], observed=True)
        d = g.agg(n=("pnl_official", "size"), pnl_official=("pnl_official", "sum"),
                  pnl_engine=("pnl", "sum"), wins=("won_official", "sum")).reset_index()
        d["strategy_id"] = sid
        daily.append(d)
        for era in ["clean", "virgin"]:
            sub = lab[lab["era"] == era]
            n_pend = int((t["era"] == era).sum() - len(sub))
            if not len(sub):
                continue
            lo, mid, hi = _ci(sub)
            ev_eng = sub["pnl"].mean()
            board.append(f"| {sid} | {era} | {len(sub)} | {n_pend} | "
                         f"{sub['pnl_official'].mean():+.3f} | {lo:+.3f} | {hi:+.3f} | "
                         f"{ev_eng:+.3f} | {ev_eng - sub['pnl_official'].mean():+.3f} |")
        print(f"[resettle_official] {sid}: {len(t)} trades, "
              f"{(t['label_status'] == 'pending').sum()} pending")

    if daily:
        pd.concat(daily, ignore_index=True).to_parquet(
            os.path.join(args.out_root, "daily_scores.parquet"), index=False)
    with open(os.path.join(args.out_root, "scoreboard.md"), "w") as f:
        f.write("\n".join(board) + "\n")
    print(f"[resettle_official] wrote {args.out_root}/scoreboard.md")


if __name__ == "__main__":
    main()
