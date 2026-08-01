"""maker_execution (WP1) — does RESTING (maker) instead of CROSSING (taker) keep our
already-gross-positive edges net-positive, after adverse selection?

Every edge we have is cost-killed: taker fee + half-spread eats 50-85% of it. A
patient MAKER rests a bid, pays no fee, earns a rebate, and enters cheaper — and
being patient is the ultimate "no speed" approach. The catch is ADVERSE SELECTION:
a resting bid fills precisely when the side is being sold down (often the loser).
We measure that directly.

NO-SPEED DISCIPLINE (load-bearing — we are never fast / never first in queue):
  - queue_ahead = the FULL visible resting depth at our level (assume we are LAST).
  - strict trade-through: a print must trade BELOW our level (px < rest) to fill us,
    proving the queue at our exact price cleared. (Optimistic variant: px <= rest.)
  - post at entry_sec + buffer; only strictly-later SELLs can fill.
  - non-fill => window skipped, ZERO cost (never counted as a loss).

Correctness: YES and NO are separate tokens with separate books. A resting YES buy
fills only from taker SELLs of the YES token; a NO buy only from SELLs of the NO
token. We keep them separate (no synthetic crossing).

Settlement: Chainlink, hold to resolution. Reuses edge_lab (decisions/settlement),
fills_v2.maker_buy_fill, hypothesis_sweep.BUILDERS (edge defs).

Run: uv run python -m research.analysis.maker_execution
Out: data/research/hypotheses/maker_execution.jsonl
"""
from __future__ import annotations
import gzip
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

from research.analysis.edge_lab import load_base, cl_outcomes
from research.analysis.hypothesis_sweep import BUILDERS
from research.sim.fills_v2 import maker_buy_fill, FeeSchedule
from research.lib.stats import window_clustered_bootstrap

OUT = os.path.join("data", "research", "hypotheses", "maker_execution.jsonl")
STAKE = 10.0
FEES = FeeSchedule()
SYMS = ("btc", "eth", "sol", "xrp")

# candidate edges (reuse BUILDERS); the ones cost-sensitivity matters most for
CANDIDATES = [
    ("det_d12_wide", "det", dict(t_lo=1, t_hi=180, dist_min=12, ask_lo=0.50, ask_hi=0.85)),
    ("det_lwd_base", "det", dict(t_lo=1, t_hi=60, dist_min=5, ask_lo=0.50, ask_hi=0.90)),
    ("fav_lowvol",   "fav", dict(t_lo=240, t_hi=420, dist_min=8, ask_lo=0.55, ask_hi=0.80,
                                 vol_max=1.0, side=None)),
    ("fav_disagree", "e4",  dict(t_lo=120, t_hi=360, dist_min=10, ud_ask_max=None)),
]


def _clean_dates():
    import glob, re
    ds = set()
    for p in glob.glob("data/live_trades/btc_*.csv.gz"):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.csv\.gz$", p)
        if m and "2026-05-23" <= m.group(1) <= "2026-06-04":
            ds.add(m.group(1))
    return sorted(ds)


