"""Three-way backtest: det_d12_dual (deployed adjustments) vs det_d12_wide_v1 (paper)
vs det_d12_wide_live (original live), over the FULL Chainlink-settled history.

KEY FACT: det_d12_wide_v1 and det_d12_wide_live share the IDENTICAL determinism
config (mode=consistent, t 1-180, dist>=12, ask 0.50-0.85, no oracle gate). They
differ ONLY in live stake/bankroll/daily-cap — so their PER-TRADE backtest is
identical. The real contrast is dual vs wide.

All three settle on Chainlink (the Polymarket-true outcome) via edge_lab.simulate,
2s fill latency, $10 backtest stake. Live $ at $5/trade = half these totals.

Run: uv run python -m research.analysis.compare_dual_vs_wide
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.analysis import edge_lab
from research.analysis.dynamic_max_ask import _prep, T_MIN, T_MAX, DIST_MIN, ASK_LO, ADVEL

STAKE = 10.0  # edge_lab convention; live runs $5 -> halve all $ totals


def _wide_ledger(b: pd.DataFrame, max_ask: float = 0.85) -> pd.DataFrame:
    """ORIGINAL det_d12_wide: consistent, t 1-180, dist>=12, ask in [0.50, max_ask].
    NO oracle gate, NO adverse_vel, NO per-tick Chainlink requirement (oracle-blind)."""
    m = ((b["time_left_sec"] >= T_MIN) & (b["time_left_sec"] <= T_MAX)
         & (b["abs_dist_bps"] >= DIST_MIN) & (b["consistent"])
         & (b["fav_ask"] >= ASK_LO) & (b["fav_ask"] <= max_ask + 1e-9))
    c = b[m]
    if c.empty:
        return pd.DataFrame()
    dd = edge_lab.first_tick(c, (c["yes_mid"] >= 0.5).to_numpy())
    return edge_lab.simulate(dd, latency=2, stake=STAKE)


def _dual_ledger(b: pd.DataFrame) -> pd.DataFrame:
    """DEPLOYED dual config: AGREE gate + adverse_vel<=2 + adaptive ceiling
    0.78 base, 0.85 when |cl_dist|>=20bps (deep Chainlink lock)."""
    cld = b["cl_dist_bps"].abs().to_numpy("f8")
    ceiling = np.where(cld >= 20.0, 0.85, 0.78)
    base = ((b["time_left_sec"] >= T_MIN) & (b["time_left_sec"] <= T_MAX)
            & (b["abs_dist_bps"] >= DIST_MIN) & (b["consistent"])
            & (b["fav_ask"] >= ASK_LO) & (b["adverse_vel_10s"] <= ADVEL)
            & (b["cl_ok"]) & (b["oracle_agree"]))
    m = base & (b["fav_ask"] <= ceiling + 1e-9)
    c = b[m]
    if c.empty:
        return pd.DataFrame()
    dd = edge_lab.first_tick(c, (c["yes_mid"] >= 0.5).to_numpy())
    return edge_lab.simulate(dd, latency=2, stake=STAKE)


def _summ(name: str, led: pd.DataFrame) -> dict:
    if led is None or len(led) == 0:
        print(f"  {name:24} n=0")
        return {}
    e = edge_lab.evaluate(led)
    ps = e["per_split"]
    fu = ps.get("future")
    fl = ps["FULL"]
    lat = edge_lab.latency_survival(
        edge_lab.first_tick(led.assign(seconds_into_window=led["entry_sec"]), led["buy_yes"])
        if "buy_yes" in led.columns else None) if False else None
    row = dict(name=name, n=e["n"], wr=e["wr"], ev=e["ev"], total=e["total"],
               full_lo=fl["lo"], full_hi=fl["hi"],
               fut_n=(fu["n"] if fu else 0), fut_wr=(fu["wr"] if fu else float("nan")),
               fut_ev=(fu["ev"] if fu else float("nan")),
               fut_lo=(fu["lo"] if fu else float("nan")), fut_hi=(fu["hi"] if fu else float("nan")),
               fut_total=(fu["total"] if fu else float("nan")),
               cpcv=e["cpcv"].get("pct_pos", float("nan")), dsr=e["dsr"].get("dsr"))
    print(f"  {name:24} n={row['n']:>3}  WR={row['wr']:>5.1f}%  EV=${row['ev']:+.2f}/tr  "
          f"total=${row['total']:>+6.0f}  FULL CI[{row['full_lo']:+.2f},{row['full_hi']:+.2f}]")
    print(f"  {'':24} future: n={row['fut_n']:>3}  WR={row['fut_wr']:>5.1f}%  "
          f"EV=${row['fut_ev']:+.2f}/tr  total=${row['fut_total']:>+6.0f}  "
          f"CI[{row['fut_lo']:+.2f},{row['fut_hi']:+.2f}]  CPCV={row['cpcv']:.0f}%  DSR={row['dsr']}")
    return row


def run():
    b = _prep(edge_lab.load_base())
    dmin = pd.to_datetime(b["window_start_ts"], unit="s").min()
    dmax = pd.to_datetime(b["window_start_ts"], unit="s").max()
    print(f"\nChainlink-settled backtest  |  data {dmin:%Y-%m-%d} .. {dmax:%Y-%m-%d}  |  "
          f"2s latency, ${STAKE:.0f} stake (live=$5 -> halve totals)\n")

    rows = []
    print("=== det_d12_dual  (DEPLOYED: AGREE gate + adverse_vel2 + adaptive 0.78->0.85@|cl|>=20) ===")
    rows.append(_summ("det_d12_dual", _dual_ledger(b)))
    print("\n=== det_d12_wide  (det_d12_wide_v1 paper == det_d12_wide_live config: max_ask 0.85, no gate) ===")
    wide = _wide_ledger(b, 0.85)
    rows.append(_summ("det_d12_wide", wide))

    # Compact comparison table
    print("\n" + "=" * 78)
    print("SUMMARY (full backtest, $10 stake)")
    print(f"{'config':18} {'n':>4} {'WR%':>6} {'EV/tr':>8} {'total$':>9} {'fut n':>6} {'fut WR%':>8} {'fut EV':>8} {'fut $':>8}")
    label = {"det_d12_dual": "dual (live now)", "det_d12_wide": "wide v1 == live"}
    for r in rows:
        if not r:
            continue
        print(f"{label.get(r['name'], r['name']):18} {r['n']:>4} {r['wr']:>6.1f} "
              f"{r['ev']:>+8.2f} {r['total']:>+9.0f} {r['fut_n']:>6} {r['fut_wr']:>8.1f} "
              f"{r['fut_ev']:>+8.2f} {r['fut_total']:>+8.0f}")
    print("\nNote: det_d12_wide_v1 (paper, $10/$1000) and det_d12_wide_live (live, $5/$100) have")
    print("the SAME determinism config -> identical entries/WR/EV per trade. The row above is both;")
    print("det_d12_wide_live's live $ = half the $10-stake totals shown.")


if __name__ == "__main__":
    run()
