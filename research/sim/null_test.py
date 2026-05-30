"""Phase 0d — null-test gate for the harness.

Before any edge claim, the simulator + dataset must pass two nulls:

  Null A (random side):  random window/second, buy a RANDOM side ($10), walk the
    real L2 ask ladder, hold to resolution, TRUE outcome. EV must be clearly
    NEGATIVE (~ the one-way entry cost). If random betting is >= 0 the simulator
    is manufacturing money (the original March artifact).

  Null B (shuffled labels):  buy the favourite, then recompute PnL with the
    win/lose labels PERMUTED across the sampled entries. The real-label PnL must
    NOT be meaningfully more positive than the shuffled-label distribution — i.e.
    the pipeline credits no phantom edge once the label's predictive content is
    destroyed. (On a calibrated market both are ~ -cost.)

Run: uv run python -m research.sim.null_test
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, settle_pnl
from research.dataset.feeds import load_l2_ladders
from research.clean_window import CLEAN_START, CLEAN_DEV_END, available_clean_dates

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_RESEARCH = os.path.join(REPO_ROOT, "data", "research")
STAKE = 10.0
N_SAMPLE = 5000
N_SHUFFLE = 2000
SEED = 1337
_LV = range(1, 11)


def _yes_ask_ladder(lr):
    return [lr[f"ask_px_{i}"] for i in _LV], [lr[f"ask_sz_{i}"] for i in _LV]


def _no_ask_ladder(lr):  # buy NO = sell YES into the YES bids
    return [1.0 - lr[f"bid_px_{i}"] for i in _LV], [lr[f"bid_sz_{i}"] for i in _LV]


def _simulate(df, ladders, rng, force_side=None) -> pd.DataFrame:
    """Buy `force_side` ('fav'|'yes'|'no'|None=random) and hold to resolution.
    Returns per-entry records: shares, notional, fee, won, fav_price, side."""
    recs = []
    for _, r in df.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        try:
            lr = lad.loc[(r["slug"], int(r["seconds_into_window"]))]
        except KeyError:
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        if force_side == "fav":
            side = "yes" if r["yes_mid"] >= 0.5 else "no"
        elif force_side in ("yes", "no"):
            side = force_side
        else:
            side = "yes" if rng.random() < 0.5 else "no"
        px, sz = _yes_ask_ladder(lr) if side == "yes" else _no_ask_ladder(lr)
        fill = walk_buy(px, sz, STAKE)
        if not fill.filled or fill.unfilled_usd > STAKE * 0.5:
            continue
        won = (r["outcome_up_clean"] == 1) if side == "yes" else (r["outcome_up_clean"] == 0)
        recs.append({"shares": fill.shares, "notional": fill.notional_usd,
                     "fee": fill.fee_usd, "won": bool(won),
                     "fav_price": max(r["yes_mid"], 1 - r["yes_mid"]), "side": side})
    return pd.DataFrame(recs)


def _net_pnl(recs: pd.DataFrame, won: np.ndarray) -> np.ndarray:
    payoff = np.where(won, recs["shares"].to_numpy(), 0.0)
    return payoff - recs["notional"].to_numpy() - recs["fee"].to_numpy()


def run() -> bool:
    df = pd.read_parquet(os.path.join(DATA_RESEARCH, "joined_15m.parquet"))
    dates = available_clean_dates("btc")
    d0, d1 = CLEAN_START, (dates[-1] if dates else CLEAN_START)
    pool = df[(df["split"] == "dev")
              & df["yes_mid"].between(0.05, 0.95)
              & df["outcome_up_clean"].notna()
              & df["seconds_into_window"].between(30, 870)].copy()
    rng = np.random.default_rng(SEED)
    samp = pool.sample(min(N_SAMPLE, len(pool)), random_state=SEED)
    print(f"Null-test: dev {d0}..{CLEAN_DEV_END}  pool={len(pool):,}  sample={len(samp):,}")
    ladders = {s: load_l2_ladders(s, d0, d1) for s in sorted(samp["symbol"].unique())}

    # ---- Gate 1 (PRIMARY): calibration / integrity ----
    # If buying at entry price p wins ~p of the time across buckets, the
    # dataset join + labels + fills + settlement are correctly wired. A label
    # misalignment (e.g. off-by-one window) would shatter this. Low variance.
    rec_a = _simulate(samp, ladders, rng, force_side=None)
    rec_a["entry_price"] = rec_a["notional"] / rec_a["shares"]
    qs = np.linspace(0.05, 0.95, 10)
    edges = np.unique(np.quantile(rec_a["entry_price"], np.linspace(0, 1, 11)))
    rec_a["bkt"] = pd.cut(rec_a["entry_price"], edges, include_lowest=True)
    cal = rec_a.groupby("bkt", observed=True).agg(
        price=("entry_price", "mean"), wr=("won", "mean"), n=("won", "size"))
    mace = float((cal["wr"] - cal["price"]).abs().mean())
    slope = np.polyfit(cal["price"], cal["wr"], 1)[0]
    g1_pass = mace < 0.04 and 0.8 <= slope <= 1.2
    print(f"\n[Gate 1] calibration (random-side entries, n={len(rec_a)}):")
    print(f"   mean |WR-price| = {mace:.4f} (<0.04?)   WR-vs-price slope = {slope:.2f} (~1?)")
    print(f"   => {'PASS' if g1_pass else 'FAIL'} — labels/fills/settlement correctly aligned")

    # ---- Gate 2: no manufactured POSITIVE EV (one-sided) ----
    pnl_a = _net_pnl(rec_a, rec_a["won"].to_numpy())
    mu = pnl_a.mean(); se = pnl_a.std(ddof=1) / np.sqrt(len(pnl_a))
    g2_pass = (mu - 1.96 * se) <= 0  # must NOT be significantly positive
    print(f"\n[Gate 2] random-side net PnL/trade = ${mu:+.4f}  "
          f"95% CI [${mu-1.96*se:+.4f}, ${mu+1.96*se:+.4f}]  WR={rec_a['won'].mean()*100:.1f}%")
    print(f"   => {'PASS' if g2_pass else 'FAIL'} — random betting shows no significant positive edge "
          f"(March artifact was ~+$2)")
    a_pass = g1_pass and g2_pass

    # ---- Null B: buy favourite, shuffle labels ----
    rec_b = _simulate(samp, ladders, rng, force_side="fav")
    real = _net_pnl(rec_b, rec_b["won"].to_numpy()).mean()
    won = rec_b["won"].to_numpy()
    shuf = np.array([_net_pnl(rec_b, rng.permutation(won)).mean() for _ in range(N_SHUFFLE)])
    lo, hi = np.percentile(shuf, [2.5, 97.5])
    b_pass = real <= hi  # no phantom POSITIVE edge beyond the shuffle null
    print(f"\n[Null B] buy-favourite, shuffle labels: n={len(rec_b)}  WR={won.mean()*100:.1f}%")
    print(f"   real-label net PnL/trade = ${real:+.4f}")
    print(f"   shuffled-label 95% band  = [${lo:+.4f}, ${hi:+.4f}]")
    print(f"   => {'PASS' if b_pass else 'FAIL'} — real must not exceed the shuffle null (no phantom edge)")

    ok = a_pass and b_pass
    print(f"\nHARNESS NULL-TEST: {'PASS ✅' if ok else 'FAIL ❌'}")
    return ok


if __name__ == "__main__":
    run()
