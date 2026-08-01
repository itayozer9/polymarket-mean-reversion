"""Loss-pattern mining for the two live edges (determinism + stale-quote).

Goal (user request): scan EVERY losing paper/backtest trade per active strategy,
find a robust, CAUSAL pattern behind the losers, and test a filter that removes
them WITHOUT overfitting.

Method discipline (this repo has a long history of fake edges):
  - Build the full per-trade ledger (winners AND losers) for each edge by reusing
    the exact live entry rules (oracle_mechanics primary 60/5/0.90; stale_quote
    frozen curve). One trade/window, real L2 fills, hold-to-resolution, TRUE
    outcome. So a "loser" here = the same loser the live bot would book.
  - Engineer only CAUSAL, decision-time-observable conditioners:
      adverse_vel_bps  : spot velocity projected AGAINST the favourite's win side
                         (>0 = spot reverting toward strike = the lock is fragile)
      dist_bps         : |spot-strike| cushion
      fav_ask          : entry price (thin edge near 0.90)
      depth_usd        : L2 ask depth at entry
      n_coins_volatile : cross-coin macro stress at the entry epoch (how many of
                         the 4 symbols are moving hard at once)
      prev_fav_lost    : did the SAME symbol's previous 15m window's favourite lose
                         (whipsaw-regime autocorrelation)
      utc_hour, dow, symbol, fav_side  : standard slices (thin -> caveated)
  - Discovery on DEV only (23-27). Candidate filters validated ONCE on the sealed
    hold-out (28-29) by the caller. Day-of-week is ~1 obs/level on 5 days = unusable.

Run: uv run python -m research.analysis.loss_patterns
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, settle_pnl, FeeSchedule
from research.dataset.feeds import load_l2_ladders
from research.clean_window import CLEAN_START, available_clean_dates

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
STAKE = 10.0
FEES = FeeSchedule()
_LV = range(1, 11)


# --------------------------------------------------------------------------
# shared prep
# --------------------------------------------------------------------------
def _base(df):
    df = df[df["book_healthy"] & df["outcome_up_clean"].notna()
            & df["cb_spot"].notna() & (df["start_price"] > 0)].copy()
    yf = df["yes_mid"] >= 0.5
    df["fav_side"] = np.where(yf, "yes", "no")
    df["fav_ask"] = np.where(yf, df["yes_best_ask"], 1.0 - df["yes_best_bid"])
    df["fav_won"] = np.where(yf, df["outcome_up_clean"] == 1,
                             df["outcome_up_clean"] == 0).astype("f8")
    df["abs_dist_bps"] = df["dist_strike_bps"].abs()
    sfy = df["dist_strike_bps"] > 0
    df["consistent"] = (yf & sfy) | (~yf & ~sfy)
    # adverse velocity: spot velocity projected against the favourite's win side.
    # fav win needs spot to STAY on its side of strike. For a consistent trade the
    # favourite's side is the side spot currently favours (sign of dist). Spot
    # reverting toward strike => moving opposite to sign(dist). So:
    #   adverse = -sign(dist) * spot_vel   (>0 means reverting toward strike).
    sgn = np.sign(df["dist_strike_bps"].to_numpy())
    df["adverse_vel_10s"] = -sgn * df["spot_vel_10s_bps"].to_numpy()
    df["adverse_vel_3s"] = -sgn * df["spot_vel_3s_bps"].to_numpy()
    # window_start_ts is in SECONDS (10-digit epoch)
    df["utc_hour"] = ((df["window_start_ts"] // 3600) % 24).astype(int)
    df["dow"] = (((df["window_start_ts"] // 86400) + 4) % 7).astype(int)  # 1970-01-01=Thu(4)
    df["depth_usd"] = df["l2_ask_depth"].fillna(df["yes_ask_depth"])
    return df


def _macro_stress(df_all):
    """Cross-coin macro stress per window epoch.

    NOTE (honesty): this is a WINDOW-AGGREGATE (median |vel| over the whole
    window) and therefore peeks at post-entry ticks. It looked like a clean
    filter on dev/holdout, but a POINT-IN-TIME recompute (other symbols' |vel|
    at the exact entry second) does NOT reproduce a robust monotonic effect — the
    dev/holdout signal was lookahead. RETIRED as a deployable filter; kept here
    only as a diagnostic. Real-time cross-coin stress is available live via
    MarketContext.snapshot (n_symbols_dipping_5pct_60s) and could be re-tested
    cleanly later. Returns window_start_ts -> n_coins_volatile (0..4)."""
    thr = df_all["spot_vel_10s_bps"].abs().quantile(0.75)
    # one row per (window_start_ts, symbol): take the median |vel| over the window
    g = (df_all.assign(av=df_all["spot_vel_10s_bps"].abs())
         .groupby(["window_start_ts", "symbol"])["av"].median().reset_index())
    g["hot"] = (g["av"] >= thr).astype(int)
    s = g.groupby("window_start_ts")["hot"].sum()
    return s.to_dict(), float(thr)


def _prev_fav_lost(first_trades):
    """For each trade's window, the SAME symbol's immediately-prior window's
    favourite-won flag (0/1) -> prev_fav_lost = 1 if that prior favourite LOST."""
    first_trades = first_trades.sort_values(["symbol", "window_start_ts"]).copy()
    first_trades["prev_fav_won"] = (first_trades.groupby("symbol")["fav_won"].shift(1))
    first_trades["prev_fav_lost"] = np.where(
        first_trades["prev_fav_won"].isna(), np.nan, 1.0 - first_trades["prev_fav_won"])
    return first_trades


# --------------------------------------------------------------------------
# determinism ledger (primary live rule 60 / 5 / 0.90)
# --------------------------------------------------------------------------
def det_ledger(df, ladders, t_max=60, dist_min=5, max_ask=0.90, latency=2):
    cand = df[(df["time_left_sec"] >= 1) & (df["time_left_sec"] <= t_max)
              & (df["abs_dist_bps"] >= dist_min) & df["consistent"]
              & df["fav_ask"].between(0.50, max_ask)]
    first = cand.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False).first()
    rows = []
    for _, r in first.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        sec = int(r["seconds_into_window"]) + latency
        try:
            lr = lad.loc[(r["slug"], sec)]
        except KeyError:
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        if r["fav_side"] == "yes":
            px = [lr[f"ask_px_{i}"] for i in _LV]; sz = [lr[f"ask_sz_{i}"] for i in _LV]
        else:
            px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]; sz = [lr[f"bid_sz_{i}"] for i in _LV]
        fill = walk_buy(px, sz, STAKE)
        if not fill.filled or fill.unfilled_usd > STAKE * 0.5:
            continue
        won = bool(r["fav_won"])
        rows.append(dict(
            slug=r["slug"], symbol=r["symbol"], date=str(r["date"]),
            window_start_ts=int(r["window_start_ts"]),
            entry_sec=int(r["seconds_into_window"]),
            pnl=settle_pnl(fill, won), won=int(won),
            dist_bps=float(r["abs_dist_bps"]), fav_ask=float(fill.avg_price),
            depth_usd=float(r["depth_usd"]) if pd.notna(r["depth_usd"]) else np.nan,
            adverse_vel_10s=float(r["adverse_vel_10s"]),
            adverse_vel_3s=float(r["adverse_vel_3s"]),
            time_left=int(r["time_left_sec"]), utc_hour=int(r["utc_hour"]),
            dow=int(r["dow"]), fav_side=r["fav_side"],
            fav_won=float(r["fav_won"]),
        ))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# stale-quote ledger (frozen curve, mid-window) — reuse stale_quote logic
# --------------------------------------------------------------------------
def _sq_prep(df):
    vol_bps = df["realized_vol"].to_numpy("f8") * 100.0
    tleft = np.clip(df["time_left_sec"].to_numpy("f8"), 1, None)
    exp_move = vol_bps * np.sqrt(tleft)
    z = np.where(exp_move > 1e-6, df["dist_strike_bps"].to_numpy("f8") / exp_move, np.nan)
    df = df.copy(); df["z"] = z
    return df


def _fit_curve(train, n_bins=25):
    t = train[np.isfinite(train["z"])].copy()
    t["z"] = t["z"].clip(-6, 6)
    qs = np.unique(np.quantile(t["z"], np.linspace(0, 1, n_bins + 1)))
    t["zb"] = pd.cut(t["z"], qs, include_lowest=True)
    g = t.groupby("zb", observed=True).agg(zc=("z", "mean"), p=("outcome_up_clean", "mean"))
    g = g.dropna().sort_values("zc")
    p = np.maximum.accumulate(g["p"].to_numpy())
    return g["zc"].to_numpy(), p


def sq_ledger(df, ladders, zc, p, margin=0.08, jump_bps=8,
              t_lo=60, t_hi=840, latency=2):
    d = df[(df["time_left_sec"] >= t_lo) & (df["time_left_sec"] <= t_hi)
           & np.isfinite(df["z"])].copy()
    d["model_p"] = np.interp(np.clip(d["z"], -6, 6), zc, p, left=p[0], right=p[-1])
    d["mis"] = d["model_p"] - d["yes_mid"]
    d = d[d["mis"].abs() >= margin]
    if jump_bps is not None:
        d = d[d["spot_vel_10s_bps"].abs() >= jump_bps]
    if d.empty:
        return pd.DataFrame()
    first = d.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False).first()
    rows = []
    for _, r in first.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        sec = int(r["seconds_into_window"]) + latency
        try:
            lr = lad.loc[(r["slug"], sec)]
        except KeyError:
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        buy_yes = r["mis"] > 0
        if buy_yes:
            px = [lr[f"ask_px_{i}"] for i in _LV]; sz = [lr[f"ask_sz_{i}"] for i in _LV]
            won = r["outcome_up_clean"] == 1
        else:
            px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]; sz = [lr[f"bid_sz_{i}"] for i in _LV]
            won = r["outcome_up_clean"] == 0
        fill = walk_buy(px, sz, STAKE)
        if not fill.filled or fill.unfilled_usd > STAKE * 0.5:
            continue
        won = bool(won)
        rows.append(dict(
            slug=r["slug"], symbol=r["symbol"], date=str(r["date"]),
            window_start_ts=int(r["window_start_ts"]),
            entry_sec=int(r["seconds_into_window"]),
            pnl=settle_pnl(fill, won), won=int(won),
            model_p=float(r["model_p"]), mis=float(r["mis"]), abs_mis=float(abs(r["mis"])),
            z=float(r["z"]), entry_ask=float(fill.avg_price),
            dist_bps=float(r["abs_dist_bps"]),
            depth_usd=float(r["depth_usd"]) if pd.notna(r["depth_usd"]) else np.nan,
            spot_vel_10s=float(r["spot_vel_10s_bps"]),
            time_left=int(r["time_left_sec"]), utc_hour=int(r["utc_hour"]),
            dow=int(r["dow"]), symbol_buy=("yes" if buy_yes else "no"),
        ))
    return pd.DataFrame(rows)


def _ladders(symbols):
    dates = available_clean_dates("btc")
    d1 = dates[-1] if dates else CLEAN_START
    return {s: load_l2_ladders(s, CLEAN_START, d1) for s in symbols}


def build_ledgers():
    full = _base(pd.read_parquet(JOINED))
    macro, thr = _macro_stress(full)
    ladders = _ladders(sorted(full["symbol"].unique()))
    sqful = _sq_prep(full)
    train = sqful[sqful["date"] <= "2026-05-24"]
    zc, p = _fit_curve(train)

    out = {}
    for split, dd in (("dev", full[full["split"] == "dev"]),
                      ("holdout", full[full["split"] == "holdout"])):
        det = det_ledger(dd, ladders)
        det["n_coins_volatile"] = det["window_start_ts"].map(macro).fillna(0).astype(int)
        det = _prev_fav_lost(det)
        sqdd = sqful[sqful["split"] == ("dev" if split == "dev" else "holdout")]
        # SQ must not fit on holdout: curve is frozen from train (<=05-24)
        sq = sq_ledger(sqdd, ladders, zc, p)
        if len(sq):
            sq["n_coins_volatile"] = sq["window_start_ts"].map(macro).fillna(0).astype(int)
        out[split] = (det, sq)
    return out, thr


if __name__ == "__main__":
    led, thr = build_ledgers()
    os.makedirs(os.path.join(REPO, "data", "research", "ledgers"), exist_ok=True)
    for split, (det, sq) in led.items():
        det.to_parquet(os.path.join(REPO, "data", "research", "ledgers", f"det_{split}.parquet"))
        sq.to_parquet(os.path.join(REPO, "data", "research", "ledgers", f"sq_{split}.parquet"))
        print(f"\n##### {split} #####  (macro hot-vel thr={thr:.2f} bps)")
        for name, l in (("determinism", det), ("stale_quote", sq)):
            if not len(l):
                print(f"  {name}: 0 trades"); continue
            nl = int((l["won"] == 0).sum())
            print(f"  {name}: n={len(l)}  WR={l['won'].mean()*100:.1f}%  "
                  f"${l['pnl'].mean():+.3f}/tr  losers={nl}  "
                  f"loss$={l.loc[l['won']==0,'pnl'].sum():+.1f}  win$={l.loc[l['won']==1,'pnl'].sum():+.1f}")
