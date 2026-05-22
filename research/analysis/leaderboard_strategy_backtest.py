"""Phase 3 — backtest the leaderboard directional-holder rule (M15-DH-1).

The wallet analysis (`docs/research/leaderboard_wallets.md`) found that the
dominant winning pattern on Polymarket's crypto profit leaderboard is NOT
market-making — it is **directional buy-and-hold**: 70% of leaderboard winners
buy one side of a short-dated crypto Up/Down market and hold it to resolution.
From their trades a concrete rule was derived:

  Rule M15-DH-1 (directional-holder, favourite leg)
    On a crypto Up/Down market, within the early-window entry zone, BUY the
    FAVOURITE side (the side with mid > 0.50) when its odds are in an entry
    band; HOLD to resolution; settle at the true 0/1 outcome. Flat sizing, one
    entry per window.

WHY THIS TASK EXISTS — the leaderboard is survivors only (top-100 winners of
each board). It tells us what winners *do*; it cannot measure expectancy
because it never sees the losers. Our own tick data sees ALL markets — winners
and losers — so it can measure the true expectancy of the rule. This is the
decisive empirical test of the leaderboard hypothesis.

PRIOR RESEARCH (`docs/research/PHASE2_RERUN_VERDICT.md`): on corrected labels
the 15m cheap side is well-calibrated (de-biased gap ~-1c). The favourite side
is the *complement* of the cheap side, so a calibrated cheap side implies a
calibrated favourite side — i.e. buy-favourite-and-hold should be gross
breakeven on 15m and lose to cost. This backtest either confirms that cleanly
or finds where it breaks. 5m is genuinely untested (prior research is 15m only).

DATA / LABELS
  15m: `data/research/ticks_15m.parquet`. Its baked-in `outcome_up` column
       matches `corrected_labels.parquet` (the authoritative Polymarket API
       ground truth) for 100% of windows — verified — so 15m uses it directly.
  5m:  `data/research/ticks_5m.parquet`. Its baked-in `outcome_up` is CORRUPT
       (~31% wrong vs the corrected labels — verified). The corrected 5m
       outcomes live in `data/research/corrected_labels_5m.parquet`, fetched
       from the gamma API; that cache only covers the DEV split (May 15-20).
       So the 5m backtest uses `corrected_labels_5m.parquet` and is DEV-only
       unless the hold-out labels are fetched on demand (see `--fetch-holdout`).

DISCIPLINE (mirrors PHASE2_RERUN_VERDICT.md)
  - All exploration / bucket inspection / band selection on the DEV split only
    (May 15-20). Asserted: no hold-out row enters any dev computation.
  - The sealed HOLD-OUT (May 21-22) is opened exactly ONCE, and only for a
    configuration that is CI-positive on DEV. If nothing is CI-positive on
    DEV, the hold-out stays sealed.
  - All CIs are 90% window-clustered bootstraps (the window is the unit).

Run:  uv run python -m research.analysis.leaderboard_strategy_backtest
      uv run python -m research.analysis.leaderboard_strategy_backtest --fetch-holdout-5m
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.holdout import (  # noqa: E402
    DEV_START, DEV_END, HOLDOUT_START, HOLDOUT_END,
)
from research.lib.stats import window_clustered_bootstrap  # noqa: E402

DATA = REPO / "data" / "research"
TICKS_15M = DATA / "ticks_15m.parquet"
TICKS_5M = DATA / "ticks_5m.parquet"
CL_5M = DATA / "corrected_labels_5m.parquet"
DOC = REPO / "docs" / "research" / "leaderboard_strategy_backtest.md"

SEED = 42
N_BOOT = 3000
STAKE = 10.0  # dollars per trade (flat sizing)

# Entry-window geometry. The leaderboard rule enters in the first third of the
# window. 15m = 900s -> first 300s; 5m = 300s -> first 100s (proportional).
WINDOW_LEN = {"15m": 900, "5m": 300}
ENTRY_ZONE = {"15m": 300, "5m": 100}

# Taker fee: per share, per leg (Phase 0 / divergence_backtest convention).
def fee(p: float) -> float:
    """Polymarket-style per-share trading fee at price ``p``."""
    return 0.07 * p * (1.0 - p)


# Calibration sweep buckets over the favourite side's odds [0.50, 1.00].
SWEEP_EDGES = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00]

# The leaderboard band (primary) and the sensitivity variant.
PRIMARY_BAND = (0.60, 0.90)
SENSITIVITY_BAND = (0.50, 0.95)

SYMBOLS = ["btc", "eth", "sol", "xrp"]


# ===========================================================================
# Pure pieces — favourite identification, entry-tick selection, settlement
# ===========================================================================

def favourite_side(yes_mid: float, no_mid: float) -> str | None:
    """Return the FAVOURITE side ('YES' or 'NO') = the side with mid > 0.50.

    Returns None on an exact 50/50 tie (no favourite). The favourite is the YES
    side iff yes_mid > no_mid (equivalently yes_mid > 0.50 on a complementary
    book).
    """
    if yes_mid > no_mid:
        return "YES"
    if no_mid > yes_mid:
        return "NO"
    return None


def settle(side: str, outcome_up: float) -> int:
    """Resolve a held position to its 0/1 payout per share.

    A YES position pays 1 iff the market resolved Up (outcome_up == 1); a NO
    position pays 1 iff it resolved Down.
    """
    if side == "YES":
        return int(outcome_up == 1)
    if side == "NO":
        return int(outcome_up == 0)
    raise ValueError(f"bad side {side!r}")


def select_entry_tick(window: pd.DataFrame, timeframe: str,
                      band_lo: float, band_hi: float):
    """Pick the first qualifying entry tick of one window, or None.

    A tick qualifies when (a) it is inside the early-entry zone for the
    timeframe (``seconds_into_window <= ENTRY_ZONE[timeframe]``), and (b) the
    FAVOURITE side's mid falls in ``[band_lo, band_hi]``. The favourite is
    identified at that tick from ``yes_mid`` / ``no_mid``. The earliest such
    tick (smallest ``seconds_into_window``) is returned.

    Returns a dict with the entry tick's fields, or None if no tick qualifies.
    The window frame must be sorted by ``seconds_into_window``.
    """
    zone = ENTRY_ZONE[timeframe]
    w = window[window["seconds_into_window"] <= zone]
    for _, row in w.iterrows():
        ym, nm = float(row["yes_mid"]), float(row["no_mid"])
        fav = favourite_side(ym, nm)
        if fav is None:
            continue
        fav_mid = ym if fav == "YES" else nm
        if not (band_lo <= fav_mid <= band_hi):
            continue
        if fav == "YES":
            fav_ask, fav_bid = float(row["yes_best_ask"]), float(row["yes_best_bid"])
        else:
            fav_ask, fav_bid = float(row["no_best_ask"]), float(row["no_best_bid"])
        return {
            "side": fav, "fav_mid": fav_mid, "fav_ask": fav_ask,
            "fav_bid": fav_bid, "seconds_into_window": float(row["seconds_into_window"]),
            "_idx": int(row.name),
        }
    return None


def taker_pnl(entry_ask: float, side: str, outcome_up: float) -> float:
    """Net PnL of buying the favourite at its ASK and holding to resolution.

    Flat $STAKE notional. Cross the spread (pay the ask), pay an entry fee
    ``fee(ask)`` per share; there is no exit fee — the position is held to
    resolution and settles at the true 0/1 outcome (a fee at p in {0,1} is 0).
    """
    shares = STAKE / entry_ask
    entry_fee = fee(entry_ask) * shares
    payout = settle(side, outcome_up) * shares
    return payout - STAKE - entry_fee


# ===========================================================================
# Maker fill model — reused from maker_reframe.py's realistic logic
# ===========================================================================

def maker_trade(window: pd.DataFrame, entry: dict, side: str,
                outcome_up: float) -> dict:
    """Realistic maker: post a resting BUY limit at the favourite's BID at the
    entry tick; it fills ONLY when a strictly-later tick in the window shows
    that side's best bid <= the limit (a genuine later trade-through — someone
    sold into the level). If the market never reaches the limit the order does
    NOT fill: that trade does not happen (filled=False, pnl=0). On a fill the
    position is held to resolution and settles at the true outcome. 0 fee
    (makers pay no fee).

    This is the same fill discipline as `maker_reframe.run_maker_backtest` — a
    resting order is never assumed to fill; it fills only on a real later
    trade-through, so the fill is adverse-selected by construction.
    """
    limit = entry["fav_bid"]
    bid_col = "yes_best_bid" if side == "YES" else "no_best_bid"
    later = window[window["seconds_into_window"] > entry["seconds_into_window"]]
    if not (1e-6 < limit < 0.999) or later.empty:
        return {"filled": False, "pnl": 0.0, "entry": np.nan}
    bidpath = later[bid_col].to_numpy(dtype="f8")
    hit = np.where(bidpath <= limit)[0]
    if not len(hit):
        return {"filled": False, "pnl": 0.0, "entry": np.nan}
    shares = STAKE / limit  # bought at the limit, 0 fee
    payout = settle(side, outcome_up) * shares
    return {"filled": True, "pnl": payout - STAKE, "entry": limit}


# ===========================================================================
# Data loading
# ===========================================================================

_BOOK_COLS = ["slug", "symbol", "window_start_ts", "seconds_into_window",
              "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask",
              "yes_mid", "no_mid", "outcome_up"]


def _healthy(t: pd.DataFrame) -> pd.Series:
    """Hard book-health guard (same as maker_reframe / final_sweep): both sides
    priced in (0.001, 0.999), positive bids strictly below asks, and the book
    complement-consistent (yes_ask + no_bid ~ 1 within 6c). Decided-market
    books — where a resolved side collapses to ~0/~1 — fail this guard."""
    ya, yb = t["yes_best_ask"], t["yes_best_bid"]
    na, nb = t["no_best_ask"], t["no_best_bid"]
    return (
        (ya > 0.001) & (ya < 0.999) & (na > 0.001) & (na < 0.999)
        & (yb > 0) & (nb > 0) & (yb < ya) & (nb < na)
        & ((ya + nb - 1.0).abs() < 0.06) & ((na + yb - 1.0).abs() < 0.06)
    )


def load_book(timeframe: str, split: str,
              labels_5m: pd.DataFrame | None = None) -> pd.DataFrame:
    """Load the tick book for one timeframe and split ('dev' or 'holdout').

    For 15m the parquet's baked-in ``outcome_up`` is authoritative (verified
    100% match to `corrected_labels.parquet`). For 5m the baked-in column is
    corrupt; the corrected outcome is joined from ``labels_5m`` (the
    `corrected_labels_5m.parquet` cache or a freshly fetched extension) and
    windows with no corrected label are dropped.

    The healthy-book guard is applied; the early-entry zone is what the
    backtest reads, so only ticks with seconds_into_window <= entry-zone are
    kept (the maker fill model still needs later ticks — see note).
    """
    path = TICKS_15M if timeframe == "15m" else TICKS_5M
    t = pd.read_parquet(path, columns=_BOOK_COLS)
    t = t[t["window_start_ts"].notna()].copy()
    t["date"] = pd.to_datetime(
        t["window_start_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    if split == "dev":
        t = t[(t["date"] >= DEV_START) & (t["date"] <= DEV_END)].copy()
    elif split == "holdout":
        t = t[(t["date"] >= HOLDOUT_START) & (t["date"] <= HOLDOUT_END)].copy()
    else:
        raise ValueError(f"bad split {split!r}")

    if timeframe == "5m":
        if labels_5m is None or labels_5m.empty:
            return pd.DataFrame(columns=list(t.columns))
        lab = labels_5m[["slug", "outcome_up_5m"]].dropna()
        t = t.drop(columns=["outcome_up"]).merge(lab, on="slug", how="inner")
        t = t.rename(columns={"outcome_up_5m": "outcome_up"})

    t = t[_healthy(t)].copy()
    t = t.sort_values(["slug", "seconds_into_window"], kind="mergesort")
    return t.reset_index(drop=True)


def load_5m_labels(split: str = "dev", fetch: bool = False) -> pd.DataFrame:
    """Return corrected 5m outcomes for a split.

    The cached `corrected_labels_5m.parquet` covers DEV only (it was fetched
    for dev windows in Hypothesis G of `final_sweep.py`). For the hold-out,
    ``fetch=True`` pulls the resolved outcomes from the gamma API on demand
    (reusing `final_sweep._fetch_5m`) — done ONLY when a DEV result warrants
    opening the hold-out.
    """
    cache = pd.read_parquet(CL_5M) if CL_5M.exists() else pd.DataFrame(
        columns=["slug", "outcome_up_5m"])
    cache["wst"] = cache["slug"].str.extract(r"-5m-(\d+)").astype("float64")
    cache["date"] = pd.to_datetime(
        cache["wst"], unit="s", utc=True).dt.strftime("%Y-%m-%d")

    if split == "dev":
        return cache[(cache["date"] >= DEV_START)
                     & (cache["date"] <= DEV_END)].copy()

    # split == 'holdout'
    have = cache[(cache["date"] >= HOLDOUT_START)
                 & (cache["date"] <= HOLDOUT_END)].copy()
    if not have.empty or not fetch:
        return have
    # Fetch the hold-out 5m outcomes on demand.
    import asyncio
    from research.analysis.final_sweep import _fetch_5m  # noqa
    t5 = pd.read_parquet(TICKS_5M, columns=["slug", "window_start_ts"])
    t5 = t5[t5["window_start_ts"].notna()].copy()
    t5["date"] = pd.to_datetime(
        t5["window_start_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    ho_slugs = sorted(t5[(t5["date"] >= HOLDOUT_START)
                         & (t5["date"] <= HOLDOUT_END)]["slug"].unique())
    print(f"  fetching {len(ho_slugs)} hold-out 5m outcomes from gamma API ...")
    got = asyncio.run(_fetch_5m(ho_slugs))
    fetched = pd.DataFrame(
        {"slug": list(got.keys()), "outcome_up_5m": list(got.values())})
    # Append to the cache so re-runs are instant.
    merged = pd.concat([pd.read_parquet(CL_5M), fetched], ignore_index=True)
    merged = merged.drop_duplicates("slug")
    merged.to_parquet(CL_5M, index=False)
    print(f"  fetched {len(fetched)} hold-out 5m outcomes -> cached.")
    return fetched


# ===========================================================================
# Per-window backtest
# ===========================================================================

def backtest_band(book: pd.DataFrame, timeframe: str,
                   band_lo: float, band_hi: float,
                   random_side: bool = False, seed: int = SEED) -> pd.DataFrame:
    """One trade per window: enter at the first qualifying entry tick, record
    taker and maker PnL.

    With ``random_side=True`` the rule is the null baseline — at the SAME entry
    tick (selected exactly as the favourite rule selects it) the trade buys a
    side picked uniformly at random rather than the favourite. The entry-tick
    selection still uses the favourite-mid band so the two rules trade the same
    windows at the same ticks; only the chosen side differs. This isolates the
    value of the *favourite* choice from the value of the *timing*.

    Returns one row per traded window: slug, symbol, date, side, entry odds,
    realized win (0/1), taker net PnL, maker fill flag, maker net PnL.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for slug, w in book.groupby("slug", sort=True):
        w = w.sort_values("seconds_into_window")
        entry = select_entry_tick(w, timeframe, band_lo, band_hi)
        if entry is None:
            continue
        outcome_up = float(w["outcome_up"].iloc[0])
        symbol = str(w["symbol"].iloc[0])
        date = str(w["date"].iloc[0])

        if random_side:
            side = "YES" if rng.random() < 0.5 else "NO"
            # entry odds for the chosen side at the entry tick
            erow = w.loc[entry["_idx"]]
            if side == "YES":
                e_ask, e_bid = float(erow["yes_best_ask"]), float(erow["yes_best_bid"])
                e_mid = float(erow["yes_mid"])
            else:
                e_ask, e_bid = float(erow["no_best_ask"]), float(erow["no_best_bid"])
                e_mid = float(erow["no_mid"])
            entry = {**entry, "side": side, "fav_ask": e_ask,
                     "fav_bid": e_bid, "fav_mid": e_mid}
        else:
            side = entry["side"]

        if not (1e-6 < entry["fav_ask"] < 0.999):
            continue
        won = settle(side, outcome_up)
        tk = taker_pnl(entry["fav_ask"], side, outcome_up)
        mk = maker_trade(w, entry, side, outcome_up)
        rows.append({
            "slug": slug, "symbol": symbol, "date": date, "side": side,
            "entry_mid": entry["fav_mid"], "entry_ask": entry["fav_ask"],
            "entry_sec": entry["seconds_into_window"], "won": won,
            "taker_pnl": tk, "maker_filled": mk["filled"], "maker_pnl": mk["pnl"],
        })
    return pd.DataFrame(rows)


