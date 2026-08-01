"""EDGE ATLAS — the complete empirical map of where the Polymarket 15m Up/Down
order book is miscalibrated beyond real trading costs, cross-referenced against
what the deployed / candidate strategies already harvest.

Pre-registered in docs/research/test_ledger.md (EA1, 2026-06-10) BEFORE the future
column was computed. The systematic complement to hypothesis-by-hypothesis hunting:
instead of asking "does edge X work", grid the whole decision space and measure the
realized win rate of BUYING each cell against what the book charged, net of the
live-calibrated clean-fill slippage.

Method (one observation per slug x cell = the FIRST tick of the window landing in
the cell; window-clustered stats; dev+holdout first; FDR-gated future reveal):

  side       BUY-THE-FAVOURITE (fav ask 0.50-0.95) and BUY-THE-CHEAP-SIDE
             (cheap ask 0.05-0.50), 0.05 bins  [9 bins each]
  time_left  (0,30] (30,60] (60,120] (120,180] (180,300] (300,450] (450,900] s
  |cl_dist|  Chainlink distance from the Chainlink strike: [0,2) [2,5) [5,12)
             [12,25) 25+ bps, NaN its own bin  [6 bins]
  consistent book favourite agrees / disagrees with the Coinbase spot side  [2]

  per cell:  n (windows), realized P(bought side wins | Chainlink settle),
             edge per $1 staked = E[won/cost] - 1, gross (cost=ask) and net
             (cost=ask+0.0072 clean-fill slippage, zero fees — live pays none).

Cost model (stated limitation): CELL-LEVEL approximation — the live-calibrated
mean clean-fill slippage (fill_model_live.json mean_slip_filled=+0.0072) on top of
the displayed best ask, zero fees. NO full ladder walk / zero-fill hazard at atlas
granularity; capacity is reported via the side's displayed top-of-book depth.
Surviving cells must be re-validated with the full live fill model before deploy.

Multiplicity discipline (fixed in advance): a cell is a CANDIDATE only if
dev CI-lower(net) > 0 AND holdout net > 0 AND n_dev >= 40, and it survives
Benjamini-Hochberg (FDR 10%) across all tested cells (n_dev >= 10) on one-sided
dev bootstrap p-values. The NEGATIVE tail is gated symmetrically (fade/avoid
zones). ONLY survivors get their FUTURE column revealed, once.

Run from the repo root:
    uv run python -m research.analysis.edge_atlas --build           # dev+holdout
    uv run python -m research.analysis.edge_atlas --reveal-future   # candidates only
    uv run python -m research.analysis.edge_atlas --print           # tables from artifact
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(REPO, "data", "research", "edge_atlas")
CELLS_PATH = os.path.join(OUT_DIR, "atlas_cells.parquet")
META_PATH = os.path.join(OUT_DIR, "atlas_meta.json")
FILL_PARAMS_PATH = os.path.join(REPO, "data", "research", "fill_model_live.json")
SQ_LEDGER = os.path.join(REPO, "data", "research", "ledgers", "sq_full.parquet")

# --- pre-registered grid + gates (docs/research/test_ledger.md EA1, 2026-06-10) ---
SIDES = ("FAV", "CHEAP")
ASK_STEP = 0.05
N_ASK = 9                                   # 9 x 0.05 bins per side
FAV_LO, FAV_HI = 0.50, 0.95
CHEAP_LO, CHEAP_HI = 0.05, 0.50
TL_EDGES = (0.0, 30.0, 60.0, 120.0, 180.0, 300.0, 450.0, 900.0)   # (lo, hi] bins
CLD_EDGES = (0.0, 2.0, 5.0, 12.0, 25.0)     # [lo, hi) bins, last is 25+, then NaN
N_CLD = len(CLD_EDGES) + 1                  # + the NaN bin
DEFAULT_SLIP = 0.0072                       # fallback if fill_model_live.json absent

FDR_Q = 0.10            # Benjamini-Hochberg level, per tail
N_DEV_TEST = 10         # min dev windows for a cell to enter the BH family
N_DEV_CAND = 40         # min dev windows for candidacy
N_FUT_CONFIRM = 30      # min future windows for a confirmation verdict
CI_N, CI_SEED = 3000, 0     # window-clustered bootstrap CI (5%/95%)
P_N, P_SEED = 10000, 1      # one-sided bootstrap p-value draws
HARVEST_COVER = 0.50    # a candidate is UNHARVESTED if max decision-set coverage < this

# near-strike fade region (task definition; OP4 family) for the harvested overlay
FADE_FAV_MIN, FADE_P_MAX = 0.75, 0.60
FADE_TL = (60.0, 360.0)

TL_LABELS = tuple(f"tl{int(TL_EDGES[i])}-{int(TL_EDGES[i + 1])}"
                  for i in range(len(TL_EDGES) - 1))
CLD_LABELS = ("cl0-2", "cl2-5", "cl5-12", "cl12-25", "cl25+", "clNA")
ASK_LABELS = {
    "FAV": tuple(f"a{FAV_LO + i * ASK_STEP:.2f}-{FAV_LO + (i + 1) * ASK_STEP:.2f}"
                 for i in range(N_ASK)),
    "CHEAP": tuple(f"a{CHEAP_LO + i * ASK_STEP:.2f}-{CHEAP_LO + (i + 1) * ASK_STEP:.2f}"
                   for i in range(N_ASK)),
}


# ==========================================================================
# pure helpers (unit-tested in tests/research/test_edge_atlas.py)
# ==========================================================================
def ask_bin(ask, lo: float, hi: float, step: float = ASK_STEP) -> np.ndarray:
    """0.05-wide ask-bin index in [lo, hi); -1 outside / non-finite.
    Bin i = [lo + i*step, lo + (i+1)*step)."""
    a = np.asarray(ask, dtype="f8")
    with np.errstate(invalid="ignore"):
        idx = np.floor((a - lo) / step + 1e-9)
    n = int(round((hi - lo) / step))
    bad = ~np.isfinite(a) | (a < lo) | (a >= hi - 1e-12)
    idx = np.where(bad, -1, idx).astype(int)
    idx[idx >= n] = -1
    return idx


def tl_bin(tl) -> np.ndarray:
    """time_left bin index for (lo, hi] edges TL_EDGES; -1 outside (0, 900]."""
    t = np.asarray(tl, dtype="f8")
    idx = np.searchsorted(TL_EDGES, t, side="left") - 1
    idx = np.where(np.isfinite(t) & (t > TL_EDGES[0]) & (t <= TL_EDGES[-1]), idx, -1)
    return idx.astype(int)


def cld_bin(abs_cl_dist) -> np.ndarray:
    """|cl_dist| bin index over [lo, hi) edges CLD_EDGES (last bin = 25+);
    NaN maps to its own bin (index N_CLD-1)."""
    v = np.asarray(abs_cl_dist, dtype="f8")
    idx = np.searchsorted(CLD_EDGES, v, side="right") - 1
    idx = np.clip(idx, 0, len(CLD_EDGES) - 1)
    return np.where(np.isfinite(v), idx, N_CLD - 1).astype(int)


def encode_cell(side_i, ask_i, tl_i, cld_i, cons) -> np.ndarray:
    """Pack (side, ask, tl, cld, consistent) into one small int code."""
    n_tl = len(TL_EDGES) - 1
    return (((np.asarray(side_i) * N_ASK + np.asarray(ask_i)) * n_tl
             + np.asarray(tl_i)) * N_CLD + np.asarray(cld_i)) * 2 \
        + np.asarray(cons).astype(int)


def decode_cell(code: int) -> dict:
    """Inverse of encode_cell -> dict of labels."""
    n_tl = len(TL_EDGES) - 1
    cons = code % 2
    code //= 2
    cld = code % N_CLD
    code //= N_CLD
    tl = code % n_tl
    code //= n_tl
    ask = code % N_ASK
    side = code // N_ASK
    s = SIDES[side]
    return dict(side=s, ask_bin=ASK_LABELS[s][ask], tl_bin=TL_LABELS[tl],
                cld_bin=CLD_LABELS[cld], cons=("C" if cons else "D"))


def cell_label(code: int) -> str:
    d = decode_cell(int(code))
    return f"{d['side']}|{d['ask_bin']}|{d['tl_bin']}|{d['cld_bin']}|{d['cons']}"


def per_obs_returns(won, ask, slip: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-$1-staked returns: gross = won/ask - 1; net = won/(ask+slip) - 1.
    Buying x$1 at cost c yields 1/c shares paying $1 each iff won."""
    w = np.asarray(won, dtype="f8")
    a = np.asarray(ask, dtype="f8")
    return w / a - 1.0, w / (a + slip) - 1.0


