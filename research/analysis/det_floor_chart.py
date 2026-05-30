"""Chart: realized favourite WR vs entry ask, with the break-even diagonal.
Shows the WR line sits flat-high above break-even, so the edge (vertical gap) is
biggest at the cheapest prices -> a floor removes the best trades.

Run: uv run python -m research.analysis.det_floor_chart
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from research.analysis.det_floor_sweep import _load, _entries, FEES
from research.lib.stats import window_clustered_bootstrap


def main():
    first = _entries(_load(["dev", "holdout"]), 0.50, max_ask=0.98)
    edges = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98]
    xs, wrs, lo_err, hi_err, ns = [], [], [], [], []
    for lo_e, hi_e in zip(edges[:-1], edges[1:]):
        sub = first[(first["fav_ask"] >= lo_e) & (first["fav_ask"] < hi_e)]
        if len(sub) < 5:
            continue
        wr = sub["fav_won"].mean()
        lo, _, hi = window_clustered_bootstrap(sub["fav_won"].to_numpy(),
                                               sub["slug"].to_numpy(), n=2000)
        xs.append(sub["fav_ask"].mean()); wrs.append(wr)
        lo_err.append(wr - lo); hi_err.append(hi - wr); ns.append(len(sub))
    xs, wrs = np.array(xs), np.array(wrs)

    fig, ax = plt.subplots(figsize=(9.0, 5.8))
    gx = np.linspace(0.5, 0.98, 120)
    be = gx + FEES.rate * gx * (1 - gx)
    ax.fill_between(gx, be, 1.0, color="#d8f3d8", alpha=0.65,
                    label="profit zone (WR > price + fee)")
    ax.fill_between(gx, 0.5, be, color="#f7dada", alpha=0.5,
                    label="loss zone (WR < price + fee)")
    ax.plot(gx, be, "--", color="#777", lw=1.3, label="break-even (WR = price + fee)")
    ax.errorbar(xs, wrs, yerr=[lo_err, hi_err], fmt="o-", color="#138a4a", lw=2.2,
                ms=7, capsize=4, label="realized favourite WR (90% CI)")
    for x, w, n in zip(xs, wrs, ns):
        ax.annotate(f"n={n}", (x, w), textcoords="offset points", xytext=(0, 10),
                    ha="center", fontsize=7.5, color="#444")
    for xb, lab, col in ((0.50, "floor 0.50", "#2a6fdb"), (0.90, "ceiling 0.90", "#2a6fdb")):
        ax.axvline(xb, color=col, lw=1.2, alpha=0.7)
        ax.text(xb + 0.006, 0.515, f"incumbent {lab}", rotation=90, fontsize=8,
                color=col, va="bottom")
    ax.axvline(0.95, color="#cc3333", lw=1.3, ls=":", alpha=0.85)
    ax.text(0.956, 0.515, "proposed 0.95", rotation=90, fontsize=8,
            color="#cc3333", va="bottom")
    ax.set_xlabel("entry ask — price paid for the favourite")
    ax.set_ylabel("realized win rate")
    ax.set_title("Determinism edge lives in [0.50, 0.90]\n"
                 "below 0.90 WR beats price (edge biggest when cheapest); "
                 "above 0.90 WR drops BELOW price (no edge) — keep BOTH bounds")
    ax.set_xlim(0.48, 0.985); ax.set_ylim(0.5, 1.01)
    ax.legend(loc="lower right", fontsize=8.5)
    ax.grid(alpha=0.25)
    out = os.path.join("docs", "research", "charts", "det_entry_floor.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.tight_layout(); fig.savefig(out, dpi=130)
    print("saved", out)


if __name__ == "__main__":
    main()
