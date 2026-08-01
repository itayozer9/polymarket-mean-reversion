"""sizing_backtest (WP-A/B/C) — does VARIABLE position sizing beat fixed $10 on a
RISK-ADJUSTED basis, for our existing edges, honestly?

The trap: edge_lab pnl is exactly LINEAR in stake (pnl = stake*(won/a - 1 -
FEE*(1-a))), so any sizing-up raises total $ if EV>0. Total-$ is meaningless.
The leverage-invariant question is: does the scheme put MORE weight on the BETTER
trades? -> Sharpe / Sortino / Deflated-Sharpe of the stake-weighted daily PnL,
plus geometric growth at EQUAL bootstrapped drawdown.

Three real-world caps a naive reweight can't see, applied in a chronological walk:
  (i)  L2 capacity: stake <= 0.8 * top-of-book depth_usd at entry.
  (ii) $50/day hard_worstcase: cumulative daily reserved worst-case (stake*(1+fee))
       <= 50; trades beyond are skipped (as the live guard would).
  (iii) per-window/direction aggregate cap: 4 same-direction coins in one
       window_start_ts ~ ONE macro bet (69% co-move) -> cap aggregate same-dir stake.

Schemes: fixed | confidence-scaled (stake ~ dist_bps, robust) | fractional-Kelly
(dev-fit coarse WR table, corr-shrunk x0.73, hard-capped). Fit on dev, holdout +
future revealed once. Chainlink-settled throughout.

Run: uv run python -m research.analysis.sizing_backtest
Out: data/research/hypotheses/sizing.jsonl
"""
from __future__ import annotations
import json
import os

import numpy as np
import pandas as pd

from research.analysis.edge_lab import load_base, cl_outcomes, _book_index
from research.analysis.hypothesis_sweep import BUILDERS
from research.lib import rigor as R

OUT = os.path.join("data", "research", "hypotheses", "sizing.jsonl")
FEE = 0.07
BASE = 10.0
CORR_SHRINK = 0.73          # effective 2.67/5 independent bets
DAY_CAP = 50.0
WINDOW_DIR_CAP = 40.0       # max aggregate same-direction stake per window_start_ts
CAP_MAX = 100.0             # absolute per-trade ceiling
CAP_MIN = 5.0               # MIN_ORDER_USD

EDGES = {
    "det_d12_wide": ("det", dict(t_lo=1, t_hi=180, dist_min=12, ask_lo=0.50, ask_hi=0.85)),
    "fav_lowvol":   ("fav", dict(t_lo=240, t_hi=420, dist_min=8, ask_lo=0.55, ask_hi=0.80,
                                 vol_max=1.0, side=None)),
    "fav_disagree": ("e4",  dict(t_lo=120, t_hi=360, dist_min=10, ud_ask_max=None)),
    "fav_momentum": ("momentum", dict(t_lo=60, t_hi=120, dist_min=12, mom_min=12.0)),
    "det_disagree": ("e4",  dict(t_lo=1, t_hi=60, dist_min=5, ud_ask_max=None)),
}


def build_ledger(b, name, fam, p):
    """per-trade ledger with the features sizing needs (dist, time_left, ask, depth)."""
    c, by = BUILDERS[fam](b, p)
    if c is None or len(c) == 0:
        return pd.DataFrame()
    c = c.copy()
    c["buy_yes"] = np.asarray(by, dtype=bool)
    first = (c.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False).first())
    first = first.rename(columns={"seconds_into_window": "entry_sec"})
    first["fill_sec"] = first["entry_sec"] + 2
    bk = _book_index()
    m = first.merge(bk, left_on=["slug", "fill_sec"], right_on=["slug", "seconds_into_window"],
                    how="left", suffixes=("", "_bk"))
    byk = m["buy_yes"].astype(bool).to_numpy()
    ask = np.where(byk, m["yes_best_ask_bk"].to_numpy("f8"), 1.0 - m["yes_best_bid_bk"].to_numpy("f8"))
    depth_sh = np.where(byk, m["yes_ask_depth_bk"].to_numpy("f8"), m["no_ask_depth_bk"].to_numpy("f8"))
    ok = ((m["book_healthy_bk"] == True).to_numpy() & np.isfinite(ask) & (ask > 0.01) & (ask < 0.99)
          & (depth_sh * ask >= BASE))
    m = m[ok].copy()
    if m.empty:
        return m
    m["entry_ask"] = ask[ok]
    m["depth_usd"] = (depth_sh * ask)[ok]
    m = m.merge(cl_outcomes(), on="slug", how="inner")
    byk = m["buy_yes"].astype(bool).to_numpy()
    m["won"] = np.where(byk, m["cl_up"].to_numpy() == 1, m["cl_up"].to_numpy() == 0).astype(int)
    a = m["entry_ask"].to_numpy("f8")
    # per-$ return (pnl @ $1 stake) — pnl is linear in stake
    m["ret_per_usd"] = np.where(m["won"] == 1, 1.0 / a - 1.0 - FEE * (1 - a), -1.0 - FEE * (1 - a))
    m["edge"] = name
    return m[["edge", "slug", "symbol", "date", "split", "window_start_ts", "entry_sec",
              "buy_yes", "won", "entry_ask", "abs_dist_bps", "time_left_sec",
              "depth_usd", "ret_per_usd"]]