def bootstrap_p_one_sided(values, n: int = P_N, seed: int = P_SEED,
                          chunk: int = 2000) -> tuple[float, float]:
    """One-sided bootstrap p-values for the mean: (P(mean<=0), P(mean>=0)),
    add-one smoothed. Plain iid resampling over observations — valid here because
    atlas cells hold EXACTLY one observation per window (cluster == row), so the
    window-clustered law degenerates to the iid law."""
    v = np.asarray(values, dtype="f8")
    m = len(v)
    if m == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    le = ge = 0
    done = 0
    while done < n:
        k = min(chunk, n - done)
        means = v[rng.integers(0, m, size=(k, m))].mean(axis=1)
        le += int((means <= 0).sum())
        ge += int((means >= 0).sum())
        done += k
    return (1 + le) / (n + 1), (1 + ge) / (n + 1)


def clustered_ci(values, groups, n: int = CI_N, seed: int = CI_SEED):
    """(p5, p50, p95) of the window-clustered bootstrap mean — same resampling law
    (identical RNG draws) as research.lib.stats.window_clustered_bootstrap, computed
    O(groups)/iter; equality is asserted in tests/research/test_edge_atlas.py."""
    from research.analysis.oracle_print_model import clustered_bootstrap_fast
    return clustered_bootstrap_fast(values, groups, n=n, seed=seed)