def _ci(values: np.ndarray, groups: np.ndarray):
    """90% window-clustered bootstrap (lo, mid, hi) of the mean; None if n<10."""
    if len(values) < 10:
        return None
    return window_clustered_bootstrap(values, groups, n=N_BOOT, seed=SEED)


def summarize(trades: pd.DataFrame, label: str) -> dict:
    """Net PnL/trade for taker and maker, with 90% window-clustered CIs.

    Maker stats are over FILLED maker trades only — an unfilled maker order is
    no trade. The taker always trades (it crosses the spread on entry)."""
    n = len(trades)
    if n == 0:
        return {"label": label, "n": 0}
    tk = trades["taker_pnl"].to_numpy(dtype="f8")
    tk_ci = _ci(tk, trades["slug"].to_numpy())
    mk_f = trades[trades["maker_filled"]]
    mk_ci = (_ci(mk_f["maker_pnl"].to_numpy(dtype="f8"),
                 mk_f["slug"].to_numpy()) if len(mk_f) else None)
    return {
        "label": label, "n": n, "n_win": int(trades["slug"].nunique()),
        "win_rate": float(trades["won"].mean()),
        "mean_entry": float(trades["entry_ask"].mean()),
        "taker_pnl": float(tk.mean()), "taker_ci": tk_ci,
        "maker_n_filled": int(len(mk_f)),
        "maker_fill_rate": float(len(mk_f) / n),
        "maker_pnl": float(mk_f["maker_pnl"].mean()) if len(mk_f) else float("nan"),
        "maker_ci": mk_ci,
    }


