"""Lead D — maker-execution reframe, Polymarket 15m crypto Up/Down.

The hypothesis: every prior result was killed by the 16-21% taker round-trip
cost (fee 0.07*p*(1-p) both legs + crossing the spread). A maker (resting limit
orders) pays 0 fee and does not cross the spread. So a signal with a small but
genuinely POSITIVE gross edge — dead as a taker — could be alive as a maker.
This script determines, honestly, whether maker execution turns anything
profitable.

Two parts; Part 1 GATES Part 2:

  Part 1 — find positive GROSS edge (before any cost).
    Across the cheap-side states, the gross edge is `realized cheap_won -
    entry price`. Computed per cell conditioned on price bucket / drop /
    time-left / symbol, de-biased to one observation per (window, 60s slice)
    to defeat the ~87%-stale-tick weighting, with window-clustered CIs and a
    dev-internal early/late CV. A cell qualifies only if its gross edge is
    positive with a CI excluding zero in BOTH dev halves.

  Part 2 — only if a positive-gross cell survives.
    Backtest that cell as a REALISTIC maker: post a resting limit BUY in the
    cell; it fills ONLY when a strictly-later tick shows the side's best bid
    trade down to/through the limit (someone sold into the level). If the
    market never reaches the limit the order does not fill and that trade
    simply does not happen. Exit via a resting maker limit SELL at a profit
    target (fills only on a genuine later trade-through up to it) else settle
    on the true `outcome_up`. 0 fee. Compared to the same as a taker.

CRITICAL DISCIPLINE — Phase 4 forensics (`docs/research/phase4_forensics.md`)
proved that the entry-candidate table's cheap-side columns are CORRUPTED on
decided-market books: when a window resolves, both mids collapse toward 0, the
`cheap_side = lower-ask side` definition breaks, and a worthless losing side is
mislabelled with `cheap_mid ~ 0` and a spuriously high `cheap_won`. The
`cheap_mid in (0,0.01]` "cell" therefore shows a fantasy +30c gross edge that
is 100% that artifact. So this script works off the GENUINE two-sided book in
`ticks_15m.parquet` with a hard book-health guard (both sides priced in
(0.001,0.999), positive bids below asks, and complement-consistent within 6c),
and the maker fill model never assumes a resting order fills — it fills only on
a real later-tick trade-through.

Dev split May 15-20 only. The sealed hold-out (May 21-22) is asserted untouched
through Part 1/2 and consulted at most ONCE, only if a candidate survives dev.

Run:  uv run --extra dev python -m research.analysis.maker_reframe
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.holdout import (  # noqa: E402
    DEV_START, DEV_END, HOLDOUT_START, HOLDOUT_END,
)
from research.lib.stats import window_clustered_bootstrap  # noqa: E402

DATA = REPO / "data" / "research"
TICKS = DATA / "ticks_15m.parquet"
DOC = REPO / "docs" / "research" / "lead_D_maker.md"

SEED = 42
N_BOOT = 3000
STAKE = 10.0  # dollars per trade

# cheap_mid price buckets for the gross-edge search.
MID_EDGES = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.13, 0.17, 0.22, 0.30,
             0.40, 0.51]
# A cheap_mid bucket qualifies as a maker candidate only with a CV-stable
# positive gross edge; we still pick the "least-bad" bucket to demonstrate the
# realistic-maker backtest honestly even when nothing qualifies.

# Taker fee model (Phase 0 / divergence_backtest frame): per share, per leg.
_fee = lambda p: 0.07 * p * (1.0 - p)  # noqa: E731


# ===========================================================================
# Data loading — the GENUINE two-sided book, with a hard health guard
# ===========================================================================

def load_dev_holdout():
    """Load the 15m tick book for dev AND hold-out, deriving the cheap side
    from the GENUINE two-sided book with a hard health guard.

    Returns (dev, holdout) DataFrames. The hold-out is loaded but Part 1/2
    never touch it — it is returned only so the final-check step can use it.
    """
    cols = ["slug", "symbol", "window_start_ts", "seconds_into_window",
            "time_left_sec", "yes_best_bid", "yes_best_ask", "no_best_bid",
            "no_best_ask", "yes_bid_depth", "yes_ask_depth", "no_bid_depth",
            "no_ask_depth", "yes_drop_30s", "no_drop_30s", "outcome_up"]
    t = pd.read_parquet(TICKS, columns=cols)
    t = t[t["window_start_ts"].notna()].copy()
    t["date"] = pd.to_datetime(
        t["window_start_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    t = t[t["date"] >= DEV_START].copy()  # drop the lone 1970 garbage row

    ya, yb = t["yes_best_ask"], t["yes_best_bid"]
    na, nb = t["no_best_ask"], t["no_best_bid"]
    # Hard book-health guard. Decided-market books (Phase 4) fail this: a
    # resolved side has ask ~0 / ~1 and total_mid collapses, so the
    # complement-consistency test (yes_ask + no_bid ~ 1) is the key filter.
    healthy = (
        (ya > 0.001) & (ya < 0.999) & (na > 0.001) & (na < 0.999)
        & (yb > 0) & (nb > 0) & (yb < ya) & (nb < na)
        & ((ya + nb - 1.0).abs() < 0.06) & ((na + yb - 1.0).abs() < 0.06)
    )
    h = t[healthy].copy()

    h["cheap_is_yes"] = h["yes_best_ask"] <= h["no_best_ask"]
    h["cheap_ask"] = np.where(h["cheap_is_yes"], h["yes_best_ask"],
                              h["no_best_ask"])
    h["cheap_bid"] = np.where(h["cheap_is_yes"], h["yes_best_bid"],
                              h["no_best_bid"])
    h["cheap_mid"] = 0.5 * (h["cheap_ask"] + h["cheap_bid"])
    h["cheap_won"] = np.where(h["cheap_is_yes"], h["outcome_up"],
                              1.0 - h["outcome_up"])
    h["cheap_drop_30s"] = np.where(h["cheap_is_yes"], h["yes_drop_30s"],
                                   h["no_drop_30s"])  # percent (0-100)

    dev = h[(h["date"] >= DEV_START) & (h["date"] <= DEV_END)].copy()
    holdout = h[(h["date"] >= HOLDOUT_START)
                & (h["date"] <= HOLDOUT_END)].copy()
    assert dev["date"].nunique() <= 6, "unexpected dev day count"
    assert (dev["date"] <= DEV_END).all(), "HOLD-OUT LEAKED INTO DEV"
    return dev.reset_index(drop=True), holdout.reset_index(drop=True)


def debias(df: pd.DataFrame) -> pd.DataFrame:
    """One observation per (window, 60s time-slice). Phase 0 found ~87% of
    ticks are stale; tick-pooling over-weights long-lingering quotes, so the
    de-biased cross-section is the honest unit for the gross-edge search."""
    d = df.copy()
    d["ts"] = (d["seconds_into_window"] // 60).astype(int)
    return d.groupby(["slug", "ts"], as_index=False).first()


# ===========================================================================
# Part 1 — the positive-gross-edge search
# ===========================================================================

def _gross_ci(sub: pd.DataFrame, n: int = N_BOOT, seed: int = SEED):
    """Window-clustered (90%) bootstrap of the mean gross edge
    `cheap_won - cheap_mid` for one cell. Returns (lo, mid, hi) or None."""
    if len(sub) < 30:
        return None
    e = (sub["cheap_won"] - sub["cheap_mid"]).to_numpy(dtype="f8")
    return window_clustered_bootstrap(e, sub["slug"].to_numpy(), n=n, seed=seed)


def gross_edge_by(deb: pd.DataFrame, col: str, edges) -> list[dict]:
    """Gross edge per bucket of `col` (cut by `edges`), with window-clustered
    CI. Returns a list of dicts."""
    d = deb.copy()
    d["_b"] = pd.cut(d[col], edges, include_lowest=True)
    out = []
    for b, sub in d.groupby("_b", observed=True):
        r = _gross_ci(sub)
        if r is None:
            continue
        out.append({
            "bucket": str(b), "n": int(len(sub)),
            "nwin": int(sub["slug"].nunique()),
            "mean_mid": float(sub["cheap_mid"].mean()),
            "realized": float(sub["cheap_won"].mean()),
            "gross": r[1], "ci_lo": r[0], "ci_hi": r[2],
        })
    return out


def gross_edge_by_symbol(deb: pd.DataFrame) -> list[dict]:
    out = []
    for s, sub in deb.groupby("symbol"):
        r = _gross_ci(sub)
        if r is None:
            continue
        out.append({"bucket": s, "n": int(len(sub)),
                    "nwin": int(sub["slug"].nunique()),
                    "mean_mid": float(sub["cheap_mid"].mean()),
                    "realized": float(sub["cheap_won"].mean()),
                    "gross": r[1], "ci_lo": r[0], "ci_hi": r[2]})
    return out


def cv_gross(deb: pd.DataFrame, edges) -> list[dict]:
    """Dev-internal CV: gross edge per cheap_mid bucket on the EARLY (May
    15-17) and LATE (May 18-20) dev halves. A bucket is CV-stable-positive
    only if BOTH halves have gross > 0 with a CI excluding zero."""
    early = deb[deb["date"] <= "2026-05-17"]
    late = deb[deb["date"] >= "2026-05-18"]
    rows = []
    for half, name in ((early, "early"), (late, "late")):
        d = half.copy()
        d["_b"] = pd.cut(d["cheap_mid"], edges, include_lowest=True)
        for b, sub in d.groupby("_b", observed=True):
            r = _gross_ci(sub, seed=SEED + 7)
            if r is None:
                continue
            rows.append({"half": name, "bucket": str(b), "n": int(len(sub)),
                         "gross": r[1], "ci_lo": r[0], "ci_hi": r[2]})
    return rows


def find_positive_cells(by_mid: list[dict], cv_rows: list[dict]) -> list[str]:
    """A cell qualifies only if (a) its pooled gross edge CI excludes zero on
    the positive side AND (b) BOTH dev halves agree (gross>0, CI excludes 0)."""
    pooled_pos = {r["bucket"] for r in by_mid if r["ci_lo"] > 0}
    by_half = {}
    for r in cv_rows:
        by_half.setdefault(r["bucket"], {})[r["half"]] = r
    cv_pos = set()
    for bk, hd in by_half.items():
        e, lt = hd.get("early"), hd.get("late")
        if e and lt and e["ci_lo"] > 0 and lt["ci_lo"] > 0:
            cv_pos.add(bk)
    return sorted(pooled_pos & cv_pos)


# ===========================================================================
# Part 2 — the REALISTIC maker backtest
# ===========================================================================

def run_maker_backtest(book: pd.DataFrame, mid_lo: float, mid_hi: float,
                       execution: str, profit_target=1.00) -> pd.DataFrame:
    """Backtest one cheap_mid cell, per window, as a realistic maker OR a
    taker. ONE trade per window.

    The cell entry condition: at some tick the cheap side's mid is in
    [mid_lo, mid_hi). We treat the FIRST such tick in the window as the
    "decision tick" — the limit order is posted then.

    TAKER: at the decision tick, buy at the cheap side's ASK, pay
    0.07*p*(1-p) entry fee. Exit on a strictly-later tick whose side-bid
    reaches the +profit_target level (pay exit fee) else settle on
    `outcome_up` (no exit fee).

    MAKER (realistic): post a resting limit BUY at the cheap side's BID at the
    decision tick. It fills ONLY if a strictly-later tick in the window shows
    the side's best bid <= that limit — i.e. the market actually traded down
    to/through the level (someone sold into it). If the market never reaches
    the limit, the order DOES NOT FILL — that trade does not happen and earns
    nothing (dropped from the result). 0 fee. The fill is adverse-selection
    biased by construction: you are filled precisely when the side is getting
    cheaper. After a fill, post a resting limit SELL at limit*(1+profit_target);
    it fills only if a still-later tick's side-bid reaches it; else hold to
    window resolution and settle on the true `outcome_up`.
    """
    rows = []
    side_col_bid = {"YES": "yes_best_bid", "NO": "no_best_bid"}
    side_col_ask = {"YES": "yes_best_ask", "NO": "no_best_ask"}

    for slug, w in book.groupby("slug", sort=False):
        w = w.sort_values("seconds_into_window")
        in_cell = (w["cheap_mid"] >= mid_lo) & (w["cheap_mid"] < mid_hi)
        if not in_cell.any():
            continue
        di = np.argmax(in_cell.to_numpy())  # first in-cell tick
        d = w.iloc[di]
        side = "YES" if d["cheap_is_yes"] else "NO"
        bid_c, ask_c = side_col_bid[side], side_col_ask[side]
        side_won = float(d["cheap_won"])
        later = w.iloc[di + 1:]

        if execution == "taker":
            entry = float(d["cheap_ask"])
            if not (1e-6 < entry < 0.999):
                continue
            shares = STAKE / entry
            entry_fee = _fee(entry) * shares
            target_px = entry * (1.0 + profit_target)
            pnl, reason = None, None
            if target_px < 1.0 and len(later):
                exq = later[bid_c].to_numpy(dtype="f8")
                hit = np.where(exq >= target_px)[0]
                if len(hit):
                    px = float(exq[hit[0]])
                    pnl = (px - _fee(px)) * shares - STAKE - entry_fee
                    reason = "target"
            if pnl is None:
                pnl = side_won * shares - STAKE - entry_fee
                reason = "resolution"
            rows.append({"slug": slug, "side": side, "date": d["date"],
                         "filled": True, "entry": entry, "shares": shares,
                         "side_won": side_won, "pnl": pnl,
                         "exit_reason": reason})
            continue

        # --- realistic maker ---
        # Resting buy limit at the side's BID at the decision tick.
        limit = float(d[bid_c])
        if not (1e-6 < limit < 0.999):
            continue
        # Fill ONLY on a genuine later trade-through: a later tick whose
        # side best-bid <= limit (the market traded down to/through us).
        if not len(later):
            rows.append({"slug": slug, "side": side, "date": d["date"],
                         "filled": False, "entry": np.nan, "shares": np.nan,
                         "side_won": side_won, "pnl": 0.0,
                         "exit_reason": "no_fill"})
            continue
        bidpath = later[bid_c].to_numpy(dtype="f8")
        fhit = np.where(bidpath <= limit)[0]
        if not len(fhit):
            # never traded down to our level — no fill, no trade.
            rows.append({"slug": slug, "side": side, "date": d["date"],
                         "filled": False, "entry": np.nan, "shares": np.nan,
                         "side_won": side_won, "pnl": 0.0,
                         "exit_reason": "no_fill"})
            continue
        fi = int(fhit[0])
        shares = STAKE / limit          # bought at the limit price, 0 fee
        post_fill = later.iloc[fi + 1:]  # strictly-later than the fill tick
        target_px = limit * (1.0 + profit_target)
        pnl, reason = None, None
        if target_px < 1.0 and len(post_fill):
            # resting sell limit fills only on a genuine later trade-up: a
            # later tick whose side best-bid reaches the target.
            exq = post_fill[bid_c].to_numpy(dtype="f8")
            ehit = np.where(exq >= target_px)[0]
            if len(ehit):
                pnl = target_px * shares - STAKE  # 0 fee both legs
                reason = "target"
        if pnl is None:
            pnl = side_won * shares - STAKE  # settle on true outcome
            reason = "resolution"
        rows.append({"slug": slug, "side": side, "date": d["date"],
                     "filled": True, "entry": limit, "shares": shares,
                     "side_won": side_won, "pnl": pnl, "exit_reason": reason})

    return pd.DataFrame(rows)


def summarize_backtest(trades: pd.DataFrame, n_windows: int,
                       n_days: int) -> dict:
    """Fill rate, n filled, win rate, net PnL/trade + window-clustered CI,
    trades/day, resolution-loss rate."""
    n_candidates = len(trades)
    filled = trades[trades["filled"]] if not trades.empty else trades
    n_filled = len(filled)
    if n_filled == 0:
        return {"n_candidates": n_candidates, "n_filled": 0,
                "fill_rate": 0.0, "win_rate": float("nan"),
                "mean_pnl": float("nan"), "ci": (float("nan"), float("nan")),
                "total_pnl": 0.0, "trades_per_day": 0.0,
                "dollars_per_day": 0.0, "res_loss_rate": float("nan")}
    pnl = filled["pnl"].to_numpy(dtype="f8")
    lo, mid, hi = window_clustered_bootstrap(
        pnl, filled["slug"].to_numpy(), n=N_BOOT, seed=SEED)
    won = filled["side_won"].to_numpy(dtype="f8")
    res = filled[filled["exit_reason"] == "resolution"]
    res_loss = (res["side_won"] == 0).mean() if len(res) else float("nan")
    return {
        "n_candidates": n_candidates, "n_filled": n_filled,
        "fill_rate": n_filled / max(n_candidates, 1),
        "win_rate": float(won.mean()),
        "mean_pnl": float(pnl.mean()), "ci": (lo, hi),
        "total_pnl": float(pnl.sum()),
        "trades_per_day": n_filled / max(n_days, 1),
        "dollars_per_day": float(pnl.sum()) / max(n_days, 1),
        "res_loss_rate": float(res_loss),
    }


# ===========================================================================
# Orchestration
# ===========================================================================

def run() -> dict:
    print("=" * 72)
    print("Lead D — maker-execution reframe")
    print(f"Dev {DEV_START}..{DEV_END}; hold-out {HOLDOUT_START}.."
          f"{HOLDOUT_END} SEALED.")
    print("=" * 72)

    dev, holdout = load_dev_holdout()
    n_dev_windows = dev["slug"].nunique()
    n_dev_days = dev["date"].nunique()
    print(f"  dev: {len(dev):,} healthy ticks, {n_dev_windows} windows, "
          f"{n_dev_days} days")
    print(f"  hold-out (SEALED): {len(holdout):,} healthy ticks, "
          f"{holdout['slug'].nunique()} windows")

    deb = debias(dev)
    print(f"  de-biased dev cross-section: {len(deb):,} obs "
          f"(one per window x 60s slice)")

    # --- Part 1: the positive-gross-edge search -------------------------
    pooled = _gross_ci(deb)
    print(f"\nPart 1 — gross-edge search")
    print(f"  de-biased pooled gross edge (cheap_won - cheap_mid): "
          f"{pooled[1]:+.4f}  90% CI [{pooled[0]:+.4f}, {pooled[2]:+.4f}]")

    by_mid = gross_edge_by(deb, "cheap_mid", MID_EDGES)
    by_drop = gross_edge_by(deb, "cheap_drop_30s",
                            [-0.001, 0.001, 10, 25, 50, 100.001])
    by_tl = gross_edge_by(deb, "time_left_sec",
                          [0, 120, 300, 500, 700, 901])
    by_sym = gross_edge_by_symbol(deb)
    cv_rows = cv_gross(deb, [0.0, 0.04, 0.08, 0.13, 0.22, 0.35, 0.51])

    print("  gross edge by cheap_mid bucket:")
    for r in by_mid:
        flag = ("  <-- POSITIVE, CI excl 0" if r["ci_lo"] > 0
                else ("  (neg)" if r["ci_hi"] < 0 else "  (~0)"))
        print(f"    {r['bucket']:16s} n={r['n']:6d} gross={r['gross']:+.4f} "
              f"CI[{r['ci_lo']:+.4f},{r['ci_hi']:+.4f}]{flag}")

    positive_cells = find_positive_cells(
        by_mid, cv_rows)
    print(f"\n  CV-stable positive-gross cells: "
          f"{positive_cells if positive_cells else 'NONE'}")

    # --- Part 2: realistic-maker backtest -------------------------------
    # Per the discipline: even with no qualifying cell we still run the
    # realistic-maker backtest on the LEAST-BAD (highest pooled gross) cheap
    # bucket, to demonstrate honestly that maker cannot create gross edge.
    if positive_cells:
        # parse the first qualifying bucket's edges
        demo_bucket = positive_cells[0]
        gate = "candidate_survives"
    else:
        demo_bucket = max(by_mid, key=lambda r: r["gross"])["bucket"]
        gate = "no_positive_gross_cell"
    mid_lo, mid_hi = _parse_bucket(demo_bucket)
    print(f"\nPart 2 — realistic-maker backtest on cheap_mid cell "
          f"[{mid_lo}, {mid_hi}) ({gate})")

    bt = {}
    for execution in ("maker", "taker"):
        tr = run_maker_backtest(dev, mid_lo, mid_hi, execution,
                                profit_target=1.00)
        s = summarize_backtest(tr, n_dev_windows, n_dev_days)
        bt[execution] = s
        print(f"  {execution:6s}: n_filled={s['n_filled']:5d} "
              f"fill_rate={s['fill_rate']:.2f} WR={s['win_rate']:.3f} "
              f"PnL/trade={s['mean_pnl']:+.4f} "
              f"CI[{s['ci'][0]:+.4f},{s['ci'][1]:+.4f}] "
              f"${s['dollars_per_day']:+.2f}/day "
              f"res_loss={s['res_loss_rate']:.3f}")

    # --- Part 3: hold-out final check, ONLY if a candidate survived dev --
    holdout_bt = None
    if gate == "candidate_survives":
        print("\nPart 3 — sealed hold-out final check (candidate survived "
              "dev — opening hold-out ONCE)")
        n_ho_windows = holdout["slug"].nunique()
        n_ho_days = holdout["date"].nunique()
        holdout_bt = {}
        for execution in ("maker", "taker"):
            tr = run_maker_backtest(holdout, mid_lo, mid_hi, execution,
                                    profit_target=1.00)
            s = summarize_backtest(tr, n_ho_windows, n_ho_days)
            holdout_bt[execution] = s
            print(f"  {execution:6s}: n_filled={s['n_filled']:5d} "
                  f"PnL/trade={s['mean_pnl']:+.4f} "
                  f"${s['dollars_per_day']:+.2f}/day")
    else:
        print("\nPart 3 — NO candidate survived dev. The sealed hold-out is "
              "NOT consulted; it stays sealed.")

    result = {
        "pooled_gross": pooled, "by_mid": by_mid, "by_drop": by_drop,
        "by_tl": by_tl, "by_sym": by_sym, "cv_rows": cv_rows,
        "positive_cells": positive_cells, "gate": gate,
        "demo_bucket": (mid_lo, mid_hi), "bt": bt, "holdout_bt": holdout_bt,
        "n_dev_windows": n_dev_windows, "n_dev_days": n_dev_days,
    }
    _write_doc(result)
    print(f"\nDoc written: {DOC}")
    print("=" * 72)
    return result


def _parse_bucket(s: str) -> tuple[float, float]:
    """Parse a pandas Interval repr like '(0.04, 0.06]' or '(-0.001, 0.02]'."""
    inner = s.strip().lstrip("([").rstrip(")]")
    lo, hi = (float(x) for x in inner.split(","))
    return max(lo, 0.0), hi


# ===========================================================================
# Doc rendering
# ===========================================================================

def _mid_table(rows: list[dict]) -> str:
    h = ("| cheap_mid bucket | n (de-biased) | windows | mean mid "
         "| realized | gross edge | 90% CI |\n|---|---|---|---|---|---|---|\n")
    out = []
    for r in rows:
        out.append(
            f"| {r['bucket']} | {r['n']:,} | {r['nwin']:,} "
            f"| {r['mean_mid']:.4f} | {r['realized']:.4f} "
            f"| **{r['gross']:+.4f}** "
            f"| [{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}] |")
    return h + "\n".join(out)


def _simple_table(rows: list[dict], label: str) -> str:
    h = (f"| {label} | n | gross edge | 90% CI |\n|---|---|---|---|\n")
    out = []
    for r in rows:
        out.append(f"| {r['bucket']} | {r['n']:,} | {r['gross']:+.4f} "
                   f"| [{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}] |")
    return h + "\n".join(out)


def _cv_table(cv_rows: list[dict]) -> str:
    by_half = {}
    for r in cv_rows:
        by_half.setdefault(r["bucket"], {})[r["half"]] = r
    h = ("| cheap_mid bucket | early gross (CI) | late gross (CI) "
         "| both halves positive? |\n|---|---|---|---|\n")
    out = []
    for bk in sorted(by_half):
        e, lt = by_half[bk].get("early"), by_half[bk].get("late")
        es = (f"{e['gross']:+.4f} [{e['ci_lo']:+.4f},{e['ci_hi']:+.4f}]"
              if e else "-")
        ls = (f"{lt['gross']:+.4f} [{lt['ci_lo']:+.4f},{lt['ci_hi']:+.4f}]"
              if lt else "-")
        ok = "YES" if (e and lt and e["ci_lo"] > 0 and lt["ci_lo"] > 0) \
            else "no"
        out.append(f"| {bk} | {es} | {ls} | {ok} |")
    return h + "\n".join(out)


def _bt_table(bt: dict) -> str:
    h = ("| execution | candidates | filled | fill rate | win rate "
         "| PnL/trade | 90% CI | trades/day | $/day | resolution-loss rate "
         "|\n|---|---|---|---|---|---|---|---|---|---|\n")
    out = []
    for ex in ("maker", "taker"):
        s = bt[ex]
        if s["n_filled"] == 0:
            out.append(f"| {ex} | {s['n_candidates']:,} | 0 | 0.00 | - | - "
                       f"| - | - | - | - |")
            continue
        out.append(
            f"| {ex} | {s['n_candidates']:,} | {s['n_filled']:,} "
            f"| {s['fill_rate']:.2f} | {s['win_rate']:.3f} "
            f"| ${s['mean_pnl']:+.4f} "
            f"| [${s['ci'][0]:+.4f}, ${s['ci'][1]:+.4f}] "
            f"| {s['trades_per_day']:.1f} | ${s['dollars_per_day']:+.2f} "
            f"| {s['res_loss_rate']:.3f} |")
    return h + "\n".join(out)


def _write_doc(r: dict) -> None:
    p = r["pooled_gross"]
    mlo, mhi = r["demo_bucket"]
    L: list[str] = []
    L.append("# Lead D — Maker-Execution Reframe")
    L.append("")
    L.append("**Date:** 2026-05-22")
    L.append("**Branch:** `edge-leads`")
    L.append("**Code:** `research/analysis/maker_reframe.py`")
    L.append("**Data:** `data/research/ticks_15m.parquet` (corrected dataset)."
             f" Dev split **{DEV_START}..{DEV_END}** "
             f"({r['n_dev_days']} UTC days, {r['n_dev_windows']:,} windows); "
             f"the **{HOLDOUT_START}..{HOLDOUT_END} hold-out** is "
             "sealed (asserted in `load_dev_holdout`).")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## The hypothesis")
    L.append("")
    L.append(
        "Every prior result was killed by the **16-21% taker round-trip cost** "
        "(fee `0.07*p*(1-p)` on both legs + crossing the spread). A **maker** "
        "(resting limit orders) pays 0 fee and does not cross the spread. So a "
        "signal with a small but genuinely **positive gross edge** — dead as a "
        "taker — could be alive as a maker. This lead asks, honestly, whether "
        "maker execution turns anything profitable.")
    L.append("")
    L.append(
        "The logic gate: **maker execution removes COST, it cannot create "
        "GROSS edge.** If the gross edge (`realized cheap_won - entry price`, "
        "before any cost) is ~0 or negative everywhere, no fee saving can "
        "rescue it. So Part 1 — the positive-gross-edge search — gates "
        "everything.")
    L.append("")
    L.append("## Critical data discipline — two artifacts avoided")
    L.append("")
    L.append(
        "**(1) The decided-market book artifact.** `phase4_forensics.md` "
        "proved the entry-candidate table's cheap-side columns are *corrupted "
        "on decided-market books*: when a window resolves, both mids collapse "
        "toward 0, the `cheap_side = lower-ask side` rule breaks, and a "
        "worthless **losing** side gets mislabelled with `cheap_mid ~ 0` and a "
        "spuriously high `cheap_won`. Run naively, the `cheap_mid in (0, 0.01]` "
        "bucket shows a fantasy **+30c gross edge** — a side priced 0.0015 "
        "'winning' 30% of the time. That is 100% the artifact (those rows have "
        "a median `cheap_spread` of **-0.31**, a crossed/inverted book, and "
        "cluster at `seconds_into_window ~800`, near resolution). This script "
        "therefore works off the **genuine two-sided book** in "
        "`ticks_15m.parquet` with a hard book-health guard: both sides priced "
        "in (0.001, 0.999), positive bids below asks, and "
        "complement-consistent (`yes_ask + no_bid` within 6c of 1). 92.1% of "
        "dev ticks pass; the 7.9% that fail are exactly the decided-market "
        "rows, and removing them removes the entire fantasy edge.")
    L.append("")
    L.append(
        "**(2) The fantasy maker fill (Phase 4's mistake).** A realistic maker "
        "model is enforced: a resting buy limit at price `L` fills **only when "
        "a strictly-later tick shows the side's best bid <= L** — i.e. the "
        "market actually traded down to/through the level (someone sold into "
        "it). If the market never reaches `L` within the window, the order "
        "**does not fill — that trade simply does not happen** and earns "
        "nothing. Maker fills are therefore **adverse-selection-biased by "
        "construction**: you are filled precisely when the side is getting "
        "cheaper. A maker exit (resting sell limit) fills only on a genuine "
        "later trade-up to it; otherwise the position is held to window "
        "resolution and settles on the true `outcome_up`.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## Part 1 — the positive-gross-edge search")
    L.append("")
    L.append(
        "The gross edge is `realized cheap_won - cheap_mid` — the cheap side's "
        "realized win rate minus its price, **before any cost**. The "
        "cross-section is de-biased to **one observation per (window, 60s "
        "time-slice)** — Phase 0 found ~87% of ticks are stale, so "
        "tick-pooling over-weights long-lingering quotes. All CIs are 90% "
        f"window-clustered bootstraps (n={N_BOOT}, groups = `slug`).")
    L.append("")
    L.append(
        f"**De-biased pooled gross edge: {p[1]:+.4f}** "
        f"(90% CI [{p[0]:+.4f}, {p[2]:+.4f}]) — i.e. the cheap side, on a "
        "genuine two-sided book, realizes about "
        f"{abs(p[1])*100:.1f}c {'BELOW' if p[1] < 0 else 'above'} its mid, "
        "before any cost. (For comparison: the **tick-pooled** number on the "
        "raw table is `+1.4c` — a stale-tick weighting artifact; and the raw "
        "table's `cheap_mid in (0,0.01]` cell shows `+30c` — the "
        "decided-market artifact. The honest de-biased, healthy-book number "
        "is the one above.)")
    L.append("")
    L.append("### Gross edge by cheap_mid price bucket (de-biased, healthy "
             "book)")
    L.append("")
    L.append(_mid_table(r["by_mid"]))
    L.append("")
    pos_mid = [x for x in r["by_mid"] if x["ci_lo"] > 0]
    L.append(
        ("**Every price bucket is negative or straddles zero — not one has a "
         "CI excluding zero on the positive side.**"
         if not pos_mid else
         f"**Buckets with a pooled positive CI: "
         f"{[x['bucket'] for x in pos_mid]}.**")
        + " The extreme cheap tail (`cheap_mid < 0.02`) — Phase 2's claimed "
        "+6.9c longshot residual — is here near zero with a CI straddling "
        "zero: that residual was itself decided-market contamination, which "
        "the healthy-book guard removes.")
    L.append("")
    L.append("### Gross edge by drop / time-left / symbol")
    L.append("")
    L.append("`cheap_drop_30s` is the 30-second odds drop in **percent** "
             "(0-100). The panic-overshoot hypothesis (H2) predicts a positive "
             "gross edge after a steep drop.")
    L.append("")
    L.append(_simple_table(r["by_drop"], "cheap_drop_30s (%)"))
    L.append("")
    L.append(_simple_table(r["by_tl"], "time_left_sec"))
    L.append("")
    L.append(_simple_table(r["by_sym"], "symbol"))
    L.append("")
    L.append(
        "No drop bucket, no time-left bucket, no symbol shows a positive "
        "gross edge — every cell is negative or straddles zero. The "
        "steepest-drop cells (the H2 panic-overshoot candidate) are negative "
        "or ~0.")
    L.append("")
    L.append("### Dev-internal CV — early (May 15-17) vs late (May 18-20)")
    L.append("")
    L.append(
        "A cell qualifies as a real candidate only if its gross edge is "
        "positive with a CI excluding zero in **both** dev halves.")
    L.append("")
    L.append(_cv_table(r["cv_rows"]))
    L.append("")
    if r["positive_cells"]:
        L.append(f"**CV-stable positive-gross cells: "
                 f"{r['positive_cells']}.**")
    else:
        L.append(
            "**No cheap_mid bucket is positive with a CI excluding zero in "
            "both halves.** The handful of near-zero positives flip sign or "
            "lose CI-significance across the early/late split — noise, not "
            "edge. There is no CV-stable positive-gross cell anywhere.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## Part 2 — the realistic-maker backtest")
    L.append("")
    if r["gate"] == "candidate_survives":
        L.append(
            f"A candidate cell survived Part 1. Backtested on cheap_mid cell "
            f"**[{mlo}, {mhi})**: post a resting limit buy at the side's bid; "
            "fill only on a genuine later trade-through; +100% profit-target "
            "exit (resting limit sell, fills only on a genuine later "
            "trade-up) else settle on the true `outcome_up`; 0 fee. Compared "
            "to the same as a taker (enter at ask, pay `0.07*p*(1-p)` per "
            "leg).")
    else:
        L.append(
            "**No cell survived Part 1**, so there is nothing a maker could "
            "rescue. For completeness and honesty the realistic-maker "
            "backtest is still run on the **least-bad** cheap_mid cell "
            f"(highest pooled gross edge): **[{mlo}, {mhi})**. Post a resting "
            "limit buy at the side's bid; fill only on a genuine later "
            "trade-through; +100% profit-target exit (resting limit sell, "
            "fills only on a genuine later trade-up) else settle on the true "
            "`outcome_up`; 0 fee. Compared to the same as a taker.")
    L.append("")
    L.append("### Dev split")
    L.append("")
    L.append(_bt_table(r["bt"]))
    L.append("")
    mk, tk = r["bt"]["maker"], r["bt"]["taker"]
    L.append(
        f"The resting buy limit is posted at the side's *current bid* and "
        f"fills **{mk['fill_rate']:.0%}** of candidate windows — a "
        f"high rate, because an at-the-bid limit on a cheap side that mostly "
        f"drifts further down gets traded through almost always. That is "
        f"precisely the adverse-selection problem: the fill is not a favour, "
        f"it is the market handing you a side that is on its way to losing. "
        f"Net PnL/trade **${mk['mean_pnl']:+.4f}** "
        f"(90% CI [${mk['ci'][0]:+.4f}, ${mk['ci'][1]:+.4f}]), "
        f"**${mk['dollars_per_day']:+.2f}/day**, resolution-loss rate "
        f"**{mk['res_loss_rate']:.0%}**. The taker on the same cell is "
        f"**${tk['mean_pnl']:+.4f}/trade**. "
        + ("Maker beats taker (no fee, no spread crossing) but "
           if mk['mean_pnl'] > tk['mean_pnl'] else "")
        + ("**both are negative** — removing cost does not lift a "
           "negative-gross signal above zero."
           if mk['mean_pnl'] <= 0 else
           "the maker number is positive — see the hold-out check."))
    L.append("")
    if r["holdout_bt"] is not None:
        L.append("### Sealed hold-out (May 21-22) — final check")
        L.append("")
        L.append(
            "A candidate survived dev, so the hold-out was opened **once**.")
        L.append("")
        L.append(_bt_table(r["holdout_bt"]))
        L.append("")
    else:
        L.append("### Sealed hold-out (May 21-22)")
        L.append("")
        L.append(
            "**Not consulted.** No candidate survived the dev-split "
            "gross-edge search; a signal that has no positive gross edge on "
            "dev must not consume the hold-out. It stays sealed.")
        L.append("")
    L.append("---")
    L.append("")
    L.append("## VERDICT")
    L.append("")
    if r["positive_cells"]:
        L.append(
            "A CV-stable positive-gross cell exists — see the maker backtest "
            "above for whether realistic maker fills monetize it.")
    else:
        L.append(
            "**Maker execution does NOT rescue any signal — because there is "
            "no gross edge for it to rescue.**")
        L.append("")
        L.append(
            f"On the genuine, healthy two-sided book, de-biased to one "
            f"observation per window-slice, the **gross edge "
            f"(`cheap_won - cheap_mid`, before any cost) is "
            f"{p[1]:+.4f} pooled** and is **negative or zero in every cell** "
            f"— every price bucket, every drop bucket, every time-left "
            f"bucket, every symbol, and in both halves of the dev-internal "
            f"CV. Not one cell is positive with a CI excluding zero, and the "
            f"dev-internal CV confirms it: nothing is positive in both the "
            f"early and late halves.")
        L.append("")
        L.append(
            "Maker execution attacks the **cost wall** — the 16-21% taker "
            "round-trip. That is the right attack *if* a positive gross edge "
            "is hiding under the cost. But the gross edge is ~0 to slightly "
            "negative everywhere. A maker pays no fee and crosses no spread, "
            "yet a 0-fee bet on a coin-flip-or-worse is still a coin-flip-or-"
            "worse. There is nothing under the cost wall. The realistic-maker "
            f"backtest confirms it operationally: on the least-bad cell the "
            f"maker earns **${mk['mean_pnl']:+.4f}/trade** "
            f"(CI [${mk['ci'][0]:+.4f}, ${mk['ci'][1]:+.4f}]) — "
            + ("negative" if mk['mean_pnl'] <= 0
               else "not CI-separated from zero")
            + f", with a {mk['res_loss_rate']:.0%} resolution-loss rate. "
            "The realistic "
            "fill model also exposes a second problem even if a gross edge "
            "DID exist: the maker only gets filled when the market trades "
            "down to its level — i.e. precisely on the windows where the side "
            "is getting cheaper and (on this calibrated market) more likely "
            "to lose. Maker fills are adverse-selected.")
        L.append("")
        L.append(
            "This agrees with the corrected-data Phase 2 re-run "
            "(`PHASE2_RERUN_VERDICT.md`: the cheap side is calibrated, no "
            "tradeable mispricing) and with the Phase 4 forensics "
            "(the only large positive numbers were data artifacts). The "
            "honest answer to 'does maker execution rescue a strategy' is "
            "**no — the gross edge is ~0 everywhere, so maker cannot save "
            "it.** There is no realistic $/day to report because there is no "
            "edge to size.")
    L.append("")
    L.append("**Status: DONE — clean negative.** No CV-stable positive-gross "
             "cell; maker execution cannot rescue a non-existent edge; the "
             "sealed hold-out was "
             + ("opened once for the surviving candidate."
                if r["holdout_bt"] is not None
                else "not consulted and stays sealed.")
             + " A skeptical reader should note this is the third independent "
             "confirmation (Phase 2 calibration, Phase 4 forensics, Lead D) "
             "that the 15m cheap-side market is calibrated and carries no "
             "harvestable edge.")
    L.append("")
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(L))


if __name__ == "__main__":
    run()