def benjamini_hochberg(pvals, q: float = FDR_Q) -> np.ndarray:
    """BH step-up rejections. NaN p-values are not part of the family."""
    p = np.asarray(pvals, dtype="f8")
    ok = np.isfinite(p)
    m = int(ok.sum())
    rej = np.zeros(len(p), dtype=bool)
    if m == 0:
        return rej
    ranked = np.sort(p[ok])
    thr = q * np.arange(1, m + 1) / m
    below = ranked <= thr
    if not below.any():
        return rej
    cut = ranked[np.max(np.where(below)[0])]
    rej[ok & (p <= cut)] = True
    return rej


def coverage(cell_slugs: set, dec_slugs: set) -> float:
    """Fraction of the cell's windows already covered by a decision set."""
    if not cell_slugs:
        return float("nan")
    return len(cell_slugs & dec_slugs) / len(cell_slugs)


# ==========================================================================
# data assembly
# ==========================================================================
def _load_slip() -> float:
    try:
        with open(FILL_PARAMS_PATH) as f:
            return float(json.load(f)["mean_slip_filled"])
    except Exception:
        return DEFAULT_SLIP


def atlas_tick_frame() -> pd.DataFrame:
    """Healthy ticks with the per-tick Chainlink view: cl_start (poll-asof at window
    open — the settlement strike basis, same convention as cl_outcomes), cl_dist_bps,
    and the Chainlink settlement label cl_up."""
    from research.analysis.edge_lab import cl_outcomes, load_base
    from research.analysis.oracle_print_model import load_polls
    from research.analysis.oracle_settlement_gap import asof_chainlink

    b = load_base()
    cols = ["slug", "symbol", "date", "split", "window_start_ts",
            "seconds_into_window", "time_left_sec", "yes_best_bid", "yes_best_ask",
            "yes_ask_depth", "no_ask_depth", "fav_side", "fav_ask", "consistent",
            "dist_strike_bps", "chainlink_price", "cl_cb_basis_bps", "realized_vol",
            "book_healthy"]
    t = b[cols]
    t = t[(t["book_healthy"] == True)  # noqa: E712
          & t["time_left_sec"].between(1, 900)].copy()
    t = t.drop_duplicates(["slug", "seconds_into_window"])
    t["symbol"] = t["symbol"].astype(str).str.lower()

    polls = load_polls()
    w = t[["slug", "symbol", "window_start_ts"]].drop_duplicates("slug").copy()
    w["t_start_ms"] = w["window_start_ts"].astype("int64") * 1000
    w = asof_chainlink(polls[["timestamp_ms", "symbol", "price"]], w,
                       "t_start_ms", "cl_start")
    t = t.merge(w[["slug", "cl_start"]], on="slug", how="left")
    clp = t["chainlink_price"].to_numpy("f8")
    cls = t["cl_start"].to_numpy("f8")
    with np.errstate(invalid="ignore", divide="ignore"):
        cld = np.where((clp > 0) & (cls > 0), (clp / cls - 1.0) * 1e4, np.nan)
    t["cl_dist_bps"] = cld
    t["abs_cl_dist"] = np.abs(cld)
    t = t.merge(cl_outcomes(), on="slug", how="left")
    return t