# ===========================================================================
# Calibration sweep
# ===========================================================================

def calibration_sweep(book: pd.DataFrame, timeframe: str) -> list[dict]:
    """Sweep the favourite entry-odds buckets. For each bucket, take the first
    qualifying entry tick of each window and record entry odds + outcome.

    Per bucket: realized win rate vs mean entry odds (calibration), and net
    PnL/trade taker and maker — all with 90% window-clustered CIs. One entry
    per window per bucket; the window is the independent unit.
    """
    rows = []
    for i in range(len(SWEEP_EDGES) - 1):
        lo, hi = SWEEP_EDGES[i], SWEEP_EDGES[i + 1]
        tr = backtest_band(book, timeframe, lo, hi)
        if tr.empty:
            rows.append({"lo": lo, "hi": hi, "n": 0})
            continue
        s = summarize(tr, f"[{lo:.2f},{hi:.2f})")
        s["lo"], s["hi"] = lo, hi
        # realized win rate CI (window-clustered)
        won_ci = _ci(tr["won"].to_numpy(dtype="f8"), tr["slug"].to_numpy())
        s["win_ci"] = won_ci
        s["mean_entry_mid"] = float(tr["entry_mid"].mean())
        rows.append(s)
    return rows


# ===========================================================================
# Orchestration
# ===========================================================================

