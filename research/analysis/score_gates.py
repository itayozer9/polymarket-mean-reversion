"""Registered gate-metric calculator. One tool for every dated gate in test_ledger.md.

Computes the exact metric the ledger registers, so gate day is one command + a decision:
official-settled realized $/fill (and per-$ EV) with a slug-clustered bootstrap CI
([p5,p95] of window_clustered_bootstrap, the ledger's CI convention).

It is a calculator, not a judge: it prints n / EV / CI / WR / fill-rate and the humans
apply the registered pass/kill thresholds. No thresholds are baked in.

live mode  — executor fills.jsonl x official outcomes (the honest ledger, NOT
             settlements.jsonl which runs ~$0.09/win optimistic):
  uv run python -m research.analysis.score_gates live --sid fav_disagree_hi_live \
      --since 2026-07-25 --until 2026-08-07

paper mode — data/research/paper_official/<sid>.parquet slices (label_status=official):
  uv run python -m research.analysis.score_gates paper --sids fav_disagree,fav_disagree_live \
      --since 2026-07-17T17:00 --until 2026-07-26T12:00 --symbol hype
  R2 slug-difference cut: --sids fav_disagree_d5 --minus-sids fav_disagree \
      --ask-band 0.30,0.45   (drops slugs the minus-sids traded in the same window)
  R4 hour cells: --hours 16,17,18,19

All timestamps UTC. Pending-label rows are excluded and counted, never imputed.
"""
from __future__ import annotations
import argparse
import json
import os

import numpy as np
import pandas as pd

from research.dataset.official_outcomes import OUT as OFFICIAL_OUT

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILLS = os.path.join(REPO, "data", "live", "fills.jsonl")
PAPER_ROOT = os.path.join(REPO, "data", "research", "paper_official")


def _ts_ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)


def _cluster_ci(stat_fn, slugs, n=5000, seed=0):
    """Slug-clustered bootstrap 95% CI ([p2.5, p97.5], matching the 07-17 published
    precedent; research.lib.stats.window_clustered_bootstrap returns p5/p95 which is
    a 90% interval, hence the local helper). stat_fn(row_idx) -> scalar."""
    slugs = np.asarray(slugs)
    uniq = np.unique(slugs)
    idx = {g: np.where(slugs == g)[0] for g in uniq}
    rng = np.random.default_rng(seed)
    stats = np.empty(n, "f8")
    for b in range(n):
        rows = np.concatenate([idx[g] for g in rng.choice(uniq, size=len(uniq), replace=True)])
        stats[b] = stat_fn(rows)
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def summarize(pnl, slugs, deployed=None, label="") -> dict:
    n = len(pnl)
    out = {"label": label, "n": n}
    if n == 0:
        return out
    pnl = np.asarray(pnl, "f8")
    ci_lo, ci_hi = _cluster_ci(lambda rows: pnl[rows].mean(), slugs)
    out.update(total=float(pnl.sum()), ev_fill=float(pnl.mean()),
               ci_lo=ci_lo, ci_hi=ci_hi, wr=float((pnl > 0).mean()),
               n_slugs=int(pd.Series(slugs).nunique()))
    if deployed is not None and np.sum(deployed) > 0:
        dep = np.asarray(deployed, "f8")
        out["per_dollar"] = float(pnl.sum() / dep.sum())
        out["pd_ci_lo"], out["pd_ci_hi"] = _cluster_ci(
            lambda rows: pnl[rows].sum() / dep[rows].sum(), slugs)
    return out


def print_summary(s: dict, extra: str = ""):
    if s.get("n", 0) == 0:
        print(f"{s.get('label','')}: n=0 (no rows after filters) {extra}")
        return
    line = (f"{s['label']}: n={s['n']} ({s['n_slugs']} slugs)  total=${s['total']:+.2f}  "
            f"EV/fill=${s['ev_fill']:+.3f}  CI[{s['ci_lo']:+.2f},{s['ci_hi']:+.2f}]  "
            f"WR={s['wr']:.0%}")
    if "per_dollar" in s:
        line += (f"  per-$={s['per_dollar']:+.3f}"
                 f" CI[{s['pd_ci_lo']:+.2f},{s['pd_ci_hi']:+.2f}]")
    print(line + ("  " + extra if extra else ""))