def build_obs(t: pd.DataFrame, slip: float) -> pd.DataFrame:
    """One observation per (slug x cell): the FIRST tick of the window landing in
    the cell, for both sides. Columns: slug/symbol/date/split, cell, entry_sec,
    ask, won, ret_gross, ret_net, depth_usd_side."""
    fav_yes = t["fav_side"].astype(str).eq("yes").to_numpy()
    tl_i = tl_bin(t["time_left_sec"].to_numpy("f8"))
    cld_i = cld_bin(t["abs_cl_dist"].to_numpy("f8"))
    cons = t["consistent"].to_numpy(bool)
    cl_up = t["cl_up"].to_numpy("f8")
    ya = t["yes_best_ask"].to_numpy("f8")
    yb = t["yes_best_bid"].to_numpy("f8")
    yd = t["yes_ask_depth"].to_numpy("f8")
    nd = t["no_ask_depth"].to_numpy("f8")

    parts = []
    for side_i, side in enumerate(SIDES):
        if side == "FAV":
            buy_yes = fav_yes
            ask = t["fav_ask"].to_numpy("f8")
            lo, hi = FAV_LO, FAV_HI
        else:
            buy_yes = ~fav_yes
            ask = np.where(fav_yes, 1.0 - yb, ya)
            lo, hi = CHEAP_LO, CHEAP_HI
        a_i = ask_bin(ask, lo, hi)
        keep = (a_i >= 0) & (tl_i >= 0) & np.isfinite(cl_up)
        if not keep.any():
            continue
        code = encode_cell(side_i, a_i[keep], tl_i[keep], cld_i[keep], cons[keep])
        won = np.where(buy_yes[keep], cl_up[keep] == 1, cl_up[keep] == 0).astype("f8")
        depth_sh = np.where(buy_yes[keep], yd[keep], nd[keep])
        sub = pd.DataFrame({
            "slug": t["slug"].to_numpy()[keep],
            "symbol": t["symbol"].to_numpy()[keep],
            "date": t["date"].to_numpy()[keep],
            "split": t["split"].to_numpy()[keep],
            "entry_sec": t["seconds_into_window"].to_numpy()[keep],
            "cell": code, "ask": ask[keep], "won": won,
            "depth_usd_side": depth_sh * ask[keep],
        })
        parts.append(sub)
    if not parts:
        return pd.DataFrame(columns=["slug", "symbol", "date", "split", "entry_sec",
                                     "cell", "ask", "won", "depth_usd_side",
                                     "ret_gross", "ret_net"])
    obs = pd.concat(parts, ignore_index=True)
    obs = (obs.sort_values(["slug", "cell", "entry_sec"], kind="mergesort")
           .drop_duplicates(["slug", "cell"], keep="first")
           .reset_index(drop=True))
    g, n = per_obs_returns(obs["won"], obs["ask"], slip)
    obs["ret_gross"], obs["ret_net"] = g, n
    return obs


