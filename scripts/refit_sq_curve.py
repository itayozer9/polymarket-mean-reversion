"""Rolling re-fit of the stale-quote P(Up|z) curve (A3 drift fix).

Audit A3 found the frozen 05-23..29 curve drifted — it over-predicts Up by ~5.6pp
on June data (vol ~4x, DOWN tape). Re-fit on a TRAILING window so the live sq curve
tracks the current regime. Schedule nightly (cron) to make it truly rolling.

  uv run python scripts/refit_sq_curve.py [--days 7]

Writes data/research/stale_quote_curve.json (backs up the prior to .bak). The bot
picks up the new curve on its next restart.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.analysis.loss_patterns import _base, _sq_prep, _fit_curve, JOINED
from research.clean_window import available_clean_dates

CURVE = os.path.join(os.path.dirname(JOINED), "stale_quote_curve.json")


def main(days: int = 7):
    df = _sq_prep(_base(pd.read_parquet(JOINED)))
    alldays = available_clean_dates("btc")
    keep = sorted(alldays)[-days:]
    train = df[df["date"].isin(set(keep))]
    zc, p = _fit_curve(train)
    z50 = float(zc[int(np.argmin(np.abs(p - 0.5)))])
    out = {
        "z": [round(float(x), 4) for x in zc],
        "p_up": [round(float(x), 4) for x in p],
        "note": (f"ROLLING refit on last {days} clean days {keep[0]}..{keep[-1]} "
                 f"(A3 drift fix); z50={z50:.3f}; Up-rate(train)={train['outcome_up_clean'].mean():.3f}"),
    }
    if os.path.exists(CURVE):
        shutil.copy(CURVE, CURVE + ".bak")
    with open(CURVE, "w") as f:
        json.dump(out, f, indent=2)
    print(f"refit on {len(train):,} ticks / {len(keep)} days {keep[0]}..{keep[-1]} -> {CURVE}")
    print(f"  z50 (P=0.5 crossing) = {z50:+.3f}  | train Up-rate = {train['outcome_up_clean'].mean():.3f}")
    print(f"  backup: {CURVE}.bak")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    main(ap.parse_args().days)
