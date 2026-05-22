"""Unconditional calibration — Task 6 of the Phase 2-4 edge-discovery plan.

Question: Is the market's implied probability calibrated? For ticks where the
cheap side is priced p, does the cheap side win p of the time?

Method:
  1. Load entry_candidates_15m.parquet; keep dev rows only (dev_mask) with
     non-null cheap_won and finite features.
  2. Reliability curve: pred=cheap_mid, outcome=cheap_won, groups=slug, 20 bins.
  3. Also: pred=cheap_ask (what a taker actually pays).
  4. All-states framing: pred=yes_mid, outcome=outcome_up, per tick.
  5. Per-symbol cheap-mid reliability curves.

Sanity assertions (must hold):
  - Every bin n_windows >= 20, or reported as 'thin'.
  - All-states overall mean realized vs mean predicted within 0.03.

Deliverables:
  - docs/research/charts/calibration_unconditional.png
  - docs/research/edge_map.md (created/appended)
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup: repo root is two levels up from this file
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from research.lib.splits import dev_mask, add_date_col
from research.lib.stats import reliability_curve

_EC_PATH = _REPO_ROOT / "data/research/entry_candidates_15m.parquet"
_TICKS_PATH = _REPO_ROOT / "data/research/ticks_15m.parquet"
_CHART_PATH = _REPO_ROOT / "docs/research/charts/calibration_unconditional.png"
_EDGE_MAP_PATH = _REPO_ROOT / "docs/research/edge_map.md"

N_BINS = 20
N_BOOT = 1000  # bootstrap iterations per bin (enough for stable 90% CI)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_table(curve: list[dict], name: str) -> str:
    """Format a reliability curve as a markdown table."""
    header = (
        f"### {name}\n\n"
        "| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_ticks | Note |\n"
        "|-----|-----------|----------|-------|-------|-----------|---------|------|\n"
    )
    rows = []
    for row in curve:
        note = "thin" if row["n_windows"] < 20 else ""
        gap = row["realized"] - row["mean_pred"]
        gap_str = f"{gap:+.3f}"
        rows.append(
            f"| [{row['bin_lo']:.2f}, {row['bin_hi']:.2f}) "
            f"| {row['mean_pred']:.4f} "
            f"| {row['realized']:.4f} ({gap_str}) "
            f"| {row['ci_lo']:.4f} "
            f"| {row['ci_hi']:.4f} "
            f"| {row['n_windows']:,} "
            f"| {row['n_ticks']:,} "
            f"| {note} |"
        )
    return header + "\n".join(rows)


def _verdict_from_curve(curve: list[dict], name: str) -> str:
    """Plain-language verdict: underpriced / overpriced / calibrated."""
    thick = [r for r in curve if r["n_windows"] >= 20]
    if not thick:
        return f"{name}: no thick bins — cannot assess."
    gaps = [r["realized"] - r["mean_pred"] for r in thick]
    mean_gap = np.mean(gaps)
    # Check if CI excludes zero in a consistent direction
    # A bin has buyer edge if the whole CI of realized frequency is above the diagonal (mean_pred)
    buyer_edge_bins = [r for r in thick if r["ci_lo"] > r["mean_pred"]]
    # A bin is overpriced if the whole CI of realized is below the diagonal
    overpriced_bins = [r for r in thick if r["ci_hi"] < r["mean_pred"]]
    if abs(mean_gap) < 0.01:
        verdict = "CALIBRATED (mean gap < 1¢)"
    elif mean_gap > 0:
        verdict = f"UNDERPRICED (buyer edge) — mean gap {mean_gap*100:+.2f}¢"
    else:
        verdict = f"OVERPRICED (favorite-longshot / buyer loses) — mean gap {mean_gap*100:+.2f}¢"
    detail = (
        f"  Mean realized−predicted = {mean_gap*100:+.3f}¢ across {len(thick)} thick bins. "
        f"Bins with CI entirely above zero (buyer edge): {len(buyer_edge_bins)}. "
        f"Bins with CI entirely below zero (overpriced): {len(overpriced_bins)}."
    )
    return f"**{name}: {verdict}**\n{detail}"


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run() -> None:
    print("=" * 65)
    print("Task 6: Unconditional Calibration")
    print("=" * 65)

    # --- 1. Load entry candidates (cheap-side framing) ---
    print(f"\nLoading {_EC_PATH} …")
    ec = pd.read_parquet(_EC_PATH)
    print(f"  Total rows: {len(ec):,}")

    mask = dev_mask(ec) & ec["cheap_won"].notna() & np.isfinite(ec["cheap_mid"])
    dev = ec[mask].reset_index(drop=True)
    print(f"  Dev rows (filtered): {len(dev):,}  |  slugs: {dev['slug'].nunique():,}")

    cheap_mid = dev["cheap_mid"].values
    cheap_ask = dev["cheap_ask"].values
    cheap_won = dev["cheap_won"].values
    groups_slug = dev["slug"].values

    # --- 2. cheap_mid reliability curve ---
    print("\nComputing cheap_mid reliability curve (20 bins, 1000 bootstrap iters) …")
    curve_mid = reliability_curve(cheap_mid, cheap_won, groups_slug,
                                  n_bins=N_BINS, seed=42)
    # reduce n_boot for cheap_ask (same data, just different pred)
    print("Computing cheap_ask reliability curve …")
    curve_ask = reliability_curve(cheap_ask, cheap_won, groups_slug,
                                  n_bins=N_BINS, seed=43)

    # --- 3. All-states framing (yes_mid vs outcome_up) ---
    print("Loading ticks_15m for all-states analysis …")
    ticks = pd.read_parquet(_TICKS_PATH)
    ticks2 = add_date_col(ticks)
    all_mask = dev_mask(ticks2) & ticks2["outcome_up"].notna() & np.isfinite(ticks2["yes_mid"])
    dev_ticks = ticks2[all_mask].reset_index(drop=True)
    print(f"  All-states dev rows: {len(dev_ticks):,}")

    yes_mid = dev_ticks["yes_mid"].values
    outcome_up = dev_ticks["outcome_up"].values
    # groups by slug (use 'slug' if present, else 'market_slug')
    grp_col = "slug" if "slug" in dev_ticks.columns else "market_slug"
    groups_all = dev_ticks[grp_col].values

    print("Computing all-states yes_mid reliability curve …")
    curve_all = reliability_curve(yes_mid, outcome_up, groups_all,
                                  n_bins=N_BINS, seed=44)

    # --- Sanity assertion: all-states overall mean within 0.03 ---
    overall_mean_pred = np.average(yes_mid)
    overall_mean_real = np.mean(outcome_up)
    diff = abs(overall_mean_real - overall_mean_pred)
    print(f"\n[SANITY] all-states: mean predicted={overall_mean_pred:.4f}  "
          f"realized={overall_mean_real:.4f}  diff={diff:.4f}")
    if diff > 0.03:
        print(f"  !!! WARNING: diff {diff:.4f} > 0.03 — market may be grossly mis-calibrated "
              f"or a data/filtering bug. FLAG THIS.")
    else:
        print(f"  OK: diff < 0.03")

    # --- Sanity assertion: cheap_mid thin-bin check ---
    thin_bins_mid = [r for r in curve_mid if r["n_windows"] < 20]
    if thin_bins_mid:
        print(f"[SANITY] cheap_mid: {len(thin_bins_mid)} thin bins (n_windows<20) — will be labelled.")
    else:
        print(f"[SANITY] cheap_mid: all bins have n_windows >= 20  OK")

    # --- 4. Per-symbol cheap-mid curves ---
    print("\nComputing per-symbol cheap_mid reliability curves …")
    symbol_curves: dict[str, list[dict]] = {}
    for sym in sorted(dev["symbol"].unique()):
        smask = dev["symbol"] == sym
        sub = dev[smask]
        if sub["slug"].nunique() < 5:
            print(f"  {sym}: too few slugs ({sub['slug'].nunique()}) — skipped")
            continue
        sc = reliability_curve(
            sub["cheap_mid"].values,
            sub["cheap_won"].values,
            sub["slug"].values,
            n_bins=N_BINS, seed=45,
        )
        symbol_curves[sym] = sc
        print(f"  {sym}: {len(sub):,} ticks, {sub['slug'].nunique()} slugs, "
              f"{len(sc)} non-empty bins")

    # --- 5. Chart ---
    _make_chart(curve_mid, curve_ask, curve_all, symbol_curves)

    # --- 6. Write findings to edge_map.md ---
    _write_edge_map(curve_mid, curve_ask, curve_all, symbol_curves,
                    overall_mean_pred, overall_mean_real,
                    thin_bins_mid, len(dev), dev["slug"].nunique())

    print(f"\nDone.  Chart → {_CHART_PATH}")
    print(f"       Findings → {_EDGE_MAP_PATH}")


# ---------------------------------------------------------------------------
# Charting
# ---------------------------------------------------------------------------

def _plot_curve(ax, curve, label, color, marker="o", lw=1.5):
    """Plot one reliability curve on ax with CI bars and diagonal."""
    mean_preds = [r["mean_pred"] for r in curve]
    realized = [r["realized"] for r in curve]
    ci_lo = [r["ci_lo"] for r in curve]
    ci_hi = [r["ci_hi"] for r in curve]
    yerr_lo = [r - lo for r, lo in zip(realized, ci_lo)]
    yerr_hi = [hi - r for r, hi in zip(realized, ci_hi)]

    ax.errorbar(mean_preds, realized,
                yerr=[yerr_lo, yerr_hi],
                fmt=marker, color=color, label=label,
                linewidth=lw, markersize=5, capsize=3, capthick=1.2,
                zorder=5)


def _make_chart(curve_mid, curve_ask, curve_all, symbol_curves):
    n_sym = len(symbol_curves)
    # Layout: 2 rows. Top: cheap_mid + cheap_ask + all-states (3 panels or 2+1)
    # Bottom: per-symbol
    ncols = max(3, n_sym)
    fig, axes = plt.subplots(2, ncols, figsize=(5 * ncols, 10))
    fig.suptitle("Unconditional Calibration — Polymarket Up/Down (dev split May 15–20 2026)",
                 fontsize=13, fontweight="bold", y=1.01)

    diag = np.linspace(0, 0.55, 100)

    # Panel 0,0: cheap_mid vs cheap_ask
    ax = axes[0, 0]
    _plot_curve(ax, curve_mid, "cheap_mid (market bid)", "#1f77b4")
    _plot_curve(ax, curve_ask, "cheap_ask (taker pays)", "#d62728", marker="s")
    ax.plot(diag, diag, "k--", lw=1, label="perfect calibration")
    ax.set_title("Cheap-side: Mid vs Ask")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Realized frequency (cheap side wins)")
    ax.legend(fontsize=8)
    ax.set_xlim(0, 0.55)
    ax.set_ylim(0, 0.75)
    ax.grid(True, alpha=0.3)

    # Panel 0,1: cheap_mid only + CI fill
    ax = axes[0, 1]
    _plot_curve(ax, curve_mid, "cheap_mid", "#1f77b4")
    ax.plot(diag, diag, "k--", lw=1, label="perfect calibration")
    ax.set_title("Cheap-side: Mid (zoomed)")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Realized frequency")
    ax.legend(fontsize=8)
    ax.set_xlim(0, 0.55)
    ax.set_ylim(0, 0.75)
    ax.grid(True, alpha=0.3)

    # Panel 0,2: all-states yes_mid
    ax = axes[0, 2]
    _plot_curve(ax, curve_all, "yes_mid (all states)", "#2ca02c", marker="^")
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect calibration")
    ax.set_title("All-states: yes_mid vs outcome_up")
    ax.set_xlabel("yes_mid")
    ax.set_ylabel("Realized P(Up)")
    ax.legend(fontsize=8)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)

    # Fill remaining top panels if ncols > 3
    for col in range(3, ncols):
        axes[0, col].set_visible(False)

    # Bottom row: per-symbol
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
    for col, (sym, sc) in enumerate(sorted(symbol_curves.items())):
        if col >= ncols:
            break
        ax = axes[1, col]
        _plot_curve(ax, sc, sym, colors[col % len(colors)])
        ax.plot(diag, diag, "k--", lw=1, alpha=0.6)
        ax.set_title(f"cheap_mid — {sym.upper()}")
        ax.set_xlabel("Mean predicted probability")
        ax.set_ylabel("Realized frequency")
        ax.set_xlim(0, 0.55)
        ax.set_ylim(0, 0.75)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)

    for col in range(len(symbol_curves), ncols):
        axes[1, col].set_visible(False)

    plt.tight_layout()
    _CHART_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(_CHART_PATH), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Chart saved: {_CHART_PATH}")


# ---------------------------------------------------------------------------
# Write to edge_map.md
# ---------------------------------------------------------------------------

def _write_edge_map(curve_mid, curve_ask, curve_all, symbol_curves,
                    overall_mean_pred, overall_mean_real,
                    thin_bins_mid, n_dev_rows, n_slugs):
    """Create or append findings section to docs/research/edge_map.md."""

    # Build markdown content
    lines: list[str] = []

    lines.append("## Unconditional Calibration (Task 6)")
    lines.append("")
    lines.append(
        f"**Data:** {n_dev_rows:,} dev-split ticks, {n_slugs:,} slugs, "
        f"May 15–20 2026. Sealed hold-out (May 21–22) NOT touched."
    )
    lines.append(
        f"**Method:** reliability_curve(), window-clustered bootstrap (n=1000), "
        f"groups=slug, 20 equal-width bins on [0, 1]."
    )
    lines.append("")
    lines.append("### Sanity checks")
    lines.append("")
    diff = abs(overall_mean_real - overall_mean_pred)
    ok_str = "PASS" if diff <= 0.03 else "FAIL — FLAGGED"
    lines.append(
        f"- **All-states mean consistency:** predicted={overall_mean_pred:.4f}, "
        f"realized={overall_mean_real:.4f}, |diff|={diff:.4f} → **{ok_str}**"
    )
    n_thin = len(thin_bins_mid)
    thin_str = f"{n_thin} thin bins (n_windows<20) — labelled 'thin, not interpreted'" if n_thin else "all bins n_windows ≥ 20"
    lines.append(f"- **cheap_mid bin thickness:** {thin_str}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- cheap_mid table ---
    lines.append(_render_table(curve_mid, "cheap_mid reliability (20 bins)"))
    lines.append("")
    lines.append(_verdict_from_curve(curve_mid, "cheap_mid"))
    lines.append("")

    # Detailed bin-by-bin gap commentary for cheap_mid
    thick = [r for r in curve_mid if r["n_windows"] >= 20]
    if thick:
        lines.append("**Bin-level gaps (realized − predicted) for cheap_mid:**")
        lines.append("")
        for r in thick:
            gap_c = (r["realized"] - r["mean_pred"]) * 100
            ci_gap_lo = (r["ci_lo"] - r["mean_pred"]) * 100
            ci_gap_hi = (r["ci_hi"] - r["mean_pred"]) * 100
            excl_zero = "CI excl diag" if (r["ci_lo"] > r["mean_pred"] or r["ci_hi"] < r["mean_pred"]) else "CI incl diag"
            lines.append(
                f"  - [{r['bin_lo']:.2f}, {r['bin_hi']:.2f}): "
                f"gap={gap_c:+.2f}¢  CI=({ci_gap_lo:+.2f}¢, {ci_gap_hi:+.2f}¢)  "
                f"{excl_zero}  n_win={r['n_windows']:,}"
            )
        lines.append("")

    # --- cheap_ask table ---
    lines.append(_render_table(curve_ask, "cheap_ask reliability (taker entry price, 20 bins)"))
    lines.append("")
    lines.append(_verdict_from_curve(curve_ask, "cheap_ask"))
    lines.append("")

    # --- All-states table ---
    lines.append(_render_table(curve_all, "All-states yes_mid vs outcome_up (20 bins)"))
    lines.append("")
    lines.append(_verdict_from_curve(curve_all, "all-states yes_mid"))
    lines.append("")

    # --- Per-symbol tables ---
    lines.append("### Per-symbol cheap_mid reliability")
    lines.append("")
    for sym, sc in sorted(symbol_curves.items()):
        lines.append(_render_table(sc, f"{sym.upper()} — cheap_mid"))
        lines.append("")
        lines.append(_verdict_from_curve(sc, sym.upper()))
        lines.append("")

    # --- Chart reference ---
    lines.append(
        f"**Chart:** `docs/research/charts/calibration_unconditional.png`"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    section_text = "\n".join(lines)

    # Create or append to edge_map.md
    if _EDGE_MAP_PATH.exists():
        existing = _EDGE_MAP_PATH.read_text()
        # Replace section if it already exists, otherwise append
        marker = "## Unconditional Calibration (Task 6)"
        next_marker = "## "  # any next level-2 section
        if marker in existing:
            # Find and replace the section
            start = existing.index(marker)
            # Find next ## after start (that isn't the same section)
            search_from = start + len(marker)
            next_pos = existing.find("\n## ", search_from)
            if next_pos == -1:
                new_content = existing[:start] + section_text
            else:
                new_content = existing[:start] + section_text + "\n" + existing[next_pos + 1:]
            _EDGE_MAP_PATH.write_text(new_content)
        else:
            # Append
            _EDGE_MAP_PATH.write_text(existing.rstrip() + "\n\n" + section_text)
    else:
        # Create with header
        header = "# Edge Map\n\nPhase 2–4 Edge Discovery findings for Polymarket Up/Down (May 2026).\n\n"
        _EDGE_MAP_PATH.write_text(header + section_text)

    print(f"  edge_map.md written: {_EDGE_MAP_PATH}")


if __name__ == "__main__":
    run()