# ==========================================================================
# cell statistics + gates
# ==========================================================================
def _split_agg(obs: pd.DataFrame, split: str, pre: str) -> pd.DataFrame:
    s = obs[obs["split"] == split]
    if s.empty:
        return pd.DataFrame(columns=["cell", f"n_{pre}", f"wr_{pre}",
                                     f"gross_{pre}", f"net_{pre}", f"ask_{pre}"])
    a = (s.groupby("cell")
         .agg(n=("won", "size"), wr=("won", "mean"), gross=("ret_gross", "mean"),
              net=("ret_net", "mean"), ask=("ask", "mean"))
         .reset_index())
    return a.rename(columns={c: f"{c}_{pre}" for c in ("n", "wr", "gross", "net", "ask")})


def cell_table(obs: pd.DataFrame) -> pd.DataFrame:
    """Per-cell dev/holdout stats + dev bootstrap p-values + dev CI for cells
    with n_dev >= N_DEV_CAND. No future numbers are touched here."""
    assert not obs.duplicated(["slug", "cell"]).any(), "one obs per (slug, cell)"
    dh = obs[obs["split"].isin(("dev", "holdout"))]
    cells = _split_agg(obs, "dev", "dev").merge(
        _split_agg(obs, "holdout", "hold"), on="cell", how="outer")
    cap = (dh.groupby("cell")["depth_usd_side"].median()
           .rename("depth_med_usd").reset_index())
    cells = cells.merge(cap, on="cell", how="left")
    for c in ("n_dev", "n_hold"):
        cells[c] = cells[c].fillna(0).astype(int)

    p_pos = np.full(len(cells), np.nan)
    p_neg = np.full(len(cells), np.nan)
    lo = np.full(len(cells), np.nan)
    hi = np.full(len(cells), np.nan)
    dev = obs[obs["split"] == "dev"]
    by_cell = dict(tuple(dev.groupby("cell")))
    for i, code in enumerate(cells["cell"].to_numpy()):
        g = by_cell.get(code)
        if g is None or len(g) < N_DEV_TEST:
            continue
        v = g["ret_net"].to_numpy("f8")
        p_pos[i], p_neg[i] = bootstrap_p_one_sided(v)
        if len(g) >= N_DEV_CAND:
            l5, _, h95 = clustered_ci(v, g["slug"].to_numpy())
            lo[i], hi[i] = l5, h95
    cells["p_pos_dev"], cells["p_neg_dev"] = p_pos, p_neg
    cells["dev_lo"], cells["dev_hi"] = lo, hi

    cells["bh_pos"] = benjamini_hochberg(cells["p_pos_dev"].to_numpy(), FDR_Q)
    cells["bh_neg"] = benjamini_hochberg(cells["p_neg_dev"].to_numpy(), FDR_Q)
    cells["cand_pos"] = ((cells["n_dev"] >= N_DEV_CAND) & (cells["dev_lo"] > 0)
                         & (cells["net_hold"] > 0) & cells["bh_pos"])
    cells["cand_neg"] = ((cells["n_dev"] >= N_DEV_CAND) & (cells["dev_hi"] < 0)
                         & (cells["net_hold"] < 0) & cells["bh_neg"])
    cells["label"] = [cell_label(c) for c in cells["cell"]]
    return cells


