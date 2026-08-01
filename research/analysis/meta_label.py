"""Phase 3 (M1) — meta-labeling: a secondary model P(win | decision-time features)
that gates/sizes trades. The disciplined question: does a LEARNED gate beat the
hand-crafted v2 filter OUT-OF-SAMPLE, or is simple better (honest negative)?

Gate (interpretable): take the trade iff predicted P(win) > entry price you pay
(model says the favourite beats its own ask). Validated with leakage-safe
Combinatorial Purged CV (refit per fold on TRAIN days, gate on TEST days). A
shallow, regularized GBM is used to limit overfitting on only 13 days.

Run: uv run python -m research.analysis.meta_label
"""
from __future__ import annotations
import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

from research.lib.stats import window_clustered_bootstrap
from research.lib.rigor import combinatorial_purged_cv
from research.clean_window import available_clean_dates

LED = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "data", "research", "ledgers"))

DET_FEATS = ["dist_bps", "fav_ask", "depth_usd", "adverse_vel_10s", "adverse_vel_3s",
             "time_left", "n_coins_volatile"]
SQ_FEATS = ["abs_mis", "mis", "z", "entry_ask", "dist_bps", "depth_usd",
            "spot_vel_10s", "time_left", "n_coins_volatile"]


def _prep(led, feats, ask_col):
    led = led.copy()
    for c in feats:
        if c not in led.columns:
            led[c] = 0.0
    # symbol one-hot (cheap, causal)
    for s in ("btc", "eth", "sol", "xrp"):
        led[f"is_{s}"] = (led["symbol"] == s).astype(float)
    X_cols = feats + [f"is_{s}" for s in ("btc", "eth", "sol", "xrp")]
    led[X_cols] = led[X_cols].fillna(led[X_cols].median(numeric_only=True)).fillna(0.0)
    led["ask"] = led[ask_col]
    return led, X_cols


def meta_eval(name, led, feats, ask_col, model_kind="gbm"):
    led, X_cols = _prep(led, feats, ask_col)
    days = available_clean_dates("btc")
    gated_pnl, gated_grp, ung_pnl = [], [], []
    kept = tot = 0
    for tr_days, te_days in combinatorial_purged_cv(days, n_groups=6, k_test=2, embargo_days=1):
        tr = led[led["date"].isin(tr_days)]; te = led[led["date"].isin(te_days)]
        if len(tr) < 40 or len(te) < 10 or tr["won"].nunique() < 2:
            continue
        if model_kind == "gbm":
            m = GradientBoostingClassifier(max_depth=2, n_estimators=80,
                                           learning_rate=0.05, subsample=0.8, random_state=0)
            m.fit(tr[X_cols], tr["won"])
            p = m.predict_proba(te[X_cols])[:, 1]
        else:
            sc = StandardScaler().fit(tr[X_cols])
            m = LogisticRegression(max_iter=500, C=0.5).fit(sc.transform(tr[X_cols]), tr["won"])
            p = m.predict_proba(sc.transform(te[X_cols]))[:, 1]
        take = p > te["ask"].values            # take iff model beats the price
        g = te[take]
        kept += int(take.sum()); tot += len(te)
        gated_pnl += list(g["pnl"].values); gated_grp += list(g["slug"].values)
        ung_pnl += list(te["pnl"].values)
    if not gated_pnl:
        print(f"\n{name} [{model_kind}]: gate took 0 trades"); return
    glo, _, ghi = window_clustered_bootstrap(np.array(gated_pnl), np.array(gated_grp), n=3000)
    print(f"\n=== {name} meta-label [{model_kind}] (CPCV, leakage-safe) ===")
    print(f"  ungated OOS: n={len(ung_pnl):>5}  ${np.mean(ung_pnl):+.3f}/tr")
    print(f"  GATED   OOS: n={len(gated_pnl):>5}  ${np.mean(gated_pnl):+.3f}/tr "
          f"CI[{glo:+.2f},{ghi:+.2f}]  keeps {kept/tot*100:.0f}%")
    lift = np.mean(gated_pnl) - np.mean(ung_pnl)
    print(f"  => lift ${lift:+.3f}/tr  {'BEATS ungated' if lift>0 else 'no improvement'}")


def run():
    det = pd.read_parquet(os.path.join(LED, "det_full.parquet"))
    sq = pd.read_parquet(os.path.join(LED, "sq_full.parquet"))
    for kind in ("gbm", "logistic"):
        meta_eval("determinism", det, DET_FEATS, "fav_ask", kind)
        meta_eval("stale_quote", sq, SQ_FEATS, "entry_ask", kind)


if __name__ == "__main__":
    run()
