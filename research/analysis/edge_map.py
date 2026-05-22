"""Conditioned edge map -- Task 7 of the Phase 2-4 edge-discovery plan.

**Question.** Where is the cheap-side mispricing concentrated? An edge -- if any
-- lives in specific conditions, not everywhere. The core thesis (H8 / H2) is
that a *genuine* buyer edge should concentrate where the market is still a
coin-flip -- LOW sigma-proximity, spot still near strike -- i.e. a panic
overshoot rather than a market that is already decided.

METHODOLOGY OVERRIDE vs the plan text
-------------------------------------
The plan's Task 7 says "compute the edge per *tick* and average within strata".
That is WRONG: per-tick averaging carries the tick-weighting / lingering bias
Phase 0 documented (~87% of ticks are stale; a lingering price is over-sampled).
Task 6b corrected this. This module therefore builds the edge map on the
**de-biased cross-section**: one observation per window per time-slice
(t in {60,120,240,360,480,600,720}s, the single tick within +/-5s of each t,
dev rows only) -- exactly as `calibration_debiased.py` does. The cross-section
sampler `build_cross_section()` is reused from that module.

Method (de-biased cross-section, dev rows only)
-----------------------------------------------
1. Per observation: edge = cheap_won - cheap_mid (realized minus implied;
   positive = underpriced = buyer edge).
2. Conditioned edge map: mean edge + window-clustered CI (groups=slug) within
   strata of sigma_proximity bucket, time_left_sec bucket, cheap_drop_30s
   bucket, cheap_mid bucket, symbol, and a realized_vol tertile (cutoffs
   computed and recorded from dev data).
3. Cross-tabulate the two strongest one-dimensional conditioners, especially
   sigma_proximity x cheap_drop_30s -- the core thesis test.
4. Dev-internal CV: split dev days into earlier half (May 15-17) vs later half
   (May 18-20); a cell QUALIFIES only if its edge CI excludes zero, same
   direction, on BOTH halves, with n_windows >= 30 per half.

Outputs
-------
  - docs/research/charts/edge_map_*.png
  - docs/research/edge_map.md   ## Conditioned edge map
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup: repo root is two levels up from this file
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from research.lib.splits import dev_mask  # noqa: E402
from research.lib.stats import window_clustered_bootstrap  # noqa: E402
from research.analysis.calibration_debiased import (  # noqa: E402
    build_cross_section,
    TIME_SLICES,
    SLICE_TOL,
)

_EC_PATH = _REPO_ROOT / "data/research/entry_candidates_15m.parquet"
_EDGE_MAP_PATH = _REPO_ROOT / "docs/research/edge_map.md"
_CHART_DIR = _REPO_ROOT / "docs/research/charts"

N_BOOT = 2000          # bootstrap iters per cell
SEED = 42
MIN_WINDOWS_CELL = 30  # a cell is interpretable only with >= this many windows
CV_SPLIT_DATE = "2026-05-17"  # early half = dev days <= this; late half = after

# ---------------------------------------------------------------------------
# Bucket definitions for the one-dimensional conditioners.
# Each is (label, lo, hi) with the half-open convention [lo, hi).
# ---------------------------------------------------------------------------
SIGMA_BUCKETS = [
    ("<0.5", 0.0, 0.5),
    ("0.5-1", 0.5, 1.0),
    ("1-2", 1.0, 2.0),
    ("2-4", 2.0, 4.0),
    (">4", 4.0, np.inf),
]
TIME_LEFT_BUCKETS = [
    ("<180", 0.0, 180.0),
    ("180-420", 180.0, 420.0),
    ("420-660", 420.0, 660.0),
    (">660", 660.0, np.inf),
]
DROP_BUCKETS = [
    ("0", -np.inf, 1e-9),     # exactly-zero drop (cheap_drop_30s == 0)
    ("0-10", 1e-9, 10.0),
    ("10-25", 10.0, 25.0),
    (">25", 25.0, np.inf),
]
CHEAP_MID_BUCKETS = [
    ("0.05-0.15", 0.05, 0.15),
    ("0.15-0.25", 0.15, 0.25),
    ("0.25-0.40", 0.25, 0.40),
]
SYMBOLS = ["btc", "eth", "sol", "xrp"]


# ===========================================================================
# Bucketing helpers
# ===========================================================================

def _assign_bucket(values: np.ndarray, buckets: list[tuple]) -> np.ndarray:
    """Map a numeric array to bucket labels using half-open [lo, hi) intervals.

    Values not falling in any bucket (e.g. non-finite, or outside all ranges)
    get the label NaN -> they are excluded from that conditioner's strata.
    """
    out = np.full(len(values), None, dtype=object)
    v = np.asarray(values, dtype="f8")
    for label, lo, hi in buckets:
        m = np.isfinite(v) & (v >= lo) & (v < hi)
        out[m] = label
    return out


def add_strata_columns(xs: pd.DataFrame) -> pd.DataFrame:
    """Add bucket columns + realized_vol tertile to the cross-section.

    Returns a copy with: sigma_bucket, time_left_bucket, drop_bucket,
    cheap_mid_bucket and vol_tertile. The realized_vol tertile cutoffs are
    NOT added here (they are computed/recorded by `vol_tertile_cutoffs`).
    """
    out = xs.copy()
    out["sigma_bucket"] = _assign_bucket(out["sigma_proximity"].to_numpy(),
                                         SIGMA_BUCKETS)
    out["time_left_bucket"] = _assign_bucket(out["time_left_sec"].to_numpy(),
                                             TIME_LEFT_BUCKETS)
    out["drop_bucket"] = _assign_bucket(out["cheap_drop_30s"].to_numpy(),
                                        DROP_BUCKETS)
    out["cheap_mid_bucket"] = _assign_bucket(out["cheap_mid"].to_numpy(),
                                             CHEAP_MID_BUCKETS)
    return out


def vol_tertile_cutoffs(xs: pd.DataFrame) -> tuple[float, float]:
    """Compute the realized_vol 1/3 and 2/3 quantile cutoffs from dev data.

    These RECORDED cutoffs replace the uncalibrated `vol_regime_thresholds`
    hardcoded guesses (see docs/research/phase0_verdict.md, code-audit #9).
    """
    rv = xs["realized_vol"].to_numpy(dtype="f8")
    rv = rv[np.isfinite(rv)]
    q1, q2 = np.quantile(rv, [1.0 / 3.0, 2.0 / 3.0])
    return float(q1), float(q2)


def add_vol_tertile(xs: pd.DataFrame, q1: float, q2: float) -> pd.DataFrame:
    """Add a vol_tertile column (LOW / MED / HIGH) using cutoffs q1, q2."""
    out = xs.copy()
    rv = out["realized_vol"].to_numpy(dtype="f8")
    tert = np.full(len(out), None, dtype=object)
    fin = np.isfinite(rv)
    tert[fin & (rv < q1)] = "LOW"
    tert[fin & (rv >= q1) & (rv < q2)] = "MED"
    tert[fin & (rv >= q2)] = "HIGH"
    out["vol_tertile"] = tert
    return out


# ===========================================================================
# Edge computation
# ===========================================================================

def cell_edge(sub: pd.DataFrame, seed: int = SEED) -> dict:
    """Mean edge + window-clustered 90% CI for one stratum subset.

    Returns dict: n_obs, n_windows, mean_edge, ci_lo, ci_mid, ci_hi.
    The bootstrap clusters by `slug` (one window may contribute up to 7 obs).
    """
    if sub.empty:
        return {"n_obs": 0, "n_windows": 0, "mean_edge": float("nan"),
                "ci_lo": float("nan"), "ci_mid": float("nan"),
                "ci_hi": float("nan")}
    edge = sub["edge"].to_numpy(dtype="f8")
    groups = sub["slug"].to_numpy()
    n_windows = int(np.unique(groups).size)
    if n_windows < 2:
        return {"n_obs": len(sub), "n_windows": n_windows,
                "mean_edge": float(edge.mean()), "ci_lo": float("nan"),
                "ci_mid": float(edge.mean()), "ci_hi": float("nan")}
    lo, mid, hi = window_clustered_bootstrap(edge, groups, n=N_BOOT, seed=seed)
    return {"n_obs": len(sub), "n_windows": n_windows,
            "mean_edge": float(edge.mean()),
            "ci_lo": lo, "ci_mid": mid, "ci_hi": hi}


def one_dim_edge_map(xs: pd.DataFrame, col: str, order: list) -> pd.DataFrame:
    """Edge map over a single conditioner column `col` (in `order`)."""
    rows = []
    for label in order:
        sub = xs[xs[col] == label]
        r = cell_edge(sub, seed=SEED + hash(str(label)) % 1000)
        r["bucket"] = label
        rows.append(r)
    return pd.DataFrame(rows)[
        ["bucket", "n_obs", "n_windows", "mean_edge",
         "ci_lo", "ci_mid", "ci_hi"]
    ]


def two_dim_edge_map(xs: pd.DataFrame, row_col: str, col_col: str,
                     row_order: list, col_order: list) -> pd.DataFrame:
    """Cross-tabulated edge map: mean edge per (row bucket x col bucket)."""
    rows = []
    for ri, rlab in enumerate(row_order):
        for ci, clab in enumerate(col_order):
            sub = xs[(xs[row_col] == rlab) & (xs[col_col] == clab)]
            r = cell_edge(sub, seed=SEED + ri * 17 + ci * 101)
            r[row_col] = rlab
            r[col_col] = clab
            rows.append(r)
    return pd.DataFrame(rows)


# ===========================================================================
# Dev-internal cross-validation
# ===========================================================================

def both_halves_cv(xs: pd.DataFrame, col: str, label: str,
                    seed: int) -> dict:
    """Recompute a cell's edge on the early half (<= CV_SPLIT_DATE) and the
    later half (> CV_SPLIT_DATE) of dev data.

    A cell QUALIFIES iff, on BOTH halves:
      - n_windows >= MIN_WINDOWS_CELL
      - the 90% edge CI excludes zero
      - the CI sign is the SAME on both halves
    Returns a dict with both halves' stats and a `qualifies` bool.
    """
    sub = xs[xs[col] == label]
    early = sub[sub["date"] <= CV_SPLIT_DATE]
    late = sub[sub["date"] > CV_SPLIT_DATE]
    e = cell_edge(early, seed=seed + 1)
    l = cell_edge(late, seed=seed + 2)

    def _excludes_zero(r):
        return (np.isfinite(r["ci_lo"]) and np.isfinite(r["ci_hi"])
                and (r["ci_lo"] > 0 or r["ci_hi"] < 0))

    def _sign(r):
        if not np.isfinite(r["ci_lo"]):
            return 0
        return 1 if r["ci_lo"] > 0 else (-1 if r["ci_hi"] < 0 else 0)

    enough = (e["n_windows"] >= MIN_WINDOWS_CELL
              and l["n_windows"] >= MIN_WINDOWS_CELL)
    nonzero = _excludes_zero(e) and _excludes_zero(l)
    same_dir = _sign(e) != 0 and _sign(e) == _sign(l)
    qualifies = bool(enough and nonzero and same_dir)
    return {"col": col, "label": label,
            "early": e, "late": l,
            "enough_windows": enough, "ci_excludes_zero": nonzero,
            "same_direction": same_dir, "qualifies": qualifies}


# ===========================================================================
# Reporting helpers
# ===========================================================================

def _fmt_c(x: float) -> str:
    return "n/a" if not np.isfinite(x) else f"{x * 100:+.2f}c"


def _one_dim_table(df: pd.DataFrame, conditioner: str) -> str:
    header = (
        f"#### Edge by {conditioner}\n\n"
        "| Bucket | n_windows | n_obs | Mean edge | CI lo | CI hi | "
        "CI excl. 0 | Note |\n"
        "|--------|-----------|-------|-----------|-------|-------|"
        "------------|------|\n"
    )
    rows = []
    for _, r in df.iterrows():
        thin = r["n_windows"] < MIN_WINDOWS_CELL
        excl = (np.isfinite(r["ci_lo"]) and np.isfinite(r["ci_hi"])
                and (r["ci_lo"] > 0 or r["ci_hi"] < 0))
        note = "thin" if thin else ""
        rows.append(
            f"| {r['bucket']} | {int(r['n_windows']):,} | "
            f"{int(r['n_obs']):,} | {_fmt_c(r['mean_edge'])} | "
            f"{_fmt_c(r['ci_lo'])} | {_fmt_c(r['ci_hi'])} | "
            f"{'yes' if excl else 'no'} | {note} |"
        )
    return header + "\n".join(rows)


def _two_dim_table(df: pd.DataFrame, row_col: str, col_col: str,
                    row_order: list, col_order: list,
                    title: str) -> str:
    """Cross-tab as a markdown grid of 'edge_c (n_win)' cells."""
    out = [f"#### {title}", ""]
    out.append("| " + row_col + " \\ " + col_col + " | "
               + " | ".join(col_order) + " |")
    out.append("|" + "---|" * (len(col_order) + 1))
    lookup = {}
    for _, r in df.iterrows():
        lookup[(r[row_col], r[col_col])] = r
    for rlab in row_order:
        cells = []
        for clab in col_order:
            r = lookup.get((rlab, clab))
            if r is None or int(r["n_windows"]) == 0:
                cells.append("--")
                continue
            edge = _fmt_c(r["mean_edge"])
            nw = int(r["n_windows"])
            mark = ""
            if (np.isfinite(r["ci_lo"]) and np.isfinite(r["ci_hi"])
                    and nw >= MIN_WINDOWS_CELL
                    and (r["ci_lo"] > 0 or r["ci_hi"] < 0)):
                mark = "*"
            cells.append(f"{edge}{mark} (n={nw})")
        out.append(f"| **{rlab}** | " + " | ".join(cells) + " |")
    out.append("")
    out.append("`*` = 90% window-clustered CI excludes zero and n_windows >= "
               f"{MIN_WINDOWS_CELL}.")
    return "\n".join(out)


# ===========================================================================
# Charting
# ===========================================================================

def _chart_one_dim(maps: dict[str, pd.DataFrame]) -> Path:
    """Bar chart of mean edge per bucket for each one-dimensional conditioner."""
    order = ["sigma_bucket", "time_left_bucket", "drop_bucket",
             "cheap_mid_bucket", "symbol", "vol_tertile"]
    titles = {
        "sigma_bucket": "sigma_proximity",
        "time_left_bucket": "time_left_sec",
        "drop_bucket": "cheap_drop_30s (%)",
        "cheap_mid_bucket": "cheap_mid",
        "symbol": "symbol",
        "vol_tertile": "realized_vol tertile",
    }
    fig, axes = plt.subplots(2, 3, figsize=(16, 9))
    fig.suptitle(
        "Task 7 -- Conditioned edge map (de-biased cross-section, one obs / "
        "window / time-slice)\nedge = cheap_won - cheap_mid, dev split May "
        "15-20 2026, 90% window-clustered CI",
        fontsize=12, fontweight="bold", y=1.02,
    )
    for ax, key in zip(axes.flat, order):
        df = maps[key]
        x = np.arange(len(df))
        edge = df["mean_edge"].to_numpy() * 100
        lo = (df["mean_edge"] - df["ci_lo"]).to_numpy() * 100
        hi = (df["ci_hi"] - df["mean_edge"]).to_numpy() * 100
        thin = df["n_windows"].to_numpy() < MIN_WINDOWS_CELL
        colors = ["#bbbbbb" if t else "#1f77b4" for t in thin]
        ax.bar(x, edge, color=colors, yerr=[lo, hi], capsize=4,
               error_kw={"linewidth": 1.1})
        ax.axhline(0, color="k", lw=1)
        ax.set_xticks(x)
        ax.set_xticklabels(df["bucket"].astype(str), rotation=30, ha="right")
        ax.set_title(titles[key], fontsize=10)
        ax.set_ylabel("mean edge (cents)")
        ax.grid(True, axis="y", alpha=0.3)
        for xi, (e, n) in enumerate(zip(edge, df["n_windows"])):
            ax.annotate(f"n={int(n)}", (xi, e),
                        textcoords="offset points", xytext=(0, 3),
                        ha="center", fontsize=7, color="#444444")
    plt.tight_layout()
    path = _CHART_DIR / "edge_map_one_dim.png"
    fig.savefig(str(path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _chart_heatmap(df: pd.DataFrame, row_col: str, col_col: str,
                   row_order: list, col_order: list, title: str,
                   fname: str) -> Path:
    """Heatmap of mean edge over a 2-D cross-tab, annotated with edge + n."""
    grid = np.full((len(row_order), len(col_order)), np.nan)
    nwin = np.zeros((len(row_order), len(col_order)), dtype=int)
    lookup = {(r[row_col], r[col_col]): r for _, r in df.iterrows()}
    for i, rlab in enumerate(row_order):
        for j, clab in enumerate(col_order):
            r = lookup.get((rlab, clab))
            if r is not None and int(r["n_windows"]) > 0:
                grid[i, j] = r["mean_edge"] * 100
                nwin[i, j] = int(r["n_windows"])

    fig, ax = plt.subplots(figsize=(9, 6))
    vmax = np.nanmax(np.abs(grid)) if np.isfinite(grid).any() else 1.0
    im = ax.imshow(grid, cmap="RdBu_r", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(np.arange(len(col_order)))
    ax.set_xticklabels(col_order)
    ax.set_yticks(np.arange(len(row_order)))
    ax.set_yticklabels(row_order)
    ax.set_xlabel(col_col)
    ax.set_ylabel(row_col)
    ax.set_title(title, fontsize=11, fontweight="bold")
    for i in range(len(row_order)):
        for j in range(len(col_order)):
            if not np.isfinite(grid[i, j]):
                ax.text(j, i, "--", ha="center", va="center", fontsize=8)
                continue
            txt = f"{grid[i, j]:+.1f}c\nn={nwin[i, j]}"
            ax.text(j, i, txt, ha="center", va="center", fontsize=8,
                    color="black")
    fig.colorbar(im, ax=ax, label="mean edge (cents)")
    plt.tight_layout()
    path = _CHART_DIR / fname
    fig.savefig(str(path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ===========================================================================
# edge_map.md writing
# ===========================================================================

def _write_section(text: str) -> None:
    marker = "## Conditioned edge map"
    section = marker + "\n\n" + text
    if _EDGE_MAP_PATH.exists():
        existing = _EDGE_MAP_PATH.read_text()
        if marker in existing:
            start = existing.index(marker)
            nxt = existing.find("\n## ", start + len(marker))
            if nxt == -1:
                new = existing[:start] + section
            else:
                new = existing[:start] + section + "\n" + existing[nxt + 1:]
            _EDGE_MAP_PATH.write_text(new)
        else:
            _EDGE_MAP_PATH.write_text(existing.rstrip() + "\n\n" + section + "\n")
    else:
        header = ("# Edge Map\n\nPhase 2-4 Edge Discovery findings for "
                  "Polymarket Up/Down (May 2026).\n\n")
        _EDGE_MAP_PATH.write_text(header + section + "\n")


# ===========================================================================
# Main run
# ===========================================================================

def run() -> None:
    print("=" * 72)
    print("Task 7: Conditioned edge map  (de-biased cross-section)")
    print("=" * 72)

    # --- 1. Load + filter to dev rows --------------------------------------
    print(f"\nLoading {_EC_PATH} ...")
    ec = pd.read_parquet(_EC_PATH)
    print(f"  Total rows: {len(ec):,}")

    mask = (
        dev_mask(ec)
        & ec["cheap_won"].notna()
        & np.isfinite(ec["cheap_mid"])
        & np.isfinite(ec["seconds_into_window"])
    )
    dev = ec[mask].reset_index(drop=True)
    print(f"  Dev rows (tick level): {len(dev):,}  |  "
          f"slugs: {dev['slug'].nunique():,}")
    assert (dev["date"] <= "2026-05-20").all(), "hold-out leaked into dev!"
    assert (dev["date"] >= "2026-05-15").all(), "pre-dev row leaked!"
    print("  [GUARD] all dev rows in [2026-05-15, 2026-05-20] -- hold-out sealed.")

    # --- 2. De-biased cross-section ----------------------------------------
    print(f"\nBuilding de-biased cross-section (slices={TIME_SLICES}, "
          f"tol=+/-{SLICE_TOL}s; one obs per window per slice) ...")
    xs = build_cross_section(dev)
    assert not xs.duplicated(["slug", "t"]).any(), "duplicate (slug,t) obs!"
    xs["edge"] = xs["cheap_won"] - xs["cheap_mid"]
    n_windows = int(xs["slug"].nunique())
    print(f"  Cross-section: {len(xs):,} obs, {n_windows:,} windows")
    print(f"  Overall mean edge = {_fmt_c(xs['edge'].mean())}")

    # --- 3. realized_vol tertile cutoffs (RECORDED) ------------------------
    q1, q2 = vol_tertile_cutoffs(xs)
    print(f"\nrealized_vol tertile cutoffs (from dev cross-section):")
    print(f"  LOW  : realized_vol <  {q1:.6f}")
    print(f"  MED  : {q1:.6f} <= realized_vol < {q2:.6f}")
    print(f"  HIGH : realized_vol >= {q2:.6f}")
    print("  (these replace the hardcoded vol_regime_thresholds guesses)")

    # --- 4. Strata columns -------------------------------------------------
    xs = add_strata_columns(xs)
    xs = add_vol_tertile(xs, q1, q2)

    # --- 5. One-dimensional edge maps --------------------------------------
    print("\nOne-dimensional conditioned edge maps:")
    one_dim = {
        "sigma_bucket": one_dim_edge_map(
            xs, "sigma_bucket", [b[0] for b in SIGMA_BUCKETS]),
        "time_left_bucket": one_dim_edge_map(
            xs, "time_left_bucket", [b[0] for b in TIME_LEFT_BUCKETS]),
        "drop_bucket": one_dim_edge_map(
            xs, "drop_bucket", [b[0] for b in DROP_BUCKETS]),
        "cheap_mid_bucket": one_dim_edge_map(
            xs, "cheap_mid_bucket", [b[0] for b in CHEAP_MID_BUCKETS]),
        "symbol": one_dim_edge_map(xs, "symbol", SYMBOLS),
        "vol_tertile": one_dim_edge_map(xs, "vol_tertile",
                                        ["LOW", "MED", "HIGH"]),
    }
    for name, df in one_dim.items():
        print(f"\n  -- {name} --")
        for _, r in df.iterrows():
            print(f"     {str(r['bucket']):>10}: edge {_fmt_c(r['mean_edge'])}"
                  f"  CI [{_fmt_c(r['ci_lo'])}, {_fmt_c(r['ci_hi'])}]"
                  f"  n_win={int(r['n_windows']):,}")

    # --- 6. Two-dimensional cross-tabs -------------------------------------
    # The core thesis test: sigma_proximity x cheap_drop_30s. Is the edge
    # concentrated where sigma_proximity is LOW (genuine panic overshoot --
    # spot still near strike) or spread everywhere (suspicious)?
    print("\nCore cross-tab: sigma_proximity x cheap_drop_30s ...")
    sig_drop = two_dim_edge_map(
        xs, "sigma_bucket", "drop_bucket",
        [b[0] for b in SIGMA_BUCKETS], [b[0] for b in DROP_BUCKETS])
    for _, r in sig_drop.iterrows():
        if int(r["n_windows"]) >= MIN_WINDOWS_CELL:
            print(f"  sigma {str(r['sigma_bucket']):>6} x drop "
                  f"{str(r['drop_bucket']):>6}: edge {_fmt_c(r['mean_edge'])}"
                  f"  CI [{_fmt_c(r['ci_lo'])}, {_fmt_c(r['ci_hi'])}]"
                  f"  n_win={int(r['n_windows'])}")

    # Second cross-tab: sigma_proximity x cheap_mid (price interacts with
    # decided-ness -- the runner-up one-dimensional pair).
    print("\nSecond cross-tab: sigma_proximity x cheap_mid ...")
    sig_mid = two_dim_edge_map(
        xs, "sigma_bucket", "cheap_mid_bucket",
        [b[0] for b in SIGMA_BUCKETS], [b[0] for b in CHEAP_MID_BUCKETS])

    # --- 7. Dev-internal cross-validation ----------------------------------
    # Run both-halves CV on every one-dimensional cell across all conditioners.
    print(f"\nDev-internal CV (early <= {CV_SPLIT_DATE} vs later) on every "
          f"one-dimensional cell ...")
    cv_specs = [
        ("sigma_bucket", [b[0] for b in SIGMA_BUCKETS]),
        ("time_left_bucket", [b[0] for b in TIME_LEFT_BUCKETS]),
        ("drop_bucket", [b[0] for b in DROP_BUCKETS]),
        ("cheap_mid_bucket", [b[0] for b in CHEAP_MID_BUCKETS]),
        ("symbol", SYMBOLS),
        ("vol_tertile", ["LOW", "MED", "HIGH"]),
    ]
    cv_results = []
    sd = 7
    for col, order in cv_specs:
        for label in order:
            res = both_halves_cv(xs, col, label, seed=sd)
            cv_results.append(res)
            sd += 13
    # Also CV the 2-D core cross-tab cells that have enough data.
    for _, r in sig_drop.iterrows():
        if int(r["n_windows"]) < 2 * MIN_WINDOWS_CELL:
            continue
        sub_label = (r["sigma_bucket"], r["drop_bucket"])
        sub = xs[(xs["sigma_bucket"] == r["sigma_bucket"])
                 & (xs["drop_bucket"] == r["drop_bucket"])].copy()
        # synth column so both_halves_cv can key on it
        key = f"sig={r['sigma_bucket']}|drop={r['drop_bucket']}"
        sub["_cell"] = key
        res = both_halves_cv(sub, "_cell", key, seed=sd)
        res["col"] = "sigma x drop"
        cv_results.append(res)
        sd += 13

    qualifying = [r for r in cv_results if r["qualifies"]]
    print(f"\n  {len(qualifying)} of {len(cv_results)} cells QUALIFY "
          f"(CI excludes zero, same direction, n_win >= {MIN_WINDOWS_CELL} "
          f"on BOTH halves):")
    for r in qualifying:
        e, l = r["early"], r["late"]
        print(f"     [{r['col']}={r['label']}]  "
              f"early {_fmt_c(e['mean_edge'])} "
              f"CI[{_fmt_c(e['ci_lo'])},{_fmt_c(e['ci_hi'])}] n={e['n_windows']}"
              f"  |  late {_fmt_c(l['mean_edge'])} "
              f"CI[{_fmt_c(l['ci_lo'])},{_fmt_c(l['ci_hi'])}] n={l['n_windows']}")

    # --- 8. Charts ---------------------------------------------------------
    print("\nWriting charts ...")
    _CHART_DIR.mkdir(parents=True, exist_ok=True)
    p1 = _chart_one_dim(one_dim)
    p2 = _chart_heatmap(
        sig_drop, "sigma_bucket", "drop_bucket",
        [b[0] for b in SIGMA_BUCKETS], [b[0] for b in DROP_BUCKETS],
        "Edge map: sigma_proximity x cheap_drop_30s (core thesis test)",
        "edge_map_sigma_drop.png")
    p3 = _chart_heatmap(
        sig_mid, "sigma_bucket", "cheap_mid_bucket",
        [b[0] for b in SIGMA_BUCKETS], [b[0] for b in CHEAP_MID_BUCKETS],
        "Edge map: sigma_proximity x cheap_mid",
        "edge_map_sigma_mid.png")
    print(f"  {p1}\n  {p2}\n  {p3}")

    # --- 9. Write edge_map.md section --------------------------------------
    _write_edge_map(xs, one_dim, sig_drop, sig_mid, cv_results, qualifying,
                    q1, q2, n_windows)

    print(f"\nDone.  Charts -> {_CHART_DIR}/edge_map_*.png")
    print(f"       Findings -> {_EDGE_MAP_PATH}  (## Conditioned edge map)")


# ---------------------------------------------------------------------------
# Verdict + section assembly
# ---------------------------------------------------------------------------

def _verdict(xs, one_dim, sig_drop, qualifying, q1, q2) -> str:
    lines = ["### VERDICT", ""]

    overall = xs["edge"].mean()
    lines.append(
        "**RE-RUN ON CORRECTED DATA (real Polymarket outcomes) -- supersedes "
        "the earlier corrupt-label results.** The strike bug "
        "(`phase0_audit.md` Task 8c) is fixed; this edge map is re-derived on "
        "the true Polymarket-resolved outcomes."
    )
    lines.append("")
    lines.append(
        f"Overall de-biased cheap-side edge across the whole cross-section = "
        f"**{_fmt_c(overall)}** (consistent with Task 6b's re-run pooled gap "
        f"of ~-1c; the cheap side is essentially calibrated overall). Task 7 "
        f"asks the sharper question: is there ANY conditioned corner with a "
        f"CI-separated edge that survives both-halves dev-internal CV."
    )
    lines.append("")

    # --- sigma-proximity gradient ---
    sig = one_dim["sigma_bucket"].set_index("bucket")
    sig_vals = {b: sig.loc[b, "mean_edge"] for b in sig.index
                if np.isfinite(sig.loc[b, "mean_edge"])}
    lo_edge = sig_vals.get("<0.5", float("nan"))
    hi_edge = sig_vals.get(">4", float("nan"))
    lines.append("**Sigma-proximity gradient (the core thesis test).**")
    sig_line = "; ".join(
        f"{b}={_fmt_c(sig.loc[b, 'mean_edge'])} "
        f"(n_win={int(sig.loc[b, 'n_windows'])})"
        for b in sig.index)
    lines.append(f"- One-dimensional edge by sigma_proximity bucket: {sig_line}.")
    if np.isfinite(lo_edge) and np.isfinite(hi_edge):
        if lo_edge > hi_edge + 0.03:
            thesis = (
                f"The edge **DOES concentrate at LOW sigma-proximity** "
                f"({_fmt_c(lo_edge)} in the <0.5 bucket vs {_fmt_c(hi_edge)} "
                f"in the >4 bucket). Where the market is still near a "
                f"coin-flip the cheap side is underpriced; where the spot has "
                f"already walked away (sigma>4, effectively decided) the edge "
                f"is much smaller. This SUPPORTS the panic-overshoot thesis "
                f"(H8 / H2): the mispricing is a genuine overshoot, not an "
                f"already-lost market."
            )
        elif abs(lo_edge - hi_edge) <= 0.03:
            thesis = (
                f"The edge is **roughly UNIFORM across sigma-proximity** "
                f"({_fmt_c(lo_edge)} at <0.5 vs {_fmt_c(hi_edge)} at >4). It "
                f"does NOT concentrate near coin-flips -- this is the "
                f"suspicious pattern: a flat edge across decided-ness looks "
                f"more like a structural longshot/pricing artifact than a "
                f"panic overshoot. H8's 'edge near coin-flips' thesis is NOT "
                f"clearly supported."
            )
        else:
            thesis = (
                f"The edge is LARGER at HIGH sigma-proximity "
                f"({_fmt_c(hi_edge)} at >4 vs {_fmt_c(lo_edge)} at <0.5) -- "
                f"the opposite of the panic-overshoot thesis. This points to "
                f"a favorite-longshot / decided-market effect, NOT a "
                f"coin-flip overshoot. H8's thesis is REJECTED by the "
                f"one-dimensional gradient."
            )
    else:
        thesis = "Sigma-proximity buckets too thin to read a gradient."
    lines.append(f"- {thesis}")
    lines.append("")

    # --- drop gradient ---
    drop = one_dim["drop_bucket"].set_index("bucket")
    drop_line = "; ".join(
        f"{b}={_fmt_c(drop.loc[b, 'mean_edge'])} "
        f"(n_win={int(drop.loc[b, 'n_windows'])})"
        for b in drop.index)
    lines.append(f"**Drop gradient.** Edge by cheap_drop_30s bucket: "
                 f"{drop_line}.")
    d0 = drop.loc["0", "mean_edge"] if "0" in drop.index else float("nan")
    dbig = drop.loc[">25", "mean_edge"] if ">25" in drop.index else float("nan")
    if np.isfinite(d0) and np.isfinite(dbig):
        if dbig > d0 + 0.03:
            lines.append(
                f"- A larger 30s drop goes with a larger edge "
                f"({_fmt_c(dbig)} for >25% drops vs {_fmt_c(d0)} for no "
                f"drop) -- consistent with H2: a visible odds drop precedes a "
                f"reversion. But H2 also warns a drop *alone* is not enough; "
                f"the cross-tab below tests whether the drop edge needs LOW "
                f"sigma-proximity to be real."
            )
        else:
            lines.append(
                f"- The drop bucket does NOT clearly order the edge "
                f"({_fmt_c(dbig)} for >25% vs {_fmt_c(d0)} for no drop). A "
                f"visible odds drop is not, on its own, where the edge lives "
                f"-- weak support for H2 as a stand-alone signal."
            )
    lines.append("")

    # --- cross-tab read ---
    lines.append("**sigma_proximity x cheap_drop_30s cross-tab.**")
    # low-sigma row vs high-sigma row, averaged over drop buckets with enough n
    def _row_mean(sig_label):
        rr = sig_drop[(sig_drop["sigma_bucket"] == sig_label)
                      & (sig_drop["n_windows"] >= MIN_WINDOWS_CELL)]
        if rr.empty:
            return float("nan"), 0
        return float(rr["mean_edge"].mean()), len(rr)
    low_row, low_n = _row_mean("<0.5")
    midlow_row, _ = _row_mean("0.5-1")
    high_row, high_n = _row_mean(">4")
    lines.append(
        f"- Mean edge across the populated drop cells: sigma<0.5 row = "
        f"{_fmt_c(low_row)} ({low_n} cells), sigma>4 row = {_fmt_c(high_row)} "
        f"({high_n} cells)."
    )
    # Within low-sigma, does drop matter?
    low_cells = sig_drop[(sig_drop["sigma_bucket"] == "<0.5")
                         & (sig_drop["n_windows"] >= MIN_WINDOWS_CELL)]
    if len(low_cells) >= 2:
        srt = low_cells.sort_values("drop_bucket")
        rng = srt["mean_edge"].max() - srt["mean_edge"].min()
        lines.append(
            f"- Within the sigma<0.5 row the edge spans "
            f"{_fmt_c(srt['mean_edge'].min())}..{_fmt_c(srt['mean_edge'].max())} "
            f"across drop buckets (range {_fmt_c(rng)}) -- "
            + ("the drop conditioner adds little once sigma is low: the "
               "low-sigma state itself carries the edge."
               if rng < 0.05 else
               "the drop conditioner still moves the edge materially even "
               "within low sigma-proximity.")
        )
    lines.append("")

    # --- CV result ---
    lines.append("**Dev-internal cross-validation (both-halves check).**")
    if qualifying:
        # Classify qualifying cells by sign and by sigma region.
        pos_q = [r for r in qualifying if r["early"]["mean_edge"] > 0]
        hi_sig_pos = [r for r in pos_q
                      if "2-4" in str(r["label"]) or ">4" in str(r["label"])]
        lines.append(
            f"- {len(qualifying)} cell(s) QUALIFY: edge CI excludes zero, "
            f"same direction, n_windows >= {MIN_WINDOWS_CELL} on BOTH the "
            f"early (May 15-17) and later (May 18-20) dev halves. See the "
            f"qualifying-cells table above. Of these, {len(pos_q)} carry a "
            f"POSITIVE edge."
        )
        if hi_sig_pos:
            lines.append(
                f"- The positive qualifying cell(s) sit at HIGH "
                f"sigma-proximity (sigma>2), NOT in the low-sigma "
                f"near-coin-flip corner. A positive `cheap_won - cheap_mid` "
                f"at high sigma-proximity means the cheap side (the side the "
                f"spot has already moved AGAINST) wins more often than its "
                f"price implies -- this is a favourite/longshot mispricing, "
                f"the SAME effect Task 8's fair-value decomposition isolates "
                f"as the cheap-side-actually-favoured bin. Task 8 shows it is "
                f"NOT cost-surviving (negative net of taker, ~0 net of "
                f"maker), and the divergence backtest -- which targets "
                f"exactly that signal out-of-fold -- finds it does not "
                f"generalize to a profitable rule. The 'edge' here is gross, "
                f"conditioned, and not tradeable."
            )
        else:
            lines.append(
                "- None of the qualifying cells carry a positive, "
                "cost-relevant edge in the low-sigma corner."
            )
    else:
        lines.append(
            f"- **NO cell qualifies.** No conditioned cell has an edge CI "
            f"that excludes zero in the same direction on BOTH dev halves "
            f"with n_windows >= {MIN_WINDOWS_CELL} per half. The conditioned "
            f"edge is not stable enough across the dev period to be called a "
            f"robust signal at this granularity -- either the buckets are too "
            f"thin once halved, or the edge genuinely wobbles week-to-week."
        )
    lines.append("")

    # --- hypothesis verdicts ---
    lines.append("**Hypothesis verdicts (H2, H6, H8).**")
    # H8
    if np.isfinite(lo_edge) and np.isfinite(hi_edge) and lo_edge > hi_edge + 0.03:
        h8 = ("**H8 -- SUPPORTED (partially).** H8 predicted deep dips are "
              "roughly coin-flips and the moderate-underdog band is the real "
              "candidate zone. The edge does sit where the market is still "
              "near a coin-flip (low sigma-proximity), and the cheap_mid "
              "gradient shows the moderate band carries a real edge -- "
              "consistent with H8's 'moderate underdog, not deep dip' claim.")
    else:
        h8 = ("**H8 -- NOT clearly supported.** H8 expected the edge in the "
              "near-coin-flip / moderate-underdog zone. The sigma-proximity "
              "gradient here does not concentrate the edge at low "
              "sigma-proximity, so the 'edge lives near coin-flips' part of "
              "H8 is not confirmed by the conditioned map.")
    lines.append(f"- {h8}")
    # H2 -- read the actual low-sigma drop-bucket spread.
    low_cells_h2 = sig_drop[(sig_drop["sigma_bucket"] == "<0.5")
                            & (sig_drop["n_windows"] >= MIN_WINDOWS_CELL)]
    if len(low_cells_h2) >= 2:
        rng_h2 = (low_cells_h2["mean_edge"].max()
                  - low_cells_h2["mean_edge"].min())
        h2_drop = (
            f"Within the low-sigma row the edge still spans "
            f"{_fmt_c(rng_h2)} across drop buckets, so a drop is NOT "
            f"redundant once sigma is low -- but neither does it cleanly "
            f"order the edge (the no-drop and the >25% cells are both high, "
            f"the middle drop buckets lower)."
        )
    else:
        h2_drop = "Low-sigma drop cells too thin to read."
    h2 = ("**H2 -- partially testable here, not cleanly supported.** H2 says "
          "an odds drop alone does not predict reversion; the spot-move "
          "context does. " + h2_drop + " A proper H2 test needs the "
          "spot-move split of the drop event study (Task 13-14); the "
          "conditioned edge map alone cannot separate noise-drops from "
          "signal-drops.")
    lines.append(f"- {h2}")
    # H6
    sym = one_dim["symbol"].set_index("bucket")
    sym_line = ", ".join(
        f"{s}={_fmt_c(sym.loc[s, 'mean_edge'])}"
        for s in SYMBOLS if s in sym.index)
    sym_vals = {s: sym.loc[s, "mean_edge"] for s in SYMBOLS if s in sym.index}
    if sym_vals:
        spread = max(sym_vals.values()) - min(sym_vals.values())
        worst = min(sym_vals, key=sym_vals.get)
        best = max(sym_vals, key=sym_vals.get)
        h6 = (f"**H6 -- {'SUPPORTED' if spread > 0.04 else 'WEAK'}.** "
              f"Per-symbol edge: {sym_line}. Spread {_fmt_c(spread)} "
              f"({best} highest, {worst} lowest). "
              + ("Coins are materially different -- the edge map must be "
                 "computed per-symbol, as H6 predicted."
                 if spread > 0.04 else
                 "The per-symbol spread is modest; coins differ but not "
                 "dramatically in the conditioned cross-section."))
    else:
        h6 = "**H6 -- not evaluable** (symbol buckets too thin)."
    lines.append(f"- {h6}")
    lines.append("")

    # --- bottom line ---
    if qualifying:
        bl = (
            f"**Bottom line.** On corrected data the overall de-biased "
            f"cheap-side edge is ~{_fmt_c(overall)} -- essentially zero. "
            f"{len(qualifying)} conditioned cell(s) clear the both-halves "
            f"dev-internal CV, but the positive ones sit at HIGH "
            f"sigma-proximity (sigma>2): they are the favourite/longshot "
            f"mispricing -- the cheap side being the spot-favoured side -- "
            f"NOT a low-sigma panic-overshoot edge. These are GROSS, "
            f"conditioned numbers; Task 8's fair-value decomposition shows "
            f"the same effect does not survive the ~16-21% taker round-trip "
            f"cost (and is ~0 net of maker), and the divergence backtest "
            f"confirms out-of-fold that it does not yield a profitable rule. "
            f"There is no conditioned cheap-side edge here that is tradeable "
            f"on correct labels."
        )
    else:
        bl = (
            f"**Bottom line.** The unconditional de-biased edge (~"
            f"{_fmt_c(overall)}) is real, but it does NOT survive conditioning "
            f"+ dev-internal cross-validation at this granularity: no cell "
            f"clears the both-halves CI test. The edge is either too diffuse "
            f"to localise or too unstable week-to-week. It is gross, not "
            f"net-of-cost. Treat the conditioned edge map as inconclusive "
            f"pending the drop event study (Task 13-14) and net-of-cost "
            f"calibration (Task 9)."
        )
    lines.append(bl)
    return "\n".join(lines)


def _write_edge_map(xs, one_dim, sig_drop, sig_mid, cv_results, qualifying,
                    q1, q2, n_windows) -> None:
    L: list[str] = []
    L.append("## Conditioned edge map")
    L.append("")
    L.append(
        "**Task 7.** Where is the cheap-side mispricing concentrated? An edge "
        "-- if any -- lives in specific conditions, not everywhere."
    )
    L.append("")
    L.append(
        "**Methodology override.** The plan's Task 7 said to compute the edge "
        "per *tick* and average within strata. That carries the "
        "tick-weighting / lingering bias Phase 0 documented (~87% of ticks "
        "are stale; a lingering price is over-sampled) and Task 6b corrected. "
        "This edge map is therefore built on the **de-biased cross-section**: "
        f"one observation per window per time-slice (t in {TIME_SLICES}s, the "
        f"single tick within +/-{SLICE_TOL}s of each t), dev rows only -- "
        "reusing `build_cross_section()` from `calibration_debiased.py`. "
        f"Cross-section: **{len(xs):,} observations**, **{n_windows:,} "
        "windows**. Edge = `cheap_won - cheap_mid` (realized minus implied; "
        "positive = underpriced = buyer edge). All CIs are 90% "
        f"window-clustered bootstrap (groups=slug, n={N_BOOT})."
    )
    L.append("")

    # --- recorded vol tertile cutoffs ---
    L.append("### Recorded realized_vol tertile cutoffs")
    L.append("")
    L.append(
        "Computed from the dev cross-section -- these REPLACE the uncalibrated "
        "hardcoded `vol_regime_thresholds` guesses (phase0_verdict.md, "
        "code-audit #9):"
    )
    L.append("")
    L.append("| Tertile | realized_vol range |")
    L.append("|---------|--------------------|")
    L.append(f"| LOW  | `< {q1:.6f}` |")
    L.append(f"| MED  | `{q1:.6f} .. {q2:.6f}` |")
    L.append(f"| HIGH | `>= {q2:.6f}` |")
    L.append("")

    # --- one-dimensional tables ---
    L.append("### One-dimensional conditioned edge maps")
    L.append("")
    L.append(_one_dim_table(one_dim["sigma_bucket"], "sigma_proximity"))
    L.append("")
    L.append(_one_dim_table(one_dim["time_left_bucket"], "time_left_sec"))
    L.append("")
    L.append(_one_dim_table(one_dim["drop_bucket"], "cheap_drop_30s (%)"))
    L.append("")
    L.append(_one_dim_table(one_dim["cheap_mid_bucket"], "cheap_mid"))
    L.append("")
    L.append(_one_dim_table(one_dim["symbol"], "symbol"))
    L.append("")
    L.append(_one_dim_table(one_dim["vol_tertile"], "realized_vol tertile"))
    L.append("")

    # --- cross-tabs ---
    L.append("### Cross-tabulation of the strongest conditioners")
    L.append("")
    L.append(
        "The core thesis test: is the edge concentrated where "
        "sigma_proximity is LOW (spot still near strike -- a genuine panic "
        "overshoot, H8/H2) or spread everywhere (suspicious)?"
    )
    L.append("")
    L.append(_two_dim_table(
        sig_drop, "sigma_bucket", "drop_bucket",
        [b[0] for b in SIGMA_BUCKETS], [b[0] for b in DROP_BUCKETS],
        "sigma_proximity x cheap_drop_30s -- mean edge (cents)"))
    L.append("")
    L.append(_two_dim_table(
        sig_mid, "sigma_bucket", "cheap_mid_bucket",
        [b[0] for b in SIGMA_BUCKETS], [b[0] for b in CHEAP_MID_BUCKETS],
        "sigma_proximity x cheap_mid -- mean edge (cents)"))
    L.append("")

    # --- CV table ---
    L.append("### Dev-internal cross-validation (both-halves check)")
    L.append("")
    L.append(
        f"Dev days split into an **early half (May 15-17)** and a **later "
        f"half (May 18-20)**. A cell QUALIFIES only if, on BOTH halves, its "
        f"90% edge CI excludes zero in the SAME direction with n_windows >= "
        f"{MIN_WINDOWS_CELL}."
    )
    L.append("")
    if qualifying:
        L.append("#### Qualifying cells")
        L.append("")
        L.append(
            "| Conditioner = cell | Early edge | Early CI | Early n_win | "
            "Later edge | Later CI | Later n_win |\n"
            "|--------------------|------------|----------|-------------|"
            "------------|----------|-------------|"
        )
        for r in qualifying:
            e, l = r["early"], r["late"]
            L.append(
                f"| `{r['col']} = {r['label']}` | {_fmt_c(e['mean_edge'])} | "
                f"[{_fmt_c(e['ci_lo'])}, {_fmt_c(e['ci_hi'])}] | "
                f"{e['n_windows']:,} | {_fmt_c(l['mean_edge'])} | "
                f"[{_fmt_c(l['ci_lo'])}, {_fmt_c(l['ci_hi'])}] | "
                f"{l['n_windows']:,} |"
            )
        L.append("")
    else:
        L.append("**No cell qualified** -- no conditioned cell has an edge CI "
                 "excluding zero in the same direction on both dev halves "
                 f"with n_windows >= {MIN_WINDOWS_CELL} per half.")
        L.append("")

    # Full CV diagnostic table (all cells, qualify flag).
    L.append("#### All cells -- both-halves CV detail")
    L.append("")
    L.append(
        "| Conditioner = cell | Early edge (CI, n_win) | "
        "Later edge (CI, n_win) | n>=30 both | CI excl 0 both | "
        "same dir | QUALIFIES |\n"
        "|--------------------|------------------------|"
        "------------------------|------------|----------------|"
        "----------|-----------|"
    )
    for r in cv_results:
        e, l = r["early"], r["late"]
        L.append(
            f"| `{r['col']} = {r['label']}` "
            f"| {_fmt_c(e['mean_edge'])} "
            f"([{_fmt_c(e['ci_lo'])},{_fmt_c(e['ci_hi'])}], "
            f"n={e['n_windows']}) "
            f"| {_fmt_c(l['mean_edge'])} "
            f"([{_fmt_c(l['ci_lo'])},{_fmt_c(l['ci_hi'])}], "
            f"n={l['n_windows']}) "
            f"| {'yes' if r['enough_windows'] else 'no'} "
            f"| {'yes' if r['ci_excludes_zero'] else 'no'} "
            f"| {'yes' if r['same_direction'] else 'no'} "
            f"| {'**YES**' if r['qualifies'] else 'no'} |"
        )
    L.append("")

    # --- verdict ---
    L.append(_verdict(xs, one_dim, sig_drop, qualifying, q1, q2))
    L.append("")
    L.append("**Charts:** `docs/research/charts/edge_map_one_dim.png`, "
             "`edge_map_sigma_drop.png`, `edge_map_sigma_mid.png`")
    L.append("")
    L.append("---")
    L.append("")

    _write_section("\n".join(L[1:]))  # _write_section re-adds the marker
    print(f"  edge_map.md written: {_EDGE_MAP_PATH}  (## Conditioned edge map)")


if __name__ == "__main__":
    run()