def load_fills() -> pd.DataFrame:
    rows = []
    with open(FILLS) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # torn tail line
    df = pd.DataFrame(rows)
    df["ts_ms"] = (df["ts"] * 1000).astype("int64")
    return df


def official_up_map() -> pd.Series:
    off = pd.read_parquet(os.path.join(REPO, OFFICIAL_OUT))
    return off.dropna(subset=["official_up"]).set_index("slug")["official_up"]


def score_live(args) -> dict:
    df = load_fills()
    m = (df["strategy_id"] == args.sid) & ~df["dry_run"]
    if args.since:
        m &= df["ts_ms"] >= _ts_ms(args.since)
    if args.until:
        m &= df["ts_ms"] < _ts_ms(args.until)
    d = df[m]
    attempts = len(d)
    fills = d[d["ok"] & (d["filled_shares"] > 0)].copy()

    up = official_up_map()
    fills["official_up"] = fills["slug"].map(up)
    pending = int(fills["official_up"].isna().sum())
    fills = fills.dropna(subset=["official_up"])
    won = (fills["side"] == "UP") == (fills["official_up"] == 1.0)
    fills["pnl"] = np.where(won, fills["filled_shares"] * 1.0 - fills["usdc_paid"],
                            -fills["usdc_paid"])

    s = summarize(fills["pnl"].values, fills["slug"].values,
                  deployed=fills["usdc_paid"].values, label=args.sid)
    rate = f"fill-rate={len(fills)+pending}/{attempts}" if attempts else "fill-rate=n/a"
    print_summary(s, extra=f"{rate}  pending={pending}")
    s.update(attempts=attempts, pending=pending)
    return s


def score_paper(args) -> dict:
    frames = [pd.read_parquet(os.path.join(PAPER_ROOT, f"{sid}.parquet"))
              for sid in args.sids.split(",")]
    d = pd.concat(frames, ignore_index=True)
    m = d["label_status"] == "official"
    pending = int((d["label_status"] != "official").sum())
    if args.since:
        m &= d["entry_ts_ms"] >= _ts_ms(args.since)
    if args.until:
        m &= d["entry_ts_ms"] < _ts_ms(args.until)
    if args.symbol:
        m &= d["symbol"].isin(args.symbol.split(","))
    if args.ask_band:
        lo, hi = (float(x) for x in args.ask_band.split(","))
        m &= (d["entry_ask"] >= lo) & (d["entry_ask"] <= hi)
    if args.hours:
        m &= d["utc_hour"].isin([int(h) for h in args.hours.split(",")])
    d = d[m]
    if args.minus_sids:
        minus = pd.concat([pd.read_parquet(os.path.join(PAPER_ROOT, f"{sid}.parquet"))
                           for sid in args.minus_sids.split(",")], ignore_index=True)
        mm = pd.Series(True, index=minus.index)
        if args.since:
            mm &= minus["entry_ts_ms"] >= _ts_ms(args.since)
        if args.until:
            mm &= minus["entry_ts_ms"] < _ts_ms(args.until)
        d = d[~d["slug"].isin(set(minus[mm]["slug"]))]

    s = summarize(d["pnl_official"].values, d["slug"].values, label=args.sids)
    print_summary(s, extra=f"(pending excluded upstream: {pending})")
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    pl = sub.add_parser("live", help="executor fills.jsonl x official outcomes")
    pl.add_argument("--sid", required=True)
    pp = sub.add_parser("paper", help="paper_official parquet slice")
    pp.add_argument("--sids", required=True, help="comma-separated strategy ids to pool")
    pp.add_argument("--minus-sids", help="drop slugs these sids traded in the same window")
    pp.add_argument("--symbol", help="comma-separated symbols, e.g. hype")
    pp.add_argument("--ask-band", help="lo,hi inclusive on entry_ask, e.g. 0.30,0.45")
    pp.add_argument("--hours", help="comma-separated UTC hours, e.g. 16,17,18,19")
    for p in (pl, pp):
        p.add_argument("--since", help="UTC entry-time lower bound (inclusive)")
        p.add_argument("--until", help="UTC entry-time upper bound (exclusive)")
    args = ap.parse_args(argv)
    return score_live(args) if args.mode == "live" else score_paper(args)


if __name__ == "__main__":
    main()