# ==========================================================================
# harvested overlay
# ==========================================================================
def fade_region_slugs(t: pd.DataFrame) -> set:
    """Slugs touched by the near-strike fade region: fav_ask >= 0.75 AND
    p_settle(fav) <= 0.60 AND time_left in [60, 360] (task definition)."""
    from research.analysis.oracle_print_model import load_polls, p_settle
    sub = t[(t["fav_ask"] >= FADE_FAV_MIN)
            & t["time_left_sec"].between(*FADE_TL)].copy()
    if sub.empty:
        return set()
    sub["cb_dist_bps"] = sub["dist_strike_bps"]
    sub["t_ms"] = ((sub["window_start_ts"].astype("int64")
                    + sub["seconds_into_window"].astype("int64")) * 1000)
    polls = load_polls()
    parts = []
    for sym, g in sub.groupby("symbol"):
        pr = (polls[(polls["symbol"] == sym) & polls["updated_at"].notna()]
              [["timestamp_ms", "updated_at"]].sort_values("timestamp_ms"))
        pr["timestamp_ms"] = pr["timestamp_ms"].astype("int64")
        m = pd.merge_asof(g.sort_values("t_ms"), pr, left_on="t_ms",
                          right_on="timestamp_ms", direction="backward",
                          tolerance=120_000)
        parts.append(m)
    sub = pd.concat(parts, ignore_index=True)
    sub["cl_oracle_age_s"] = np.clip(sub["t_ms"] / 1000.0 - sub["updated_at"], 0.0, None)
    p = p_settle(sub)
    fav_yes = sub["fav_side"].astype(str).eq("yes").to_numpy()
    p_fav = np.where(fav_yes, p, 1.0 - p)
    hit = np.isfinite(p_fav) & (p_fav <= FADE_P_MAX)
    return set(sub.loc[hit, "slug"])


def decision_slug_sets(t: pd.DataFrame) -> dict:
    """Slug sets of every existing decision set for the harvested overlay."""
    from research.analysis.edge_lab import load_base
    from research.analysis.rejudge_live_model import CONFIGS, _apply_dual_gate, decisions_for
    b = load_base()
    out = {}
    for name, cfg in CONFIGS.items():
        dec = decisions_for(name, b)
        if cfg.get("oracle_gate") == "agree" and not dec.empty:
            dec = _apply_dual_gate(dec, b)
        out[name] = set(dec["slug"]) if not dec.empty else set()
    out["near_strike_fade"] = fade_region_slugs(t)
    try:
        out["sq"] = set(pd.read_parquet(SQ_LEDGER, columns=["slug"])["slug"])
    except Exception:
        out["sq"] = set()
    return out


def overlay_for_cells(cells: pd.DataFrame, obs: pd.DataFrame,
                      dec_sets: dict, codes) -> pd.DataFrame:
    """Coverage of each decision set over each given cell's dev+holdout slugs.
    Coverage uses PRE-FUTURE windows only so the unharvested call is reveal-safe."""
    dh = obs[obs["split"].isin(("dev", "holdout"))]
    slug_by_cell = dh.groupby("cell")["slug"].agg(set).to_dict()
    rows = []
    for code in codes:
        cs = slug_by_cell.get(code, set())
        r = {"cell": code}
        for name, ds in dec_sets.items():
            r[f"cov_{name}"] = round(coverage(cs, ds), 3)
        covs = [v for k, v in r.items() if k.startswith("cov_")]
        r["cov_max"] = max(covs) if covs else float("nan")
        r["unharvested"] = bool(r["cov_max"] < HARVEST_COVER) if np.isfinite(r["cov_max"]) else True
        rows.append(r)
    return pd.DataFrame(rows)


# ==========================================================================
# build / reveal / print
# ==========================================================================
def _write_meta(meta: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)


def _read_meta() -> dict:
    with open(META_PATH) as f:
        return json.load(f)


