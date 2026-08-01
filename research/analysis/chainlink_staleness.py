"""chainlink_staleness (WP2) — is a STALE settlement oracle near expiry predictable?

Polymarket settles on Chainlink (cl_up = cl_end >= cl_start, asof window open/close).
The feed is stale a lot: oracle_age_s median 16s, >15s 50% of the time, >30s 7%.
Hypothesis: near window end, when the last Chainlink value is OLD and live (Coinbase)
spot has moved past it, the SETTLEMENT tends to reflect the stale oracle, not live
spot — and the BOOK (which tracks live spot) is on the other side. Buy the stale-CL
side, hold to settle. Latency-proof: decide with a time buffer, hold.

DISTINCTNESS GATES (this must NOT be the dead basis/determinism edge):
  1. EV must CONCENTRATE in the stale tail (age buckets <15 / 15-30 / >30s); if flat
     across age, it's just "buy the CL-implied side" = determinism in disguise -> kill.
  2. feed-update-before-end rate: how often does CL change between decision and close?
     If it usually updates, the stale value is NOT the settlement and there's no edge.
  3. Jaccard vs det / e4 known edges < ~0.5.

Reuses edge_lab (settlement+CIs+latency), oracle_settlement_gap.asof_chainlink,
resettle_chainlink.chainlink_outcome_by_slug, hypothesis_verify._known_edges/_jaccard.

Run: uv run python -m research.analysis.chainlink_staleness
Out: data/research/hypotheses/chainlink_staleness.jsonl
"""
from __future__ import annotations
import glob
import json
import os

import numpy as np
import pandas as pd

from research.analysis.edge_lab import load_base, first_tick, simulate, evaluate, latency_survival
from research.analysis.oracle_settlement_gap import asof_chainlink
from research.analysis.hypothesis_verify import _known_edges, _jaccard

OUT = os.path.join("data", "research", "hypotheses", "chainlink_staleness.jsonl")


def _load_cl_age():
    parts = []
    for f in glob.glob("data/live_chainlink/*.csv.gz"):
        try:
            d = pd.read_csv(f, usecols=["timestamp_ms", "symbol", "price", "oracle_age_s"])
            parts.append(d)
        except Exception:
            pass
    cl = pd.concat(parts, ignore_index=True)
    cl = cl[(cl["price"] > 0) & cl["timestamp_ms"].notna()].copy()
    cl["symbol"] = cl["symbol"].str.lower()
    return cl.sort_values("timestamp_ms").reset_index(drop=True)


def _asof_age(cl, queries, t_col, px_col, age_col):
    """latest CL price + its oracle_age_s as-of t_col, per symbol."""
    out = []
    for sym, g in queries.sort_values(t_col).groupby("symbol"):
        c = cl[cl["symbol"] == sym][["timestamp_ms", "price", "oracle_age_s"]]
        m = pd.merge_asof(g.sort_values(t_col), c, left_on=t_col, right_on="timestamp_ms",
                          direction="backward", tolerance=120_000)
        out.append(m.rename(columns={"price": px_col, "oracle_age_s": age_col}))
    return pd.concat(out, ignore_index=True)


def build_decisions(b, cl, t_lo=30, t_hi=90):
    """one decision row per window in the [t_lo,t_hi] time-left band, with the
    chainlink staleness features computed by asof-merge."""
    band = b[(b["time_left_sec"] >= t_lo) & (b["time_left_sec"] <= t_hi) & b["book_healthy"]]
    dec = (band.sort_values(["slug", "seconds_into_window"])
           .groupby("slug", as_index=False).first())
    dec["t_dec_ms"] = (dec["window_start_ts"] + dec["seconds_into_window"]) * 1000
    dec["t_start_ms"] = dec["window_start_ts"] * 1000
    dec["t_end_ms"] = (dec["window_start_ts"] + 900) * 1000
    dec = _asof_age(cl, dec, "t_dec_ms", "cl_dec", "cl_age")
    dec = asof_chainlink(cl, dec, "t_start_ms", "cl_start")
    dec = asof_chainlink(cl, dec, "t_end_ms", "cl_end")
    dec = dec[dec["cl_dec"].notna() & dec["cl_start"].notna() & dec["cl_end"].notna()].copy()
    dec["stale_up"] = (dec["cl_dec"] >= dec["cl_start"]).astype(int)
    dec["cl_up"] = (dec["cl_end"] >= dec["cl_start"]).astype(int)
    dec["book_up"] = (dec["yes_mid"] >= 0.5).astype(int)
    dec["gap_bps"] = (dec["cb_spot"] / dec["cl_dec"] - 1.0) * 1e4
    # feed changed between decision and end? (the stale value did NOT carry to settle)
    dec["feed_changed"] = (dec["cl_end"] != dec["cl_dec"]).astype(int)
    return dec