def load_trades_raw(symbol):
    """per-15m-slug sorted lists of taker SELL prints, split by token.
    returns dict: slug -> {'yes': [{px,usd,sec}...], 'no': [...]} (SELLs only)."""
    out = defaultdict(lambda: {"yes": [], "no": []})
    for d in _clean_dates():
        fp = f"data/live_trades/{symbol}_{d}.csv.gz"
        if not os.path.exists(fp):
            continue
        df = pd.read_csv(fp)
        df = df[(df["market_slug"].str.contains("-15m-")) & (df["side"] == "SELL")]
        if df.empty:
            continue
        wst = df["market_slug"].str.rsplit("-", n=1).str[-1].astype(int)
        sec = (df["event_ts_ms"] // 1000 - wst).astype(int)
        usd = (df["price"] * df["size"]).astype(float)
        for slug, oc, px, u, s in zip(df["market_slug"], df["outcome"], df["price"],
                                      usd, sec):
            if s < 0:
                continue
            out[slug][oc].append({"px": float(px), "usd": float(u), "sec": int(s)})
    for slug in out:
        for tok in ("yes", "no"):
            out[slug][tok].sort(key=lambda r: r["sec"])
    return out


def _decision(b, fam, p):
    """first qualifying tick per window with the fields the maker model needs."""
    c, by = BUILDERS[fam](b, p)
    if c is None or len(c) == 0:
        return pd.DataFrame()
    c = c.copy()
    c["buy_yes"] = np.asarray(by, dtype=bool)
    first = (c.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    return first[["slug", "symbol", "date", "split", "seconds_into_window", "buy_yes",
                  "yes_best_ask", "yes_best_bid", "yes_bid_depth", "yes_ask_depth"]]


def _later(prints, post_sec):
    return [t for t in prints if t["sec"] > post_sec]


def run_edge(name, fam, p, b, tape, cl, *, rest_k=0.01, buffer=2,
             strict=True, queue_frac=1.0):
    dec = _decision(b, fam, p)
    if dec.empty:
        return None
    rows = []
    for _, r in dec.iterrows():
        slug = r["slug"]
        if slug not in cl:
            continue
        by = bool(r["buy_yes"])
        won = (cl[slug] == 1) if by else (cl[slug] == 0)
        post = int(r["seconds_into_window"]) + buffer
        # --- taker baseline (cross the spread now) ---
        tpx = r["yes_best_ask"] if by else (1.0 - r["yes_best_bid"])
        if not (0.01 < tpx < 0.99):
            continue
        tsh = STAKE / tpx
        tfee = FEES.taker_fee(tsh, tpx)
        tpnl = (tsh - STAKE - tfee) if won else (-STAKE - tfee)
        # --- maker (rest below the touch on the correct token) ---
        if by:
            rest = round(r["yes_best_ask"] - rest_k, 2)
            prints = tape.get(slug, {}).get("yes", [])
            qdepth = float(r["yes_bid_depth"]) * max(rest, 0.01)   # USD ahead, our side
        else:
            rest = round((1.0 - r["yes_best_bid"]) - rest_k, 2)
            prints = tape.get(slug, {}).get("no", [])
            qdepth = float(r["yes_ask_depth"]) * max(rest, 0.01)   # NO bid ~ YES ask
        if not (0.01 < rest < 0.99):
            continue
        lt = _later(prints, post)
        # strict trade-through: require a print strictly BELOW our level
        rp = (rest - 0.0001) if strict else rest
        mf = maker_buy_fill(rp, STAKE, [{**t, "yes_px": t["px"], "side": "SELL"} for t in lt],
                            queue_ahead_usd=qdepth * queue_frac, fees=FEES)
        filled = mf.filled
        mpnl = (won * mf.shares - mf.notional_usd + mf.rebate_usd) if filled else None
        rows.append(dict(slug=slug, split=r["split"], won=int(won), buy_yes=by,
                         tpnl=float(tpnl), filled=int(filled),
                         mpnl=(float(mpnl) if mpnl is not None else np.nan)))
    if not rows:
        return None
    d = pd.DataFrame(rows)
    return _summarize(name, d, dict(rest_k=rest_k, buffer=buffer, strict=strict,
                                    queue_frac=queue_frac))


def _ci(x, g):
    if len(x) < 5:
        return None
    lo, _, hi = window_clustered_bootstrap(np.asarray(x), np.asarray(g), n=2000)
    return [round(float(lo), 3), round(float(hi), 3)]


def _summarize(name, d, cfg):
    out = {"edge": name, "cfg": cfg, "n_windows": int(len(d)),
           "fill_rate": round(float(d["filled"].mean()), 3)}
    fl = d[d["filled"] == 1]
    # taker on ALL windows, taker on FILLED subset, maker on FILLED subset
    out["taker_all"] = dict(n=int(len(d)), wr=round(float(d["won"].mean()*100), 1),
                            ev=round(float(d["tpnl"].mean()), 3),
                            ci=_ci(d["tpnl"], d["slug"]))
    if len(fl) >= 5:
        out["taker_onfilled"] = dict(n=int(len(fl)), wr=round(float(fl["won"].mean()*100), 1),
                                     ev=round(float(fl["tpnl"].mean()), 3),
                                     ci=_ci(fl["tpnl"], fl["slug"]))
        out["maker_filled"] = dict(n=int(len(fl)), wr=round(float(fl["won"].mean()*100), 1),
                                   ev=round(float(fl["mpnl"].mean()), 3),
                                   ci=_ci(fl["mpnl"], fl["slug"]))
        # adverse selection = WR(filled) - WR(all), and maker-vs-taker on filled set
        out["adverse_sel_wr"] = round(float(fl["won"].mean()*100 - d["won"].mean()*100), 1)
        out["maker_minus_taker_onfilled"] = round(
            float(fl["mpnl"].mean() - fl["tpnl"].mean()), 3)
        # per split
        out["per_split"] = {}
        for sp in ("dev", "holdout", "future"):
            s = fl[fl["split"] == sp]
            if len(s) >= 5:
                out["per_split"][sp] = dict(
                    n=int(len(s)), maker_ev=round(float(s["mpnl"].mean()), 3),
                    maker_ci=_ci(s["mpnl"], s["slug"]),
                    taker_ev=round(float(s["tpnl"].mean()), 3))
    return out


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    b = load_base()
    cl = cl_outcomes().set_index("slug")["cl_up"].to_dict()
    print("loading trade tape (4 symbols)...")
    tape = {}
    for s in SYMS:
        tp = load_trades_raw(s)
        tape.update(tp)
        print(f"  {s}: {len(tp)} slugs with SELL prints")
    results = []
    with open(OUT, "w") as f:
        for name, fam, p in CANDIDATES:
            # main conservative config + a small sensitivity band
            for cfg in (dict(rest_k=0.01, buffer=2, strict=True, queue_frac=1.0),
                        dict(rest_k=0.02, buffer=2, strict=True, queue_frac=1.0),
                        dict(rest_k=0.01, buffer=2, strict=False, queue_frac=0.5)):
                r = run_edge(name, fam, p, b, tape, cl, **cfg)
                if r:
                    results.append(r)
                    f.write(json.dumps(r) + "\n")
                    f.flush()
                    ta, mf = r.get("taker_all"), r.get("maker_filled")
                    ts = r.get("taker_onfilled", {})
                    tag = ("strict,k=%.2f,q=%.1f" % (cfg["rest_k"], cfg["queue_frac"])
                           if cfg["strict"] else "OPTIMISTIC,k=%.2f,q=%.1f" % (cfg["rest_k"], cfg["queue_frac"]))
                    print(f"\n{name:14} [{tag}] fill={r['fill_rate']:.2f} n={r['n_windows']}")
                    print(f"   taker_all   EV {ta['ev']:+.3f} {ta['ci']} WR{ta['wr']}")
                    if mf:
                        print(f"   taker_filld EV {ts.get('ev'):+.3f} {ts.get('ci')} WR{ts.get('wr')}  (advsel WR {r['adverse_sel_wr']:+.1f})")
                        print(f"   MAKER_filld EV {mf['ev']:+.3f} {mf['ci']} WR{mf['wr']}  (maker-taker {r['maker_minus_taker_onfilled']:+.3f})")
                        ps = r.get("per_split", {})
                        fu = ps.get("future")
                        if fu:
                            print(f"   FUTURE maker EV {fu['maker_ev']:+.3f} {fu['maker_ci']} (taker {fu['taker_ev']:+.3f}) n{fu['n']}")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