# ---- WR table for Kelly (dev-fit, coarse, shrunk) ----
def _bucket(row):
    d = row["abs_dist_bps"]
    db = "d<8" if d < 8 else "d8-15" if d < 15 else "d15-30" if d < 30 else "d>30"
    a = row["entry_ask"]
    ab = "a<.6" if a < 0.6 else "a.6-.75" if a < 0.75 else "a.75-.85" if a < 0.85 else "a>.85"
    return f"{row['edge']}|{db}|{ab}"


def fit_wr_table(led_dev):
    led_dev = led_dev.copy()
    led_dev["bk"] = led_dev.apply(_bucket, axis=1)
    edge_wr = led_dev.groupby("edge")["won"].mean().to_dict()
    out = {}
    for bk, g in led_dev.groupby("bk"):
        edge = bk.split("|")[0]
        n = len(g)
        # shrink toward edge overall WR (pseudo-count 20)
        p = (g["won"].sum() + 20 * edge_wr[edge]) / (n + 20)
        out[bk] = float(p)
    return out, edge_wr


def kelly_f(p, a):
    """Kelly fraction for a binary BUY at price a, win prob p. b = net odds = (1-a)/a."""
    b = (1 - a) / a if a > 0 else 0
    if b <= 0:
        return 0.0
    f = p - (1 - p) / b
    return max(0.0, f)


# ---- sizing schemes: return per-trade DESIRED stake (pre-caps) ----
def stake_fixed(row, **k):
    return BASE


def stake_conf(row, ref=20.0, base=10.0, mn=5.0, mx=40.0, **k):
    return float(np.clip(base * (row["abs_dist_bps"] / ref), mn, mx))


def stake_kelly(row, wr_table=None, edge_wr=None, frac=0.25, mn=5.0, mx=40.0, bankroll=1000.0, **k):
    bk = _bucket(row)
    p = wr_table.get(bk, edge_wr.get(row["edge"], 0.5))
    f = kelly_f(p, row["entry_ask"]) * frac * CORR_SHRINK
    return float(np.clip(f * bankroll, mn, mx))


SCHEMES = {
    "fixed":        (stake_fixed, {}),
    "conf_ref15":   (stake_conf, dict(ref=15.0, base=10.0, mn=5.0, mx=40.0)),
    "conf_ref25":   (stake_conf, dict(ref=25.0, base=12.0, mn=5.0, mx=50.0)),
    "kelly_q":      (stake_kelly, dict(frac=0.25, mn=5.0, mx=50.0)),
    "kelly_h":      (stake_kelly, dict(frac=0.50, mn=5.0, mx=60.0)),  # sensitivity only
}


def realized_stakes(led, scheme_fn, params, wr_table, edge_wr, *,
                    concurrent_cap=DAY_CAP, window_dir_cap=None):
    """chronological walk applying the real caps -> realized stake.
    - hard_worstcase = CONCURRENT open worst-case <= concurrent_cap, reserved at
      entry and RELEASED at settle (window end), per the live DailyLossGuard.
    - L2 capacity: stake <= 0.8*depth_usd.
    - window_dir_cap (ensemble only): aggregate same-direction stake per
      window_start_ts (the macro 4-coin co-move control)."""
    d = led.sort_values(["window_start_ts", "entry_sec"]).copy().reset_index(drop=True)
    desired = d.apply(lambda r: scheme_fn(r, wr_table=wr_table, edge_wr=edge_wr, **params), axis=1)
    desired = np.minimum(desired.to_numpy("f8"), 0.8 * d["depth_usd"].to_numpy("f8"))  # L2
    desired = np.clip(desired, 0.0, CAP_MAX)
    stake = np.zeros(len(d))
    win_dir = {}
    open_pos = []           # list of (release_ts, worst_case) for concurrent cap
    for i in range(len(d)):
        r = d.iloc[i]
        entry_ts = r["window_start_ts"] + r["entry_sec"]
        release_ts = r["window_start_ts"] + 900
        open_pos = [(rt, w) for (rt, w) in open_pos if rt > entry_ts]   # release settled
        cur_open = sum(w for _, w in open_pos)
        want = desired[i]
        if want < CAP_MIN:
            continue
        wc_frac = 1.0 + FEE * (1 - r["entry_ask"])      # worst-case per $ staked
        room_conc = (concurrent_cap - cur_open) / wc_frac
        s = min(want, room_conc)
        if window_dir_cap is not None:
            wkey = (r["window_start_ts"], bool(r["buy_yes"]))
            s = min(s, window_dir_cap - win_dir.get(wkey, 0.0))
        if s < CAP_MIN:
            continue
        stake[i] = s
        open_pos.append((release_ts, s * wc_frac))
        if window_dir_cap is not None:
            win_dir[wkey] = win_dir.get(wkey, 0.0) + s
    d["stake"] = stake
    d["pnl"] = d["stake"] * d["ret_per_usd"]
    return d[d["stake"] > 0]


