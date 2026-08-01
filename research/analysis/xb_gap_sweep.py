"""XB-GAP sweep — find the strike-gap sweet spot for xb_5m15m_causal_v1.

CAUSAL frame only: a tick is eligible iff the co-terminal 5m window's strike had
ALREADY been captured by discovery at that second (first s5 with raw start_price>0
in data/research/external_inputs/ticks5m_coterminal.parquet) — the defect class
documented in test_ledger § "XI4 AMENDMENT (2026-06-12)".

Discipline: the gap chooser is d+h-ONLY and registered here BEFORE the run:
    sweet spot = the gap maximizing d+h TOTAL $ subject to
    d+h CI-lower(EV/fill) > 0 and >= 2 signals/day; ties -> larger gap.
The FUTURE block is revealed only for the chosen gap and the 2bps default.
Premium fixed at the registered 0.03; ceiling 0.90; $10 stake; analytic fill
(ask + 0.07*p*(1-p) fee — the XI4 'v2' convention; live-hazard scaling ~x0.55
fills applies uniformly across gaps so it cannot change the chooser).

Run: uv run python -m research.analysis.xb_gap_sweep
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis.edge_lab import cl_outcomes, load_base
from research.analysis.external_inputs import (
    OUT_DIR, build_xi4_join, monotonic_signal, relabel_splits)
from research.lib.stats import window_clustered_bootstrap

GAPS = [2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0]
MARGIN = 0.03
CEILING = 0.90
STAKE = 10.0
DH_DAYS = 13.0   # 05-23..06-04
FUT_DAYS = 5.0   # 06-05..09


def causal_join() -> pd.DataFrame:
    j = relabel_splits(build_xi4_join(load_base()))
    raw = pd.read_parquet(OUT_DIR + "/ticks5m_coterminal.parquet")
    cap = (raw[raw["start_price"] > 0]
           .groupby(["symbol", "window_start_ts"])["seconds_into_window"]
           .min().rename("cap_s5").reset_index()
           .rename(columns={"window_start_ts": "w5"}))
    j = j.merge(cap, on=["symbol", "w5"], how="left")
    return j[j["s5"] >= j["cap_s5"]].copy()   # NaN cap (never captured) drops out


def score(sig: pd.DataFrame, cl: dict) -> pd.DataFrame:
    first = (sig.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    first = first[first["entry_ask"] <= CEILING]
    first["cl_up"] = first["slug"].map(cl)
    first = first.dropna(subset=["cl_up"])
    a = first["entry_ask"].to_numpy("f8")
    sh = STAKE / a
    fee = 0.07 * a * (1 - a) * sh
    won = np.where(first["buy_yes"], first["cl_up"] == 1, first["cl_up"] == 0)
    first["won"] = won.astype(int)
    first["pnl"] = np.where(won, sh - STAKE - fee, -STAKE - fee)
    return first


def row(t: pd.DataFrame, days: float) -> dict:
    if len(t) < 3:
        return dict(n=int(len(t)), per_day=round(len(t) / days, 1), ev=None,
                    lo=None, hi=None, wr=None, total=None, ask=None)
    lo, _, hi = window_clustered_bootstrap(t["pnl"].values, t["slug"].values, n=2000)
    return dict(n=int(len(t)), per_day=round(len(t) / days, 1),
                ev=round(float(t["pnl"].mean()), 2), lo=round(float(lo), 2),
                hi=round(float(hi), 2), wr=round(float(t["won"].mean() * 100), 0),
                total=round(float(t["pnl"].sum()), 0),
                ask=round(float(t["entry_ask"].median()), 2))


def main():
    j = causal_join()
    cl = cl_outcomes().set_index("slug")["cl_up"].to_dict()
    print(f"causal join: {len(j)} ticks, {j['slug'].nunique()} windows "
          f"(capture median {j['cap_s5'].median():.0f}s into the 5m window)\n")
    dh_rows, ledgers = {}, {}
    print(f"{'gap':>4} | d+h: {'n':>4} {'sig/d':>5} {'EV/fill':>8} {'CI':>15} "
          f"{'WR%':>4} {'total$':>7} {'ask':>5}")
    for g in GAPS:
        sig = monotonic_signal(j, MARGIN, g)
        t = score(sig, cl)
        ledgers[g] = t
        r = row(t[t["split"] != "future"], DH_DAYS)
        dh_rows[g] = r
        ci = f"[{r['lo']},{r['hi']}]" if r["ev"] is not None else "-"
        print(f"{g:4.0f} | {r['n']:9d} {r['per_day']:5.1f} "
              f"{('$' + str(r['ev'])) if r['ev'] is not None else '-':>8} {ci:>15} "
              f"{str(r['wr']):>4} {str(r['total']):>7} {str(r['ask']):>5}")
    # registered chooser: max d+h total, CI-lower>0, >=2 sig/day; ties -> larger gap
    elig = [g for g in GAPS if dh_rows[g]["ev"] is not None
            and dh_rows[g]["lo"] is not None and dh_rows[g]["lo"] > 0
            and dh_rows[g]["per_day"] >= 2.0]
    chosen = (max(elig, key=lambda g: (dh_rows[g]["total"], g)) if elig else None)
    print(f"\nchooser (registered): eligible gaps {elig} -> CHOSEN = {chosen}")
    for g in sorted({chosen, 2.0} - {None}):
        fr = row(ledgers[g][ledgers[g]["split"] == "future"], FUT_DAYS)
        ci = f"[{fr['lo']},{fr['hi']}]" if fr["ev"] is not None else "-"
        print(f"FUTURE reveal gap={g:.0f}: n={fr['n']} ({fr['per_day']}/d) "
              f"EV/fill {('$' + str(fr['ev'])) if fr['ev'] is not None else '-'} {ci} "
              f"WR {fr['wr']}% total ${fr['total']} ask~{fr['ask']}")


if __name__ == "__main__":
    main()