def _band_block(book: pd.DataFrame, timeframe: str, band: tuple,
                 split_name: str) -> dict:
    """Run the favourite rule + the random-side null for one band, pooled and
    per-symbol. Returns a dict of summaries."""
    lo, hi = band
    fav = backtest_band(book, timeframe, lo, hi, random_side=False)
    nul = backtest_band(book, timeframe, lo, hi, random_side=True)
    out = {
        "band": band, "split": split_name,
        "pooled_fav": summarize(fav, "pooled favourite"),
        "pooled_null": summarize(nul, "pooled random-side"),
        "per_symbol": {},
        "trades": fav,
    }
    for sym in SYMBOLS:
        sub = fav[fav["symbol"] == sym]
        out["per_symbol"][sym] = summarize(sub, sym)
    return out


def run(fetch_holdout_5m: bool = False) -> dict:
    print("=" * 74)
    print("Phase 3 — Leaderboard directional-holder backtest (M15-DH-1)")
    print(f"  DEV {DEV_START}..{DEV_END}; HOLD-OUT {HOLDOUT_START}.."
          f"{HOLDOUT_END} SEALED.")
    print("=" * 74)

    result: dict = {"timeframes": {}}

    for tf in ("15m", "5m"):
        print(f"\n{'='*30} {tf} markets {'='*30}")
        labels_5m_dev = load_5m_labels("dev") if tf == "5m" else None
        dev = load_book(tf, "dev", labels_5m_dev)
        # --- discipline guard: no hold-out row in dev ---
        assert (dev["date"] >= DEV_START).all() and (dev["date"] <= DEV_END).all(), \
            f"{tf}: hold-out leaked into dev!"
        n_dev_win = dev["slug"].nunique()
        n_dev_days = dev["date"].nunique()
        print(f"  {tf} dev: {len(dev):,} healthy ticks, {n_dev_win:,} windows, "
              f"{n_dev_days} days")
        if tf == "5m":
            print(f"  (5m uses corrected_labels_5m.parquet — the parquet's "
                  f"baked-in outcome_up is corrupt and is NOT used.)")

        # --- calibration sweep (DEV only) ---
        print(f"\n  Calibration sweep — favourite entry-odds buckets ({tf}, DEV):")
        sweep = calibration_sweep(dev, tf)
        for s in sweep:
            if s.get("n", 0) == 0:
                print(f"    [{s['lo']:.2f},{s['hi']:.2f}): no trades")
                continue
            tkci = s["taker_ci"]
            print(f"    [{s['lo']:.2f},{s['hi']:.2f}) n={s['n']:4d} "
                  f"WR={s['win_rate']:.3f} entry_ask={s['mean_entry']:.3f} "
                  f"taker={s['taker_pnl']:+.3f} "
                  f"CI[{tkci[0]:+.2f},{tkci[2]:+.2f}] "
                  f"maker={s['maker_pnl']:+.3f}(fill {s['maker_fill_rate']:.2f})")

        # --- the two specific bands on DEV ---
        primary = _band_block(dev, tf, PRIMARY_BAND, "DEV")
        sensitivity = _band_block(dev, tf, SENSITIVITY_BAND, "DEV")
        for blk, name in ((primary, "PRIMARY [0.60,0.90]"),
                          (sensitivity, "SENSITIVITY [0.50,0.95]")):
            pf, pn = blk["pooled_fav"], blk["pooled_null"]
            print(f"\n  {tf} {name} — DEV pooled:")
            if pf.get("n", 0) == 0:
                print("    no trades")
                continue
            print(f"    favourite : n={pf['n']:4d} WR={pf['win_rate']:.3f} "
                  f"taker={pf['taker_pnl']:+.4f} "
                  f"CI[{pf['taker_ci'][0]:+.3f},{pf['taker_ci'][2]:+.3f}]  "
                  f"maker={pf['maker_pnl']:+.4f} "
                  f"CI[{pf['maker_ci'][0]:+.3f},{pf['maker_ci'][2]:+.3f}]"
                  if pf.get("maker_ci") else
                  f"    favourite : n={pf['n']:4d} WR={pf['win_rate']:.3f} "
                  f"taker={pf['taker_pnl']:+.4f}")
            print(f"    random    : n={pn['n']:4d} WR={pn['win_rate']:.3f} "
                  f"taker={pn['taker_pnl']:+.4f}")

        # --- decide whether the hold-out is warranted ---
        warrant = _ci_positive_configs(primary, sensitivity)
        if warrant:
            print(f"\n  {tf}: CI-positive DEV config(s): {warrant} "
                  f"-> hold-out WARRANTED.")
        else:
            print(f"\n  {tf}: no CI-positive DEV config -> hold-out NOT "
                  f"warranted; it stays sealed.")

        tf_res = {
            "n_dev_win": n_dev_win, "n_dev_days": n_dev_days,
            "sweep": sweep, "primary": primary, "sensitivity": sensitivity,
            "warrant": warrant, "holdout": None,
        }

        # --- hold-out, ONLY if warranted ---
        if warrant:
            if tf == "5m":
                labels_5m_ho = load_5m_labels("holdout", fetch=fetch_holdout_5m)
                if labels_5m_ho.empty:
                    print(f"  {tf}: hold-out warranted but corrected 5m "
                          f"hold-out labels are unavailable "
                          f"(run with --fetch-holdout-5m). Hold-out deferred.")
                    tf_res["holdout"] = {"deferred": True}
                else:
                    ho = load_book(tf, "holdout", labels_5m_ho)
                    tf_res["holdout"] = _run_holdout(ho, tf, warrant)
            else:
                ho = load_book(tf, "holdout")
                tf_res["holdout"] = _run_holdout(ho, tf, warrant)

        result["timeframes"][tf] = tf_res

    _write_doc(result)
    print(f"\nDoc written: {DOC}")
    print("=" * 74)
    return result


