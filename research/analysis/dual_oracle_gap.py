"""A1 — quantify the Coinbase-signal vs Chainlink-outcome FLIP risk for det_d12.

det_d12 fires on a Coinbase signal but Polymarket settles on Chainlink. This script takes the
exact det_d12 entry population (last 0-180s, |cb_dist|>=12bps, book&spot consistent, fav_ask in
[0.50,0.85], buy the favourite), reconstructs the Chainlink view at entry (dual_oracle_features),
and reports, per slice, how often the trade FLIPS between the Coinbase outcome (what the paper
twin/backtest historically used) and the Chainlink outcome (what real money settles on) — plus
the Chainlink win-rate and EV/tr in each slice.

The payoff is favourite-longshot: a win pays ~+$1, a loss costs the full ~$5 stake, so a flip is
a ~$6 swing. The deliverable is the table that answers "which det_d12 entries should the
dual-oracle gate drop." Run: uv run python -m research.analysis.dual_oracle_gap
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.analysis import edge_lab
from research.analysis.dual_oracle_features import entry_cl_dist, load_chainlink_aged

STAKE = 10.0
FEE = 0.07
WINDOW_S = 900

# det_d12 entry gate (mirrors determinism_state.on_tick for mode=consistent)
T_MIN, T_MAX = 1, 180
DIST_MIN = 12.0
ASK_LO, ASK_HI = 0.50, 0.85


def _pnl(entry_ask: np.ndarray, won: np.ndarray, stake: float = STAKE) -> np.ndarray:
    shares = stake / entry_ask
    fee = FEE * entry_ask * (1 - entry_ask) * shares
    return np.where(won == 1, shares - stake - fee, -stake - fee)


def build() -> pd.DataFrame:
    """One row per det_d12 entry, with both oracle outcomes + the entry Chainlink view."""
    b = edge_lab.load_base()
    cand = b[(b["time_left_sec"] >= T_MIN) & (b["time_left_sec"] <= T_MAX)
             & (b["abs_dist_bps"] >= DIST_MIN) & (b["consistent"])
             & (b["fav_ask"].between(ASK_LO, ASK_HI))]
    dd = edge_lab.first_tick(cand, (cand["yes_mid"] >= 0.5).to_numpy())
    aug = entry_cl_dist(dd, base=b)

    # entry ask actually paid (favourite side) at the entry tick
    feat = b[["slug", "seconds_into_window", "fav_ask", "outcome_up_clean"]].drop_duplicates(
        ["slug", "seconds_into_window"])
    aug = aug.merge(feat, left_on=["slug", "entry_sec"],
                    right_on=["slug", "seconds_into_window"], how="left").drop(
        columns=["seconds_into_window"], errors="ignore")

    # Chainlink outcome (Polymarket-true)
    aug = aug.merge(edge_lab.cl_outcomes(), on="slug", how="left")  # adds cl_up

    by = aug["buy_yes"].astype(bool).to_numpy()
    cb_up = aug["outcome_up_clean"].to_numpy()        # Coinbase outcome (0/1)
    cl_up = aug["cl_up"].to_numpy()                    # Chainlink outcome (0/1)
    aug["won_cb"] = np.where(by, cb_up == 1, cb_up == 0).astype(float)
    aug["won_cl"] = np.where(by, cl_up == 1, cl_up == 0).astype(float)
    aug["flip"] = (aug["won_cb"] != aug["won_cl"]).astype(float)
    aug["time_left"] = WINDOW_S - aug["entry_sec"].astype(int)
    ask = aug["fav_ask"].to_numpy("f8")
    aug["pnl_cl"] = _pnl(ask, aug["won_cl"].to_numpy())
    aug["pnl_cb"] = _pnl(ask, aug["won_cb"].to_numpy())
    return aug


def _bucket(df: pd.DataFrame, col: str, bins, label_abs=False) -> None:
    x = df[col].abs() if label_abs else df[col]
    cats = pd.cut(x, bins)
    g = df.groupby(cats, observed=True)
    print(f"\n  by {col}{' (abs)' if label_abs else ''}:")
    print(f"    {'bucket':>16} {'n':>4} {'flip%':>6} {'WR_cl%':>7} {'EV_cl$':>7} {'EV_cb$':>7}")
    for cat, s in g:
        if len(s) == 0:
            continue
        print(f"    {str(cat):>16} {len(s):>4} {s['flip'].mean()*100:>6.1f} "
              f"{s['won_cl'].mean()*100:>7.1f} {s['pnl_cl'].mean():>+7.2f} {s['pnl_cb'].mean():>+7.2f}")


def run():
    df = build()
    n_all = len(df)
    df = df[df["cl_available"] & df["cl_up"].notna() & df["outcome_up_clean"].notna()].copy()
    print(f"=== det_d12 entry population (last {T_MAX}s, |cb_dist|>={DIST_MIN}bps, consistent, "
          f"fav_ask [{ASK_LO},{ASK_HI}]) ===")
    print(f"total entries: {n_all}  with Chainlink+outcome resolved: {len(df)} "
          f"({len(df)/max(n_all,1)*100:.0f}%)   [stake ${STAKE:.0f}]")
    print(f"\nHEADLINE  flip%={df['flip'].mean()*100:.1f}  "
          f"WR_cl={df['won_cl'].mean()*100:.1f}%  WR_cb={df['won_cb'].mean()*100:.1f}%  "
          f"EV_cl=${df['pnl_cl'].mean():+.2f}/tr  (EV_cb=${df['pnl_cb'].mean():+.2f})  "
          f"total_cl=${df['pnl_cl'].sum():+.0f}")

    # THE key comparison: AGREE gate vs disagreement (the trades the gate would drop)
    ag = df[df["oracle_agree"]]
    di = df[~df["oracle_agree"]]
    print(f"\n=== dual-oracle AGREE gate effect ===")
    print(f"  {'slice':>14} {'n':>4} {'flip%':>6} {'WR_cl%':>7} {'EV_cl$':>7} {'total$':>7}")
    for nm, s in (("AGREE(keep)", ag), ("DISAGREE(cut)", di), ("ALL", df)):
        if len(s):
            print(f"  {nm:>14} {len(s):>4} {s['flip'].mean()*100:>6.1f} {s['won_cl'].mean()*100:>7.1f} "
                  f"{s['pnl_cl'].mean():>+7.2f} {s['pnl_cl'].sum():>+7.0f}")
    keep_frac = len(ag) / max(len(df), 1)
    print(f"  -> AGREE gate keeps {keep_frac*100:.0f}% of volume; "
          f"EV lifts ${df['pnl_cl'].mean():+.2f} -> ${ag['pnl_cl'].mean() if len(ag) else float('nan'):+.2f}/tr")

    # by split (is the lift real on the held-out future block?)
    print(f"\n=== AGREE gate by split ===")
    if "split" in df.columns:
        print(f"  {'split':>8} {'base n/EV':>14} {'agree n/EV':>14}")
        for sp in ("dev", "holdout", "future"):
            bb = df[df["split"] == sp]; aa = ag[ag["split"] == sp]
            if len(bb):
                print(f"  {sp:>8} {len(bb):>4} ${bb['pnl_cl'].mean():>+6.2f}   "
                      f"{len(aa):>4} ${aa['pnl_cl'].mean() if len(aa) else float('nan'):>+6.2f}")

    # slice tables: where does the flip risk live?
    print(f"\n=== flip risk by slice ===")
    _bucket(df, "cl_dist_bps", [0, 4, 8, 12, 16, 24, 1e9], label_abs=True)
    _bucket(df, "cb_dist_bps", [12, 14, 16, 18, 20, 1e9], label_abs=True)
    _bucket(df, "fav_ask", [0.50, 0.60, 0.70, 0.78, 0.85])
    _bucket(df, "time_left", [0, 30, 60, 120, 180])
    _bucket(df, "cl_oracle_age_s", [0, 8, 16, 30, 60, 1e9])
    return df


if __name__ == "__main__":
    run()
