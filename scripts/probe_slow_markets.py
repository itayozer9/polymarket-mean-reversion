#!/usr/bin/env python3
"""Weekly harvest of RESOLVED non-crypto Polymarket markets, with pre-close prices.

WHY THIS EXISTS (2026-08-28 decision, option B): every edge this project tested lived in
short-dated crypto Up/Down, where the counterparty is another bot racing the same spot
feed. That adverse selection killed all of them. "Will X happen by date Y" markets have
no feed to race, so the mechanism that killed us does not exist there. It is the one
untested space.

It cannot be backtested: Polymarket's CLOB `prices-history` is PURGED after ~4 weeks
(verified 2026-08-28 — markets ending 08-01..08-28 return history, 07-01..08-01 return
none). So there is no back-data. The only way to get an answer is to harvest forward,
weekly, before the history is deleted.

Run weekly (cron). Appends to data/research/slow_markets.parquet, deduped on slug.
After ~12 weeks there is enough to read a calibration curve; until then it is collection.

  uv run python scripts/probe_slow_markets.py            # harvest + print running read
  uv run python scripts/probe_slow_markets.py --report   # read only, no network
  uv run python scripts/probe_slow_markets.py --selfcheck
"""
from __future__ import annotations
import json, os, sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "data", "research", "slow_markets.parquet")
GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com/prices-history"
# Lead times sampled before each market's last traded price.
LEADS = [("h1", 3600), ("h6", 6 * 3600), ("h24", 86400), ("d3", 3 * 86400), ("d7", 7 * 86400)]
S = requests.Session()


def price_at(history: list[dict], t_end: int, secs: int) -> float:
    """Last traded price at or before `secs` seconds before t_end. NaN if none."""
    past = [x for x in history if x["t"] <= t_end - secs]
    return float(past[-1]["p"]) if past else float("nan")


def list_markets(weeks: int = 5) -> list[dict]:
    """Resolved markets from the last `weeks` weeks. Gamma caps offset at 2500, so page
    inside one-week windows rather than over the whole range."""
    rows, today = [], pd.Timestamp.utcnow().normalize()
    for k in range(weeks):
        hi, lo = today - pd.Timedelta(weeks=k), today - pd.Timedelta(weeks=k + 1)
        for off in range(0, 2400, 100):
            try:
                r = S.get(GAMMA, timeout=40, params={
                    "closed": "true", "limit": 100, "offset": off,
                    "end_date_min": lo.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_date_max": hi.strftime("%Y-%m-%dT%H:%M:%SZ")}).json()
            except Exception as e:
                print(f"  gamma {lo.date()} offset {off}: {e}")
                break
            if not isinstance(r, list) or not r:
                break
            rows += r
            if len(r) < 100:
                break
    return [m for m in rows if "updown" not in (m.get("slug") or "") and m.get("clobTokenIds")]


def harvest_one(m: dict) -> dict | None:
    try:
        op = m.get("outcomePrices")
        op = json.loads(op) if isinstance(op, str) else op
        won = float(op[0])
        if won not in (0.0, 1.0):
            return None                      # void / ambiguous resolution
        tk = m.get("clobTokenIds")
        toks = json.loads(tk) if isinstance(tk, str) else tk
        r = S.get(CLOB, params={"market": toks[0], "interval": "max", "fidelity": 60}, timeout=25)
        if r.status_code != 200:
            return None
        h = r.json().get("history", [])
        if len(h) < 6:
            return None                      # purged, or never traded
        t_end = h[-1]["t"]
        out = {"slug": m["slug"], "question": (m.get("question") or "")[:120],
               "won": won, "vol": float(m.get("volumeNum") or 0),
               "end_ts": t_end, "n_pts": len(h),
               "span_h": round((t_end - h[0]["t"]) / 3600, 1)}
        out.update({lab: price_at(h, t_end, secs) for lab, secs in LEADS})
        return out
    except Exception:
        return None


def harvest() -> pd.DataFrame:
    mk = list_markets()
    print(f"resolved non-updown markets in window: {len(mk)}")
    rows = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        for i, r in enumerate(ex.map(harvest_one, mk)):
            if r:
                rows.append(r)
            if i % 300 == 0:
                print(f"\r  {i}/{len(mk)} with history: {len(rows)}", end="", flush=True)
    print(f"\r  {len(mk)}/{len(mk)} with history: {len(rows)}")
    new = pd.DataFrame(rows)
    if os.path.exists(OUT):
        old = pd.read_parquet(OUT)
        before = len(old)
        new = pd.concat([old, new], ignore_index=True).drop_duplicates("slug", keep="first")
        print(f"  merged: {before} existing + {len(rows)} fetched -> {len(new)} unique")
    new.to_parquet(OUT, index=False)
    return new


def report(d: pd.DataFrame) -> None:
    """Calibration: at each lead time, does the price match how often it won?
    ev_per_$ > 0 means that price band was underpriced (buying it made money)."""
    if d.empty:
        print("no data yet")
        return
    span = pd.to_datetime(d.end_ts, unit="s", utc=True)
    print(f"\n{len(d)} markets  {span.min():%Y-%m-%d}..{span.max():%Y-%m-%d}  "
          f"median volume ${d.vol.median():,.0f}")
    for tier, vmin in [("ALL", 0), ("liquid (vol>$50k)", 50_000)]:
        x = d[d.vol >= vmin]
        print(f"\n--- {tier}: EV per $1 buying at that price, % ---")
        cols = {}
        for lab, _ in LEADS:
            y = x.dropna(subset=[lab])
            y = y[(y[lab] > 0.01) & (y[lab] < 0.99)]
            if len(y) < 40:
                continue
            b = pd.cut(y[lab], [0, .3, .7, .9, 1.0])
            cols[lab] = y.groupby(b, observed=True).apply(
                lambda g: pd.Series({"n": len(g), "ev%": 100 * (g.won.sum() / g[lab].sum() - 1)}),
                include_groups=False).round(1).stack()
        print(pd.DataFrame(cols).to_string() if cols else "  too few rows to read")
    print("\nNOTE: needs ~12 weeks before this is worth reading. Until then it is collection.")


def selfcheck() -> None:
    h = [{"t": 1000, "p": 0.2}, {"t": 5000, "p": 0.5}, {"t": 9000, "p": 0.9}]
    assert price_at(h, 9000, 8000) == 0.2, "should take the last point at/before the cut"
    assert price_at(h, 9000, 4000) == 0.5, "boundary is inclusive (t <= t_end - secs)"
    assert np.isnan(price_at(h, 9000, 99999)), "no point that early -> NaN"
    assert price_at(h, 9000, 0) == 0.9, "zero lead -> the final point"
    print("selfcheck OK")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        selfcheck()
    elif "--report" in sys.argv:
        report(pd.read_parquet(OUT) if os.path.exists(OUT) else pd.DataFrame())
    else:
        report(harvest())
