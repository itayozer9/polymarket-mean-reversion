"""Per-day green/red for the winning calibration-NO config, through the REAL
harness (Chainlink settle, depth-gated fill, latency 2 and 10). The decisive
generalization read: a calibration edge should be green across the board; a
directional-drift artifact is green only on down-days.

Config tested: z-curve NO-only, margin 0.06, decision band 60-120s (a strong
future-lo finalist that is also not the most extreme/thin).

Run: uv run python -m research.analysis.hunt.calibration_perday
"""
from __future__ import annotations
import numpy as np, pandas as pd, warnings
from research.analysis import edge_lab as L
from research.analysis.hunt.calibration import build_decision

warnings.filterwarnings("ignore")
pd.set_option("display.width", 200)

for tag, kw in [
    ("z-no m0.06 60-120s", dict(tl_lo=60, tl_hi=120, form="zcurve", margin=0.06, side="no")),
    ("blind-NO 30-60s open", dict(tl_lo=30, tl_hi=60, form="none", margin=0)),
]:
    dec = build_decision(tag, **kw)
    for lat in (2, 10):
        led = L.simulate(dec, latency=lat)
        led = led.copy()
        print("\n" + "=" * 80)
        print(f"{tag}  latency={lat}s  n={len(led)}  EV=${led['pnl'].mean():+.3f}  "
              f"total=${led['pnl'].sum():+.1f}  WR={led['won'].mean()*100:.1f}%")
        # per-day
        d = led.groupby(["date", "split"]).agg(n=("pnl", "size"), ev=("pnl", "mean"),
                                               tot=("pnl", "sum"), wr=("won", "mean"))
        d["green"] = d["tot"] > 0
        print(d.round(2).to_string())
        # per-split summary
        sp = led.groupby("split").agg(n=("pnl", "size"), ev=("pnl", "mean"), tot=("pnl", "sum"))
        print("  split:", {k: f"ev${v.ev:+.2f} n{int(v.n)} tot${v.tot:+.0f}" for k, v in sp.iterrows()})
        gd = d.groupby(level=0)["green"].first() if False else (d["tot"] > 0)
        print(f"  GREEN DAYS: {(d['tot']>0).sum()}/{len(d)}  "
              f"(dev {(d.xs('dev',level=1)['tot']>0).sum() if 'dev' in d.index.get_level_values(1) else 0}, "
              f"holdout {(d.xs('holdout',level=1)['tot']>0).sum() if 'holdout' in d.index.get_level_values(1) else 0}, "
              f"future {(d.xs('future',level=1)['tot']>0).sum() if 'future' in d.index.get_level_values(1) else 0})")