def build() -> None:
    slip = _load_slip()
    print(f"building atlas (slip={slip:+.4f}, fees=0) ...")
    t = atlas_tick_frame()
    obs = build_obs(t, slip)
    n_label_miss = int(t["cl_up"].isna().groupby(t["slug"]).first().sum())
    print(f"  ticks {len(t):,}  obs {len(obs):,}  windows {obs['slug'].nunique():,} "
          f"(slugs without CL outcome dropped: {n_label_miss})")
    cells = cell_table(obs)
    dec_sets = decision_slug_sets(t)
    cand_codes = cells.loc[cells["cand_pos"] | cells["cand_neg"], "cell"].tolist()
    ov = overlay_for_cells(cells, obs, dec_sets, cand_codes)
    cells = cells.merge(ov, on="cell", how="left")

    fam_pos = int(np.isfinite(cells["p_pos_dev"]).sum())
    meta = dict(
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        slip=slip, fdr_q=FDR_Q, n_dev_test=N_DEV_TEST, n_dev_cand=N_DEV_CAND,
        n_fut_confirm=N_FUT_CONFIRM, ci=dict(n=CI_N, seed=CI_SEED),
        pval=dict(n=P_N, seed=P_SEED),
        n_ticks=int(len(t)), n_obs=int(len(obs)),
        n_obs_split=obs["split"].value_counts().to_dict(),
        n_cells=int(len(cells)), bh_family=fam_pos,
        n_cand_pos=int(cells["cand_pos"].sum()), n_cand_neg=int(cells["cand_neg"].sum()),
        decision_set_sizes={k: len(v) for k, v in dec_sets.items()},
        future_revealed=False,
    )
    os.makedirs(OUT_DIR, exist_ok=True)
    cells.to_parquet(CELLS_PATH, index=False)
    _write_meta(meta)
    print(f"  cells {len(cells):,} | BH family (n_dev>={N_DEV_TEST}) {fam_pos:,} "
          f"| candidates +{meta['n_cand_pos']} / -{meta['n_cand_neg']}")
    print(f"  -> {CELLS_PATH}")
    print_tables(cells, meta)


def reveal_future(force: bool = False) -> None:
    meta = _read_meta()
    if meta.get("future_revealed") and not force:
        raise SystemExit("future already revealed for this artifact — pass --force "
                         "to recompute (and say so in the writeup)")
    slip = float(meta["slip"])
    cells = pd.read_parquet(CELLS_PATH)
    t = atlas_tick_frame()
    obs = build_obs(t, slip)
    assert int(len(obs)) == int(meta["n_obs"]), "obs drifted since --build"

    cand = cells[cells["cand_pos"] | cells["cand_neg"]]
    print(f"revealing FUTURE for {len(cand)} candidate cells "
          f"(+{int(cells['cand_pos'].sum())} / -{int(cells['cand_neg'].sum())}) ...")
    fut = obs[obs["split"] == "future"]
    by_cell = dict(tuple(fut.groupby("cell")))
    rows = []
    for code in cand["cell"]:
        g = by_cell.get(code)
        if g is None or len(g) == 0:
            rows.append(dict(cell=code, n_fut=0))
            continue
        v = g["ret_net"].to_numpy("f8")
        l5, _, h95 = clustered_ci(v, g["slug"].to_numpy())
        per_coin = {sym: round(float(gg["ret_net"].mean()), 4)
                    for sym, gg in g.groupby("symbol")}
        rows.append(dict(cell=code, n_fut=int(len(g)),
                         wr_fut=float(g["won"].mean()),
                         gross_fut=float(g["ret_gross"].mean()),
                         net_fut=float(v.mean()), fut_lo=float(l5), fut_hi=float(h95),
                         per_coin_fut=json.dumps(per_coin)))
    fr = pd.DataFrame(rows)
    cells = cells.merge(fr, on="cell", how="left")
    cells["n_fut"] = cells["n_fut"].fillna(0).astype(int)
    cells["confirmed_pos"] = (cells["cand_pos"] & (cells["n_fut"] >= N_FUT_CONFIRM)
                              & (cells["net_fut"] > 0))
    cells["confirmed_neg"] = (cells["cand_neg"] & (cells["n_fut"] >= N_FUT_CONFIRM)
                              & (cells["net_fut"] < 0))
    cells["strong_pos"] = cells["confirmed_pos"] & (cells["fut_lo"] > 0)
    cells["strong_neg"] = cells["confirmed_neg"] & (cells["fut_hi"] < 0)
    cells.to_parquet(CELLS_PATH, index=False)
    meta.update(future_revealed=True,
                revealed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                n_confirmed_pos=int(cells["confirmed_pos"].sum()),
                n_confirmed_neg=int(cells["confirmed_neg"].sum()),
                n_strong_pos=int(cells["strong_pos"].sum()),
                n_strong_neg=int(cells["strong_neg"].sum()))
    _write_meta(meta)
    print(f"  confirmed +{meta['n_confirmed_pos']} (strong {meta['n_strong_pos']}) "
          f"/ -{meta['n_confirmed_neg']} (strong {meta['n_strong_neg']})")
    print_tables(cells, meta)