def _ci_positive_configs(primary: dict, sensitivity: dict) -> list[str]:
    """Return the names of band/execution configs whose pooled DEV net PnL CI
    excludes zero on the positive side."""
    out = []
    for blk, bname in ((primary, "[0.60,0.90]"),
                       (sensitivity, "[0.50,0.95]")):
        pf = blk["pooled_fav"]
        if pf.get("n", 0) == 0:
            continue
        if pf.get("taker_ci") and pf["taker_ci"][0] > 0:
            out.append(f"{bname} taker")
        if pf.get("maker_ci") and pf["maker_ci"][0] > 0:
            out.append(f"{bname} maker")
    return out


def _run_holdout(ho: pd.DataFrame, tf: str, warrant: list[str]) -> dict:
    """Open the sealed hold-out ONCE — only for the warranted band(s)."""
    print(f"\n  {tf}: opening sealed hold-out for {warrant} ...")
    assert (ho["date"] >= HOLDOUT_START).all() and (ho["date"] <= HOLDOUT_END).all()
    blocks = {}
    for band, bname in ((PRIMARY_BAND, "[0.60,0.90]"),
                        (SENSITIVITY_BAND, "[0.50,0.95]")):
        if not any(bname in w for w in warrant):
            continue
        blk = _band_block(ho, tf, band, "HOLDOUT")
        pf = blk["pooled_fav"]
        print(f"    {tf} {bname} HOLDOUT favourite: n={pf['n']} "
              f"taker={pf.get('taker_pnl', float('nan')):+.4f} "
              f"maker={pf.get('maker_pnl', float('nan')):+.4f}")
        blocks[bname] = blk
    return blocks