def metrics(d, label):
    daily = R.daily_pnl_from_ledger(d)
    v = daily.values
    sr = R.sharpe(v)
    so = R.sortino(v)
    wp = R.block_bootstrap_worstpath(v)
    mdd = wp.get("max_drawdown", {}).get("p95") if wp else None
    return dict(label=label, n=int(len(d)), total=round(float(d["pnl"].sum()), 1),
                mean_stake=round(float(d["stake"].mean()), 2),
                sharpe=round(float(sr), 3), sortino=round(float(so), 3),
                p95_maxdd=round(float(mdd), 1) if mdd is not None else None,
                dsr=round(float(R.deflated_sharpe_ratio(v, n_trials=len(SCHEMES)).get("dsr", float("nan"))), 3))


def _report(tag, led, wr_table, edge_wr, f, *, window_dir_cap=None, concurrent_cap=DAY_CAP):
    res = {}
    for sname, (fn, params) in SCHEMES.items():
        d = realized_stakes(led, fn, params, wr_table, edge_wr,
                            concurrent_cap=concurrent_cap, window_dir_cap=window_dir_cap)
        row = {"group": tag, "scheme": sname}
        for sp in ("dev", "holdout", "future", "ALL"):
            sub = d if sp == "ALL" else d[d["split"] == sp]
            if len(sub) >= 8:
                row[sp] = metrics(sub, sp)
        res[sname] = row
        f.write(json.dumps(row) + "\n")
    base = {sp: res["fixed"].get(sp, {}).get("sharpe") for sp in ("dev", "holdout", "future")}
    print(f"\n=== {tag} === (Sharpe = leverage-invariant headline)")
    print(f"{'scheme':11} {'split':7} {'n':>4} {'mStk':>5} {'Sharpe':>7} {'Sortino':>8} {'p95DD':>7} {'tot$':>7}")
    for sname, row in res.items():
        for sp in ("dev", "holdout", "future"):
            m = row.get(sp)
            if not m:
                continue
            dd = m.get("p95_maxdd")
            vs = (f" vs fix {base[sp]:+.2f}" if sname != "fixed" and base.get(sp) is not None else "")
            print(f"{sname:11} {sp:7} {m['n']:>4} {m['mean_stake']:>5.0f} {m['sharpe']:>7.3f} "
                  f"{m['sortino']:>8.3f} {(dd if dd is not None else 0):>7.1f} {m['total']:>7.0f}{vs}")
    return res


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    b = load_base()
    leds = {n: build_ledger(b, n, fam, p) for n, (fam, p) in EDGES.items()}
    led = pd.concat([x for x in leds.values() if len(x)], ignore_index=True)
    print(f"combined ledger: {len(led)} trades across {led['edge'].nunique()} edges")
    wr_table, edge_wr = fit_wr_table(led[led["split"] == "dev"])
    print("edge overall WR (dev):", {k: round(v, 3) for k, v in edge_wr.items()})
    with open(OUT, "w") as f:
        # PER-EDGE (each its own $50 concurrent cap) — does sizing help each edge?
        for n, l in leds.items():
            if len(l) >= 40:
                _report(f"edge:{n}", l, wr_table, edge_wr, f, concurrent_cap=DAY_CAP)
        # ENSEMBLE: pooled, with per-window-direction macro cap
        _report("ensemble_pooled50_windir40", led, wr_table, edge_wr, f,
                concurrent_cap=DAY_CAP, window_dir_cap=WINDOW_DIR_CAP)
        _report("ensemble_pooled150_windir40", led, wr_table, edge_wr, f,
                concurrent_cap=150.0, window_dir_cap=WINDOW_DIR_CAP)
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