def run_rule(dec, age_min, gap_min, require_disagree=True):
    d = dec[(dec["cl_age"] >= age_min) & (dec["gap_bps"].abs() >= gap_min)]
    if require_disagree:
        d = d[d["book_up"] != d["stale_up"]]
    if len(d) < 20:
        return None, d
    decision = d.rename(columns={"seconds_into_window": "entry_sec"})
    decision["buy_yes"] = d["stale_up"].astype(bool).to_numpy()
    keep = ["slug", "symbol", "date", "split", "window_start_ts", "entry_sec", "buy_yes"]
    led = simulate(decision[keep], latency=2)
    if led is None or len(led) < 20:
        return None, d
    ev = evaluate(led, n_trials=200)
    lat = latency_survival(decision[keep], latencies=(2, 3, 5, 10))
    return dict(age_min=age_min, gap_min=gap_min, disagree=require_disagree,
                n=int(len(led)), ev=ev["ev"], wr=ev["wr"], total=ev["total"],
                per_split=ev["per_split"], cpcv=ev["cpcv"].get("pct_pos"),
                lat={str(k): v for k, v in lat.items()}), d


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    b = load_base()
    cl = _load_cl_age()
    dec = build_decisions(b, cl, t_lo=30, t_hi=90)
    print(f"windows with CL features in [30,90]s band: {len(dec)}")
    print(f"feed-changed-before-end rate: {dec['feed_changed'].mean():.3f}  "
          f"(if high, the stale value rarely IS the settlement)")
    # base rate: how often stale_up predicts cl_up at all (unconditional)
    print(f"stale_up == cl_up overall: {(dec['stale_up']==dec['cl_up']).mean():.3f}")
    dis = dec[dec['book_up'] != dec['stale_up']]
    print(f"book-disagrees-stale windows: {len(dis)}; among them stale_up==cl_up: "
          f"{(dis['stale_up']==dis['cl_up']).mean():.3f}")

    # DISTINCTNESS GATE 1: age-bucket monotonicity (on disagree windows, taker-settled)
    print("\nage-bucket EV (disagree, buy stale side, $10 taker, Chainlink):")
    rows = []
    for lo, hi, lab in [(0, 15, "<15s"), (15, 30, "15-30s"), (30, 999, ">30s")]:
        sub = dis[(dis["cl_age"] >= lo) & (dis["cl_age"] < hi)]
        if len(sub) < 10:
            print(f"  {lab:8} n={len(sub)} (too few)"); continue
        decision = sub.rename(columns={"seconds_into_window": "entry_sec"})
        decision["buy_yes"] = sub["stale_up"].astype(bool).to_numpy()
        keep = ["slug", "symbol", "date", "split", "window_start_ts", "entry_sec", "buy_yes"]
        led = simulate(decision[keep], latency=2)
        if len(led):
            print(f"  {lab:8} n={len(led):>4} EV ${led['pnl'].mean():+.3f} WR{led['won'].mean()*100:.0f}%")

    # grid of rules + Jaccard vs known edges
    known = _known_edges(b)
    results = []
    with open(OUT, "w") as f:
        f.write(json.dumps(dict(meta="diag", feed_changed_rate=float(dec["feed_changed"].mean()),
                                stale_eq_clup=float((dec['stale_up']==dec['cl_up']).mean()),
                                n_band=int(len(dec)))) + "\n")
        print("\ngrid (rule -> Chainlink EV per split):")
        for age_min in (0, 15, 30):
            for gap_min in (5, 10, 20, 40):
                r, d = run_rule(dec, age_min, gap_min, require_disagree=True)
                if r is None:
                    continue
                sl = set(d["slug"].unique())
                r["jaccard"] = {k: _jaccard(sl, v) for k, v in known.items()}
                results.append(r)
                f.write(json.dumps(r) + "\n")
                ps = r["per_split"]; fu = ps.get("future") or {}
                l5 = (r["lat"].get("5") or {})
                print(f"  age>={age_min:>2} gap>={gap_min:>2} n={r['n']:>4} "
                      f"EV ${r['ev']:+.2f} WR{r['wr']:.0f} | future ${fu.get('ev','-')}"
                      f"[{fu.get('lo','-')},{fu.get('hi','-')}]n{fu.get('n','-')} "
                      f"| lat5 ${l5.get('ev','-')} | maxJac {max(r['jaccard'].values()):.2f}")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