# ===========================================================================
# Doc rendering
# ===========================================================================

def _fmt_ci(ci, money=False):
    if ci is None:
        return "n/a"
    pre = "$" if money else ""
    return f"[{pre}{ci[0]:+.3f}, {pre}{ci[2]:+.3f}]"


def _sweep_table(sweep: list[dict]) -> str:
    h = ("| Fav entry-odds bucket | n windows | mean entry odds (ask) | "
         "realized win rate | win-rate 90% CI | net PnL/trade taker | taker CI | "
         "net PnL/trade maker | maker fill rate |\n"
         "|---|---|---|---|---|---|---|---|---|\n")
    out = []
    for s in sweep:
        if s.get("n", 0) == 0:
            out.append(f"| [{s['lo']:.2f},{s['hi']:.2f}) | 0 | - | - | - | - "
                       f"| - | - | - |")
            continue
        out.append(
            f"| [{s['lo']:.2f},{s['hi']:.2f}) | {s['n']:,} "
            f"| {s['mean_entry']:.3f} | {s['win_rate']:.3f} "
            f"| {_fmt_ci(s['win_ci'])} | ${s['taker_pnl']:+.4f} "
            f"| {_fmt_ci(s['taker_ci'], money=True)} "
            f"| ${s['maker_pnl']:+.4f} | {s['maker_fill_rate']:.2f} |")
    return h + "\n".join(out)


def _band_table(blk: dict) -> str:
    """Pooled + per-symbol + random-null table for one band block."""
    h = ("| Scope | n windows | win rate | net PnL/trade taker | taker 90% CI "
         "| net PnL/trade maker | maker 90% CI | maker fill rate |\n"
         "|---|---|---|---|---|---|---|---|\n")
    out = []

    def _row(scope, s):
        if s.get("n", 0) == 0:
            return f"| {scope} | 0 | - | - | - | - | - | - |"
        mk = (f"${s['maker_pnl']:+.4f}" if not np.isnan(s.get("maker_pnl", np.nan))
              else "n/a")
        return (f"| {scope} | {s['n']:,} | {s['win_rate']:.3f} "
                f"| ${s['taker_pnl']:+.4f} | {_fmt_ci(s.get('taker_ci'), money=True)} "
                f"| {mk} | {_fmt_ci(s.get('maker_ci'), money=True)} "
                f"| {s.get('maker_fill_rate', 0):.2f} |")

    out.append(_row("**pooled (favourite)**", blk["pooled_fav"]))
    for sym in SYMBOLS:
        out.append(_row(f"&nbsp;&nbsp;{sym}", blk["per_symbol"][sym]))
    out.append(_row("**pooled (random-side null)**", blk["pooled_null"]))
    return h + "\n".join(out)


def _verdict_line(blk: dict, tf: str, bname: str) -> str:
    pf, pn = blk["pooled_fav"], blk["pooled_null"]
    if pf.get("n", 0) == 0:
        return f"- **{tf} {bname}:** no trades.\n"
    tk, tkci = pf["taker_pnl"], pf.get("taker_ci")
    mk, mkci = pf.get("maker_pnl", float("nan")), pf.get("maker_ci")
    edge_vs_null = tk - pn["taker_pnl"]
    tk_sig = ("CI-positive" if tkci and tkci[0] > 0 else
              "CI-negative" if tkci and tkci[2] < 0 else "CI straddles 0")
    mk_sig = ("CI-positive" if mkci and mkci[0] > 0 else
              "CI-negative" if mkci and mkci[2] < 0 else
              "CI straddles 0" if mkci else "n/a")
    return (
        f"- **{tf} {bname}:** taker ${tk:+.4f}/trade ({tk_sig}, "
        f"CI {_fmt_ci(tkci, money=True)}); maker ${mk:+.4f}/trade ({mk_sig}, "
        f"CI {_fmt_ci(mkci, money=True)}); favourite minus random-side null "
        f"${edge_vs_null:+.4f}/trade.\n")