def _fmt_row(r, revealed: bool) -> str:
    s = (f"{r['label']:38s} n_dev={r['n_dev']:>4} net_dev={r['net_dev']*100:+6.1f}% "
         f"[{r['dev_lo']*100:+6.1f},{r['dev_hi']*100:+6.1f}] "
         f"hold={r['net_hold']*100:+6.1f}%(n={r['n_hold']:.0f})")
    if revealed and r.get("n_fut", 0) and np.isfinite(r.get("net_fut", np.nan)):
        s += (f" | FUT n={r['n_fut']:.0f} net={r['net_fut']*100:+6.1f}% "
              f"[{r['fut_lo']*100:+6.1f},{r['fut_hi']*100:+6.1f}] WR={r['wr_fut']*100:.0f}%")
    if "cov_max" in r and np.isfinite(r.get("cov_max", np.nan)):
        s += f" | cov_max={r['cov_max']:.2f}" + (" UNHARVESTED" if r.get("unharvested") else "")
    return s


def print_tables(cells: pd.DataFrame | None = None, meta: dict | None = None) -> None:
    if cells is None:
        cells = pd.read_parquet(CELLS_PATH)
        meta = _read_meta()
    revealed = bool(meta.get("future_revealed"))
    for name, mask in (("POSITIVE candidates", cells.get("cand_pos")),
                       ("NEGATIVE candidates (fade/avoid)", cells.get("cand_neg"))):
        sub = cells[mask] if mask is not None else cells.iloc[0:0]
        sort_col = "net_dev" if "net_dev" in sub.columns else None
        sub = sub.sort_values(sort_col, ascending=(name.startswith("NEG")))
        print(f"\n=== {name} ({len(sub)}) ===")
        for _, r in sub.iterrows():
            print(_fmt_row(r, revealed))
    big = cells[(cells["n_dev"] + cells["n_hold"]) >= 60].copy()
    big["net_dh"] = (big["net_dev"].fillna(0) * big["n_dev"]
                     + big["net_hold"].fillna(0) * big["n_hold"]) / (big["n_dev"] + big["n_hold"])
    big = big.reindex(big["net_dh"].abs().sort_values(ascending=False).index)
    print(f"\n=== largest |net| cells, dev+holdout pooled (n>=60; top 40 of {len(big)}) ===")
    for _, r in big.head(40).iterrows():
        print(f"{r['label']:38s} n={r['n_dev']+r['n_hold']:>5} net_dh={r['net_dh']*100:+6.1f}% "
              f"wr_dev={r['wr_dev']*100 if np.isfinite(r['wr_dev']) else float('nan'):5.1f}% "
              f"depth=${r['depth_med_usd']:,.0f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", action="store_true", help="dev+holdout atlas + gates + overlay")
    ap.add_argument("--reveal-future", action="store_true",
                    help="future column for FDR-surviving candidates only (once)")
    ap.add_argument("--print", dest="print_", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.build:
        build()
    elif args.reveal_future:
        reveal_future(force=args.force)
    elif args.print_:
        print_tables()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
