"""H4 — daily-loss-cap policy on the FULL window, parity sq ledger.

Reuses the exact DailyLossGuard replay from daily_cap_compare.simulate() but feeds
the full-window parity ledger (sq_full.parquet, incl. the big Jun 2-3 days) so we
see whether a tight cap truncates the positive skew the sq edge lives on.

Run: uv run python -m research.analysis.cap_policy_full
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.analysis.daily_cap_compare import simulate, _utc_day, STAKE, FEE_RATE, LEDGERS


def load_full(name="sq_full.parquet"):
    df = pd.read_parquet(os.path.join(LEDGERS, name))
    ws = df["window_start_ts"].astype("int64")
    df["ws_ms"] = np.where(ws < 1_000_000_000_000, ws * 1000, ws)
    df["entry_ts"] = df["ws_ms"] + df["entry_sec"].astype("int64") * 1000
    df["exit_ts"] = df["ws_ms"] + 900 * 1000
    ask_col = "entry_ask" if "entry_ask" in df.columns else "fav_ask"
    df["entry_ask"] = df[ask_col]  # simulate() reads df.entry_ask
    df["max_loss"] = STAKE + FEE_RATE * (1.0 - df[ask_col]) * STAKE
    return df.sort_values("entry_ts").reset_index(drop=True)


def main():
    for name, label in (("sq_full.parquet", "STALE-QUOTE"), ("det_full.parquet", "DETERMINISM")):
        df = load_full(name)
        ndays = df["entry_ts"].map(_utc_day).nunique()
        print(f"\n###### {label}: n={len(df)} over {ndays} UTC days ######")
        for cap in (30, 50, 75, 100, 150):
            rows = [simulate(df, cap, m) for m in
                    (None, "soft_settled", "hard_worstcase", "hard_worstcase_latch")]
            unc = rows[0]
            print(f"  cap ${cap}/day:")
            for r in rows:
                cost = unc["total"] - r["total"]
                print(f"    {r['mode']:22} taken={r['taken']:>4} skip={r['skipped']:>4} "
                      f"total=${r['total']:>+7.0f} $/tr={r['ev_tr']:>+5.2f} "
                      f"worstDay=${r['worst_day']:>+6.0f} breach={r['breach_days']} "
                      f"| cost-of-cap=${cost:>+6.0f}")


if __name__ == "__main__":
    main()