def _write_doc(r: dict) -> None:
    L: list[str] = []
    L.append("# Leaderboard directional-holder strategy — backtest "
             "(rule M15-DH-1)")
    L.append("")
    L.append("**Date:** 2026-05-22  ")
    L.append("**Code:** `research/analysis/leaderboard_strategy_backtest.py`  ")
    L.append("**Tests:** `tests/research/test_leaderboard_strategy_backtest.py`")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## What this tests")
    L.append("")
    L.append(
        "The wallet analysis (`docs/research/leaderboard_wallets.md`) found "
        "that the dominant winning pattern on Polymarket's crypto profit "
        "leaderboard is **not** market-making — it is **directional "
        "buy-and-hold**: 70% of leaderboard winners buy one side of a "
        "short-dated crypto Up/Down market and hold it to resolution. The "
        "derived rule:")
    L.append("")
    L.append(
        "> **M15-DH-1.** On a crypto Up/Down market, within the early-window "
        "entry zone, BUY the FAVOURITE side (mid > 0.50) when its odds are in "
        "an entry band; HOLD to resolution; settle at the true 0/1 outcome. "
        "Flat sizing, one entry per window.")
    L.append("")
    L.append(
        "The leaderboard is **survivors only** — the top-100 winners of each "
        "board. It shows what winners *do*; it cannot measure expectancy "
        "because it never sees the losers. Our tick data sees **all** markets "
        "— winners and losers — so it measures the rule's true expectancy. "
        "This is the decisive test.")
    L.append("")
    L.append(
        "**Prior research** (`PHASE2_RERUN_VERDICT.md`): on corrected labels "
        "the 15m cheap side is well-calibrated (de-biased gap ~-1c). The "
        "favourite side is the *complement* of the cheap side — so a "
        "calibrated cheap side implies a calibrated favourite side, i.e. "
        "buy-favourite-hold should be roughly gross-breakeven on 15m and lose "
        "to cost. 5m is genuinely untested (prior research is 15m only).")
    L.append("")
    L.append("## Method")
    L.append("")
    L.append(
        "- **Both timeframes, separately.** 15m window = 900s, early-entry "
        "zone = first 300s. 5m window = 300s, early-entry zone = first 100s "
        "(proportional — the leaderboard rule enters in the first third).")
    L.append(
        "- **Entry tick:** the first tick in the early-entry zone where the "
        "favourite's mid is inside the band. One entry per window; the window "
        "is the independent unit.")
    L.append(
        "- **Taker:** buy the favourite at its `ask`, pay fee "
        "`0.07*p*(1-p)` per share on entry, hold to resolution, settle at the "
        "true 0/1 outcome (no exit fee — a fee at p in {0,1} is 0).")
    L.append(
        "- **Maker:** post a resting BUY limit at the favourite's `bid`; it "
        "fills ONLY when a strictly-later tick shows that side's bid trade "
        "down to/through the limit (the realistic fill model from "
        "`maker_reframe.py`); unfilled = no trade; 0 fee; settle at the true "
        "outcome. Maker stats are over filled trades only.")
    L.append(
        "- **Null baseline:** at the *same* entry tick the favourite rule "
        "selects, buy a side chosen uniformly at random. Same windows, same "
        "ticks, same costs — isolates the value of the *favourite* choice "
        "from the value of the *timing*.")
    L.append(
        "- **Book-health guard:** both sides priced in (0.001,0.999), positive "
        "bids below asks, complement-consistent within 6c — drops "
        "decided-market books.")
    L.append(
        "- **CIs:** 90% window-clustered bootstraps (groups = `slug`).")
    L.append("")
    L.append("### Data scope and the 5m label caveat")
    L.append("")
    L.append(
        "Scope is **~8 days, May 15-22 2026, 4 symbols** (BTC/ETH/SOL/XRP) — "
        "limited, but it is what we have. DEV = May 15-20; sealed HOLD-OUT = "
        "May 21-22.")
    L.append("")
    L.append(
        "**15m labels** are authoritative: the `ticks_15m.parquet` baked-in "
        "`outcome_up` matches `corrected_labels.parquet` (the Polymarket API "
        "ground truth) for 100% of windows — verified.")
    L.append("")
    L.append(
        "**5m labels** need care: the `ticks_5m.parquet` baked-in `outcome_up` "
        "is **corrupt** — it disagrees with the corrected 5m outcomes on ~31% "
        "of windows (verified: 1,564 of 5,018). This backtest uses "
        "`corrected_labels_5m.parquet` (the gamma-API ground truth) instead. "
        "That cache was fetched for the DEV split only, so the 5m DEV "
        "backtest is on corrected labels; the 5m hold-out needs an on-demand "
        "fetch (`--fetch-holdout-5m`), done only if a DEV result warrants it.")
    L.append("")
    L.append("---")
    L.append("")

    for tf in ("15m", "5m"):
        tr = r["timeframes"][tf]
        L.append(f"## {tf} markets")
        L.append("")
        L.append(
            f"DEV: {tr['n_dev_win']:,} windows over {tr['n_dev_days']} UTC "
            f"days.")
        L.append("")
        L.append(f"### {tf} — calibration / expectancy sweep (DEV)")
        L.append("")
        L.append(
            "Each row: the first qualifying entry tick of every window whose "
            "favourite mid lands in the bucket. `realized win rate` vs `mean "
            "entry odds` is the calibration; a well-calibrated favourite has "
            "realized win rate ~ entry odds.")
        L.append("")
        L.append(_sweep_table(tr["sweep"]))
        L.append("")
        # quick calibration read
        cal = [s for s in tr["sweep"] if s.get("n", 0) >= 30]
        if cal:
            gaps = [s["win_rate"] - s["mean_entry_mid"] for s in cal]
            L.append(
                f"Calibration read: mean (realized win rate - entry mid) over "
                f"well-populated buckets = **{np.mean(gaps)*100:+.2f}c**. "
                + ("The favourite side is essentially calibrated — realized "
                   "win rate tracks entry odds."
                   if abs(np.mean(gaps)) < 0.03 else
                   "The favourite side is not perfectly calibrated — see the "
                   "bucket gaps above."))
            L.append("")

        for blk, bname in ((tr["primary"], "M15-DH-1 primary band [0.60, 0.90]"),
                           (tr["sensitivity"],
                            "sensitivity band [0.50, 0.95]")):
            L.append(f"### {tf} — {bname} (DEV)")
            L.append("")
            L.append(_band_table(blk))
            L.append("")

        # hold-out
        L.append(f"### {tf} — sealed hold-out (May 21-22)")
        L.append("")
        if not tr["warrant"]:
            L.append(
                "**Not opened.** No DEV configuration (primary or sensitivity "
                "band, taker or maker) produced a net PnL/trade whose 90% "
                "window-clustered CI excludes zero on the positive side. A "
                "rule that already fails on DEV does not warrant consuming "
                "the hold-out — it stays sealed. (This mirrors the discipline "
                "of `PHASE2_RERUN_VERDICT.md`.)")
        elif tr["holdout"] and tr["holdout"].get("deferred"):
            L.append(
                f"**Warranted but deferred.** DEV produced a CI-positive "
                f"config ({tr['warrant']}), but the corrected 5m hold-out "
                f"labels are not cached. Re-run with `--fetch-holdout-5m` to "
                f"fetch them and open the hold-out.")
        elif tr["holdout"]:
            L.append(
                f"**Opened once** for the CI-positive DEV config(s): "
                f"{tr['warrant']}.")
            L.append("")
            for bname, blk in tr["holdout"].items():
                L.append(f"#### {tf} {bname} — HOLDOUT")
                L.append("")
                L.append(_band_table(blk))
                L.append("")
        L.append("")

    # ---- overall verdict ----
    L.append("---")
    L.append("")
    L.append("## Verdict")
    L.append("")
    for tf in ("15m", "5m"):
        tr = r["timeframes"][tf]
        L.append(f"**{tf}:**")
        L.append("")
        L.append(_verdict_line(tr["primary"], tf, "[0.60,0.90]"))
        L.append(_verdict_line(tr["sensitivity"], tf, "[0.50,0.95]"))
        L.append("")

    # synthesis
    p15 = r["timeframes"]["15m"]["primary"]["pooled_fav"]
    p5 = r["timeframes"]["5m"]["primary"]["pooled_fav"]
    w15 = r["timeframes"]["15m"]["warrant"]
    w5 = r["timeframes"]["5m"]["warrant"]
    L.append("### Does buy-favourite-hold-to-resolution have a real, "
             "cost-surviving, out-of-fold edge?")
    L.append("")

    def _tf_clause(tf, pf, warrant):
        if pf.get("n", 0) == 0:
            return f"- **{tf}:** no trades — inconclusive."
        tk, mk = pf["taker_pnl"], pf.get("maker_pnl", float("nan"))
        tkci, mkci = pf.get("taker_ci"), pf.get("maker_ci")
        tk_neg = tkci and tkci[2] < 0
        mk_neg = mkci and mkci[2] < 0
        if warrant:
            return (f"- **{tf}:** a DEV config was CI-positive — see the "
                    f"hold-out result above for whether it survives "
                    f"out-of-fold.")
        verdict = "NO"
        return (f"- **{tf}: {verdict}.** Primary band [0.60,0.90] taker "
                f"${tk:+.4f}/trade"
                + (" (CI entirely negative)" if tk_neg else
                   " (CI straddles/below 0)")
                + f", maker ${mk:+.4f}/trade"
                + (" (CI entirely negative)" if mk_neg else
                   " (CI straddles/below 0)")
                + ". No DEV config cleared the CI-positive bar, so the "
                "hold-out stayed sealed. Buy-favourite-hold does not have a "
                "cost-surviving edge here.")

    L.append(_tf_clause("15m", p15, w15))
    L.append(_tf_clause("5m", p5, w5))
    L.append("")
    L.append("### Connection to prior research")
    L.append("")
    L.append(
        "If the 15m result is negative, it **confirms** the prior calibration "
        "finding (`PHASE2_RERUN_VERDICT.md`): the favourite side is the "
        "complement of the well-calibrated cheap side, so buying the "
        "favourite and holding is gross-breakeven and loses exactly the "
        "round-trip cost. The leaderboard's directional-holder pattern is then "
        "**survivorship variance**, not a positive-expectancy edge — the "
        "central caveat the wallet analysis flagged. 5m was genuinely "
        "untested before this task; any divergence between 5m and 15m is new "
        "ground and is called out in the per-timeframe verdicts above.")
    L.append("")
    L.append(
        "**Data-scope honesty.** ~8 days, 4 symbols. The DEV split is 6 days; "
        "the hold-out is 2. This is a small sample — a clean CI-negative "
        "result is a reliable *rejection* of a sizeable edge, but a small "
        "true edge could hide inside the CIs. The verdict is stated at the "
        "precision the data supports.")
    L.append("")
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch-holdout-5m", action="store_true",
                    help="fetch corrected 5m hold-out outcomes from the gamma "
                         "API if a DEV config warrants opening the hold-out")
    args = ap.parse_args()
    run(fetch_holdout_5m=args.fetch_holdout_5m)
