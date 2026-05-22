"""Phase 4 profit-target exit forensics (Task 8d).

Decisive forensic audit of `reconstruction.md`'s positive verdict: the patient
policy's whole +EV rests on 335 profit-target exits averaging +$15.79. This
probe settles whether those exits are genuinely tradeable.

It re-runs `research.analysis.patient_policy` on the corrected dev data, then
joins every profit-target exit tick back to the FULL order book in
`ticks_15m.parquet` (the entry-candidate table drops the held side's bid depth,
the spread, and the opposing book — so the patient simulator could never see
whether it was filling into a real book).

Five questions, each settled with numbers:

  Q1  Reconcile Phase 3 (loses) vs Phase 4 (wins) on a common basis.
  Q2  The exit-tick book health for the 335 winners — bid, bid depth, spread,
      degenerate / decided-market / one-sided / crossed fraction.
  Q3  Honest re-pricing: sell at the genuine bid, require bid depth >= stake or
      walk the book, exclude decided/degenerate books, cap the post-jump fill.
  Q4  Look-ahead audit of `simulate_window`.
  Q5  Real example paths, tick by tick, entry to exit.
  Q6  Stale-book check at the exit tick.

Run:  uv run python -m research.audit.phase4_exit_forensics
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis.patient_policy import (
    PatientPolicy,
    _load_dev_candidates,
    _taker_fee,
    run_policy,
    simulate_window,
)

STAKE_USD = 10.0
_TICKS_PATH = "data/research/ticks_15m.parquet"

BASE = dict(
    entry_mid_min=0.10, entry_mid_max=0.30, min_drop_30s=10.0,
    max_sigma_proximity=None, max_proximity_pct=None,
    min_time_left_sec=420.0, profit_target_pct=75.0, breakeven_exit=True,
)


# --------------------------------------------------------------------------
# helpers — held-side book reconstruction from the FULL tick row
# --------------------------------------------------------------------------
def held_book(row: pd.Series, side: str) -> dict:
    """Best bid / ask / bid-depth / ask-depth of the *held* side from a full
    ticks_15m row. Held side may be YES or NO."""
    if side == "YES":
        return dict(
            bid=row["yes_best_bid"], ask=row["yes_best_ask"],
            bid_depth=row["yes_bid_depth"], ask_depth=row["yes_ask_depth"],
            opp_bid=row["no_best_bid"], opp_ask=row["no_best_ask"],
        )
    return dict(
        bid=row["no_best_bid"], ask=row["no_best_ask"],
        bid_depth=row["no_bid_depth"], ask_depth=row["no_ask_depth"],
        opp_bid=row["yes_best_bid"], opp_ask=row["yes_best_ask"],
    )


def classify_book(b: dict) -> str:
    """Classify a held-side book at the exit tick.

    Categories (Phase 0 Task 3 vocabulary):
      decided_market  : the book has resolved — held ask ~0 or ~1, no real
                        two-sided quote (yes_ask==0 family).
      crossed         : bid > ask on the held side.
      one_sided       : bid at the 0.01 floor or <=0 (no real bid) while ask
                        is a normal quote, or ask>=0.99 with no real ask.
      no_bid_depth    : a two-sided price book but zero USD resting at the bid.
      healthy         : a genuine two-sided book with depth at the bid.
    """
    bid, ask = b["bid"], b["ask"]
    bd = b["bid_depth"]
    # decided market: held side's ask collapsed to ~0 or pinned ~1
    if not np.isfinite(ask) or ask <= 0.001 or ask >= 0.999:
        return "decided_market"
    if not np.isfinite(bid):
        return "one_sided"
    # crossed
    if bid > ask + 1e-9:
        return "crossed"
    # one-sided: no real bid
    if bid <= 0.011:
        return "one_sided"
    # two-sided price book but no money resting at the bid
    if not np.isfinite(bd) or bd <= 0.0:
        return "no_bid_depth"
    return "healthy"


# --------------------------------------------------------------------------
# Q2 / Q3 — join exits to the full book, classify, honestly re-price
# --------------------------------------------------------------------------
def collect_pt_exits(dev: pd.DataFrame, ticks: pd.DataFrame) -> pd.DataFrame:
    """Run the taker baseline, return one row per profit-target exit with the
    full exit-tick book joined in."""
    pol = PatientPolicy(execution="taker", **BASE)

    # index the full ticks by (slug, second) for fast exit-tick lookup
    tk = ticks.copy()
    tk["sec"] = tk["seconds_into_window"]
    nan_sec = tk["sec"].isna()
    if nan_sec.any():
        tk.loc[nan_sec, "sec"] = 900.0 - tk.loc[nan_sec, "time_left_sec"]
    tk["sec"] = tk["sec"].astype(int)
    tk = tk.sort_values(["slug", "sec"])
    tk_by_slug = {s: g.reset_index(drop=True) for s, g in tk.groupby("slug", sort=False)}

    rows = []
    for _slug, win in dev.groupby("slug", sort=False):
        tr = simulate_window(win, pol)
        if tr is None or tr["exit_reason"] != "profit_target":
            continue
        slug = tr["slug"]
        side = tr["entry_side"]
        exit_sec = _exit_sec(win, pol, tr)
        full = tk_by_slug.get(slug)
        if full is None:
            continue
        cand = full[full["sec"] == exit_sec]
        if cand.empty:
            # nearest tick at or after exit_sec
            cand = full[full["sec"] >= exit_sec]
            if cand.empty:
                continue
        exrow = cand.iloc[0]
        b = held_book(exrow, side)
        # tick before the exit (for the jump check)
        prev = full[full["sec"] < exit_sec]
        prev_held_mid = np.nan
        prev_held_bid = np.nan
        if not prev.empty:
            pr = prev.iloc[-1]
            pb = held_book(pr, side)
            prev_held_mid = (pb["bid"] + pb["ask"]) / 2.0
            prev_held_bid = pb["bid"]
        rows.append(dict(
            slug=slug, side=side, symbol=tr.get("symbol"),
            entry_sec=tr["entry_sec"], exit_sec=exit_sec,
            entry_mid=tr["entry_mid"], entry_price=tr["entry_price"],
            sim_exit_price=tr["exit_price"], sim_pnl=tr["pnl_usd"],
            outcome_up=tr["outcome_up"],
            held_bid=b["bid"], held_ask=b["ask"],
            held_mid=(b["bid"] + b["ask"]) / 2.0,
            held_bid_depth=b["bid_depth"], held_ask_depth=b["ask_depth"],
            held_spread=b["ask"] - b["bid"],
            book_class=classify_book(b),
            prev_held_mid=prev_held_mid, prev_held_bid=prev_held_bid,
        ))
    return pd.DataFrame(rows)


def _exit_sec(win: pd.DataFrame, pol: PatientPolicy, tr: dict) -> int:
    """Recover the exit second the simulator used (entry_sec + seconds_held)."""
    return int(tr["entry_sec"] + tr["seconds_held"])


def honest_reprice(pt: pd.DataFrame, walk_book: bool = True,
                   exclude_degenerate: bool = True,
                   cap_jump: bool = True) -> pd.DataFrame:
    """Re-price each profit-target exit with strict, realistic taker fills.

    walk_book          : if held bid depth < position USD value, fill the
                         remainder 2c worse (toward 0), per Phase 0 Task 8.
    exclude_degenerate : decided_market / crossed / one_sided / no_bid_depth
                         exits cannot be sold -> they fall through to a HOLD
                         to resolution, settling on the true outcome.
    cap_jump           : if the exit tick's held bid jumped far past the
                         +75% target vs the previous tick, the simulator
                         could not realistically have caught the post-jump
                         quote; fill at the realistic level (the target, or
                         the previous tick's bid if that already cleared it).
    """
    out = []
    for _, r in pt.iterrows():
        side = r["side"]
        entry_price = r["entry_price"]          # entry cost per share incl fee
        entry_mid = r["entry_mid"]
        shares = STAKE_USD / max(entry_price, 1e-9)
        target_mid = entry_mid * 1.75           # +75% profit target on the mid
        won_side = ((side == "YES" and r["outcome_up"] == 1.0) or
                    (side == "NO" and r["outcome_up"] == 0.0))

        bc = r["book_class"]
        degenerate = bc in ("decided_market", "crossed", "one_sided", "no_bid_depth")

        if exclude_degenerate and degenerate:
            # cannot sell into this book — hold to resolution on the true outcome
            gross = 1.0 if won_side else 0.0
            proceeds = gross - _taker_fee(entry_price)
            reason = "held_to_resolution"
        else:
            sell_bid = r["held_bid"]
            if not np.isfinite(sell_bid) or sell_bid <= 0:
                # no usable bid -> resolution
                gross = 1.0 if won_side else 0.0
                proceeds = gross - _taker_fee(entry_price)
                reason = "held_to_resolution"
            else:
                # cap an unrealistic post-jump fill
                if cap_jump and np.isfinite(r["prev_held_bid"]):
                    # the simulator only fires when held_mid >= target.
                    # a realistic taker, scanning 1Hz, fills at the FIRST
                    # tick that reaches target. if the bid leapt from below
                    # target to far above it in one tick, cap the fill at
                    # max(target, prev tick bid).
                    realistic_cap = max(target_mid, r["prev_held_bid"])
                    if sell_bid > realistic_cap:
                        sell_bid = realistic_cap
                # walk the book if depth < position value
                pos_value = shares * sell_bid
                bd = r["held_bid_depth"]
                if walk_book and np.isfinite(bd) and bd < pos_value:
                    frac_at_best = max(min(bd / max(pos_value, 1e-9), 1.0), 0.0)
                    worse = max(sell_bid - 0.02, 0.0)
                    eff_bid = frac_at_best * sell_bid + (1 - frac_at_best) * worse
                else:
                    eff_bid = sell_bid
                proceeds = eff_bid - _taker_fee(eff_bid)
                reason = "sold"

        pnl = shares * (proceeds - entry_price)
        out.append(dict(
            slug=r["slug"], honest_reason=reason, honest_proceeds=proceeds,
            honest_pnl=pnl, sim_pnl=r["sim_pnl"], book_class=bc,
            won_side=won_side,
        ))
    return pd.DataFrame(out)


# --------------------------------------------------------------------------
# Q6 — staleness of the exit-tick book
# --------------------------------------------------------------------------
def staleness(pt: pd.DataFrame, ticks: pd.DataFrame) -> dict:
    """For each profit-target exit tick, count how many consecutive prior
    seconds carried the byte-identical held-side bid/ask (a frozen quote)."""
    tk = ticks.copy()
    tk["sec"] = tk["seconds_into_window"]
    nan_sec = tk["sec"].isna()
    if nan_sec.any():
        tk.loc[nan_sec, "sec"] = 900.0 - tk.loc[nan_sec, "time_left_sec"]
    tk["sec"] = tk["sec"].astype(int)
    tk = tk.sort_values(["slug", "sec"])
    by_slug = {s: g.reset_index(drop=True) for s, g in tk.groupby("slug", sort=False)}

    stale_runs = []
    for _, r in pt.iterrows():
        full = by_slug.get(r["slug"])
        if full is None:
            continue
        side = r["side"]
        bid_col = "yes_best_bid" if side == "YES" else "no_best_bid"
        ask_col = "yes_best_ask" if side == "YES" else "no_best_ask"
        upto = full[full["sec"] <= r["exit_sec"]].reset_index(drop=True)
        if upto.empty:
            continue
        ex_bid = upto[bid_col].iloc[-1]
        ex_ask = upto[ask_col].iloc[-1]
        run = 0
        for k in range(len(upto) - 2, -1, -1):
            if (upto[bid_col].iloc[k] == ex_bid
                    and upto[ask_col].iloc[k] == ex_ask):
                run += 1
            else:
                break
        stale_runs.append(run)
    sr = np.array(stale_runs)
    return dict(
        n=len(sr),
        median_stale_sec=float(np.median(sr)) if len(sr) else float("nan"),
        mean_stale_sec=float(np.mean(sr)) if len(sr) else float("nan"),
        frac_fresh=float((sr == 0).mean()) if len(sr) else float("nan"),
        frac_stale_ge5=float((sr >= 5).mean()) if len(sr) else float("nan"),
        frac_stale_ge30=float((sr >= 30).mean()) if len(sr) else float("nan"),
        p90_stale=float(np.percentile(sr, 90)) if len(sr) else float("nan"),
    )


# --------------------------------------------------------------------------
# Q5 — example paths
# --------------------------------------------------------------------------
def example_paths(pt: pd.DataFrame, ticks: pd.DataFrame, n: int = 8) -> str:
    """Print full per-tick paths for a spread of profit-target winners."""
    tk = ticks.copy()
    tk["sec"] = tk["seconds_into_window"]
    nan_sec = tk["sec"].isna()
    if nan_sec.any():
        tk.loc[nan_sec, "sec"] = 900.0 - tk.loc[nan_sec, "time_left_sec"]
    tk["sec"] = tk["sec"].astype(int)
    by_slug = {s: g.sort_values("sec").reset_index(drop=True)
               for s, g in tk.groupby("slug", sort=False)}

    # pick a spread: some healthy, some degenerate, sorted by exit book class
    pick = pd.concat([
        pt[pt.book_class == "healthy"].head(3),
        pt[pt.book_class == "decided_market"].head(3),
        pt[pt.book_class.isin(["one_sided", "crossed", "no_bid_depth"])].head(2),
    ])
    lines = []
    for _, r in pick.iterrows():
        full = by_slug.get(r["slug"])
        if full is None:
            continue
        side = r["side"]
        bid_c = "yes_best_bid" if side == "YES" else "no_best_bid"
        ask_c = "yes_best_ask" if side == "YES" else "no_best_ask"
        bd_c = "yes_bid_depth" if side == "YES" else "no_bid_depth"
        ad_c = "yes_ask_depth" if side == "YES" else "no_ask_depth"
        lines.append(
            f"\n--- {r['slug']}  side={side}  entry_sec={r['entry_sec']} "
            f"entry_mid={r['entry_mid']:.3f}  exit_sec={r['exit_sec']} "
            f"book={r['book_class']}  outcome_up={r['outcome_up']:.0f}  "
            f"sim_pnl=${r['sim_pnl']:.2f}")
        seg = full[(full["sec"] >= r["entry_sec"] - 2)
                   & (full["sec"] <= r["exit_sec"] + 2)]
        # thin to <=24 rows for readability
        if len(seg) > 24:
            idx = np.linspace(0, len(seg) - 1, 24).astype(int)
            seg = seg.iloc[np.unique(idx)]
        lines.append("  sec  held_bid held_ask held_mid bid_dep ask_dep   cb_price")
        for _, t in seg.iterrows():
            mid = (t[bid_c] + t[ask_c]) / 2.0
            mark = ""
            if t["sec"] == r["entry_sec"]:
                mark = " <-ENTRY"
            if t["sec"] == r["exit_sec"]:
                mark = " <-EXIT"
            lines.append(
                f"  {int(t['sec']):4d}  {t[bid_c]:7.3f} {t[ask_c]:8.3f} "
                f"{mid:8.3f} {t[bd_c]:7.1f} {t[ad_c]:7.1f} {t['coinbase_price']:10.2f}{mark}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Q1 — Phase 3 vs Phase 4 reconciliation on a common basis
# --------------------------------------------------------------------------
def reconcile_phase3_phase4(dev: pd.DataFrame) -> dict:
    """Run the Phase 4 policy at Phase 3's profit targets and exit assumptions
    to pin which lever flips the sign."""
    out = {}
    # Phase 4 baseline at different profit targets
    for pt_pct in (25.0, 50.0, 75.0, 100.0):
        for execu in ("taker", "maker"):
            pol = PatientPolicy(execution=execu,
                                **{**BASE, "profit_target_pct": pt_pct})
            tr = run_policy(dev, pol)
            ptt = tr[tr.exit_reason == "profit_target"]
            out[(pt_pct, execu)] = dict(
                n=len(tr),
                total=float(tr["pnl_usd"].sum()),
                mean=float(tr["pnl_usd"].mean()),
                wr=float(tr["won"].mean()),
                pt_n=len(ptt),
                pt_total=float(ptt["pnl_usd"].sum()),
                pt_mean=float(ptt["pnl_usd"].mean()) if len(ptt) else float("nan"),
            )
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def run() -> None:
    print("Loading dev candidates + full ticks ...")
    dev = _load_dev_candidates()
    ticks = pd.read_parquet(_TICKS_PATH)
    # restrict ticks to dev windows for speed
    dev_slugs = set(dev["slug"].unique())
    ticks = ticks[ticks["slug"].isin(dev_slugs)].copy()
    print(f"  {len(dev):,} dev candidate ticks, {len(ticks):,} full ticks")

    # ---- Q1 ----
    print("\n" + "=" * 70)
    print("Q1 — Phase 3 vs Phase 4 reconciliation")
    print("=" * 70)
    rec = reconcile_phase3_phase4(dev)
    print(f"{'PT%':>5} {'exec':>6} {'n':>5} {'total':>10} {'mean':>8} "
          f"{'wr':>6} {'pt_n':>5} {'pt_total':>10} {'pt_mean':>9}")
    for (pt_pct, execu), d in rec.items():
        print(f"{pt_pct:5.0f} {execu:>6} {d['n']:5d} {d['total']:10.1f} "
              f"{d['mean']:8.3f} {d['wr']:6.3f} {d['pt_n']:5d} "
              f"{d['pt_total']:10.1f} {d['pt_mean']:9.3f}")

    # ---- Q2 ----
    print("\n" + "=" * 70)
    print("Q2 — exit-tick book health for the profit-target winners")
    print("=" * 70)
    pt = collect_pt_exits(dev, ticks)
    print(f"profit-target exits joined to full book: {len(pt)}")
    print("\nbook classification:")
    vc = pt["book_class"].value_counts()
    for k, v in vc.items():
        print(f"  {k:18s} {v:4d}  ({v/len(pt)*100:.1f}%)")
    degenerate = pt["book_class"].isin(
        ["decided_market", "crossed", "one_sided", "no_bid_depth"])
    print(f"  -> DEGENERATE total: {degenerate.sum()} "
          f"({degenerate.mean()*100:.1f}%)")
    print("\nexit-tick held BID distribution:")
    print(pt["held_bid"].describe(percentiles=[.1, .25, .5, .75, .9]).to_string())
    print("\nexit-tick held BID DEPTH (USD) distribution:")
    print(pt["held_bid_depth"].describe(
        percentiles=[.1, .25, .5, .75, .9]).to_string())
    print(f"  exits with bid depth < $10 stake: "
          f"{(pt['held_bid_depth'] < 10).mean()*100:.1f}%")
    print(f"  exits with bid depth == 0:        "
          f"{(pt['held_bid_depth'] <= 0).mean()*100:.1f}%")
    print("\nexit-tick held SPREAD distribution:")
    print(pt["held_spread"].describe(
        percentiles=[.1, .25, .5, .75, .9]).to_string())
    print(f"  crossed (spread<0): {(pt['held_spread'] < 0).mean()*100:.1f}%")

    # ---- Q6 ----
    print("\n" + "=" * 70)
    print("Q6 — staleness of the exit-tick book")
    print("=" * 70)
    st = staleness(pt, ticks)
    for k, v in st.items():
        print(f"  {k:20s} {v}")

    # ---- Q3 ----
    print("\n" + "=" * 70)
    print("Q3 — honest re-pricing of the profit-target exits")
    print("=" * 70)
    pol = PatientPolicy(execution="taker", **BASE)
    all_tr = run_policy(dev, pol)
    n_all = len(all_tr)
    non_pt_pnl = all_tr[all_tr.exit_reason != "profit_target"]["pnl_usd"].sum()
    sim_pt_pnl = all_tr[all_tr.exit_reason == "profit_target"]["pnl_usd"].sum()
    print(f"baseline: n={n_all}, sim total=${all_tr['pnl_usd'].sum():.0f}, "
          f"sim PT total=${sim_pt_pnl:.0f}, non-PT total=${non_pt_pnl:.0f}")

    scenarios = [
        ("A: sim (reported)", dict(walk_book=False, exclude_degenerate=False,
                                   cap_jump=False)),
        ("B: +walk-the-book", dict(walk_book=True, exclude_degenerate=False,
                                   cap_jump=False)),
        ("C: +cap post-jump", dict(walk_book=True, exclude_degenerate=False,
                                   cap_jump=True)),
        ("D: +exclude degenerate books", dict(walk_book=True,
                                              exclude_degenerate=True,
                                              cap_jump=True)),
    ]
    print(f"\n{'scenario':32s} {'PT_total':>10} {'overall':>10} "
          f"{'mean/trade':>11}")
    from research.lib.stats import window_clustered_bootstrap
    for name, kw in scenarios:
        rep = honest_reprice(pt, **kw)
        pt_total = rep["honest_pnl"].sum()
        overall = pt_total + non_pt_pnl
        mean_trade = overall / n_all
        print(f"{name:32s} {pt_total:10.0f} {overall:10.0f} "
              f"{mean_trade:11.3f}")
    # CI on the honest (D) scenario, window-clustered over ALL trades
    repD = honest_reprice(pt, walk_book=True, exclude_degenerate=True,
                          cap_jump=True)
    honest_by_slug = dict(zip(repD["slug"], repD["honest_pnl"]))
    all_tr = all_tr.copy()
    all_tr["honest_pnl"] = all_tr.apply(
        lambda r: honest_by_slug.get(r["slug"], r["pnl_usd"])
        if r["exit_reason"] == "profit_target" else r["pnl_usd"], axis=1)
    lo, mid, hi = window_clustered_bootstrap(
        all_tr["honest_pnl"].values, all_tr["slug"].values, n=4000, seed=0)
    print(f"\nHONEST scenario D — overall mean PnL/trade: "
          f"${all_tr['honest_pnl'].mean():.3f}  "
          f"90% window-clustered CI [${lo:.3f}, ${hi:.3f}]")
    print(f"  total ${all_tr['honest_pnl'].sum():.0f} over {n_all} trades")

    # how the degenerate exits actually resolve
    repD2 = repD.copy()
    held = repD2[repD2.honest_reason == "held_to_resolution"]
    print(f"\n  degenerate/unsellable PT exits forced to hold to resolution: "
          f"{len(held)}")
    if len(held):
        print(f"    of those, held side ultimately WON: "
              f"{held['won_side'].mean()*100:.1f}%")
        print(f"    mean honest PnL on those: ${held['honest_pnl'].mean():.2f}")

    # ---- Q5 ----
    print("\n" + "=" * 70)
    print("Q5 — example profit-target winning paths")
    print("=" * 70)
    print(example_paths(pt, ticks))

    print("\nDone.")


if __name__ == "__main__":
    run()
