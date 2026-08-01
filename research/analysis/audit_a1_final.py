"""AUDIT A1 — FINAL robustness pass on the two FUTURE-split positives
(cfg_late_panic_v1, v2_gold_03_down_all) and the pooled longshot bucket.

Confirmed upstream:
  * The 'drop trigger' is INERT (null test: drop vs no-drop identical EV) -> the
    mean-reversion signal does nothing; PnL is pure 'buy cheap side, hold'.
  * *_ask_depth is in SHARES; cheap-longshot L1 depth covers a $10 stake only
    ~36-44% of the time -> top-of-book fill is optimistic.
  * Live strategies LOST because exits (stop/max_hold/forced/trailing) bled the
    losers while profit-targets capped the rare 10x winners that pay for them.

This pass: (1) realistic ladder fill using l2_depth_ask_2c (shares within 2c),
remainder beyond that filled 2c worse; (2) OUTLIER sensitivity — drop the top-5
winners and re-test the future split; (3) bootstrap CI on EV (skew-robust).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

PARQUET = "data/research/joined_15m.parquet"
STAKE = 10.0; FEE = 0.07; TICK = 0.01
COLS = ['slug','window_start_ts','seconds_into_window','time_left_sec','split',
        'cb_spot','yes_best_ask','no_best_ask','spread_yes','yes_ask_depth',
        'no_ask_depth','l2_depth_ask_2c','start_price','outcome_up_clean','book_healthy']


def load():
    df = pd.read_parquet(PARQUET, columns=COLS)
    m = (df['book_healthy'].astype(bool) & df['outcome_up_clean'].notna()
         & df['cb_spot'].notna() & (df['start_price'] > 0))
    df = df[m].copy()
    df.sort_values(['slug', 'seconds_into_window'], inplace=True, kind='mergesort')
    df['hour'] = pd.to_datetime(df['window_start_ts'].values, unit='s', utc=True).hour
    return df.reset_index(drop=True)


def tod_mask(hour, tod):
    if tod == 'ASIA':
        return (hour >= 0) & (hour < 8)
    if tod == 'OVERNIGHT':
        return (hour >= 0) & (hour < 6)
    return np.ones(len(hour), dtype=bool)


def candidates(df, cfg):
    """First qualifying tick per slug (no drop trigger needed — proven inert)."""
    hour = df['hour'].values
    base = (tod_mask(hour, cfg['tod'])
            & (df['time_left_sec'].values >= cfg['min_time_left'])
            & (df['seconds_into_window'].values >= cfg['min_sec_into'])
            & (df['spread_yes'].values <= cfg['max_spread']))
    side = cfg['side']; pmin = cfg['pmin']; pmax = cfg['pmax']
    qual = np.zeros(len(df), dtype=bool)
    bs = np.empty(len(df), dtype=object); ask = np.full(len(df), np.nan)

    def one(askcol, dcol, sname):
        a = df[askcol].values
        m = base & (a >= pmin) & (a <= pmax) & (df[dcol].values >= cfg['min_depth'])
        take = m & ~qual
        bs[take] = sname; ask[take] = a[take]
        return m
    if side in ('DOWN', 'BOTH'):
        qual |= one('no_best_ask', 'no_ask_depth', 'NO')
    if side in ('UP', 'BOTH'):
        qual |= one('yes_best_ask', 'yes_ask_depth', 'YES')
    if not qual.any():
        return None
    sub = df[qual].copy(); sub['buyside'] = bs[qual]; sub['ask_buy'] = ask[qual]
    f = sub.groupby('slug', sort=False).first().reset_index()
    return f[(f['ask_buy'] > 0) & (f['ask_buy'] < 1)].reset_index(drop=True)


def pnl_optimistic(f):
    sh = STAKE / f['ask_buy'].values
    oc = f['outcome_up_clean'].values
    won = np.where(f['buyside'].values == 'YES', oc == 1.0, oc == 0.0)
    p = np.clip(f['ask_buy'].values, 0, 1)
    return won, sh * won - STAKE - sh * FEE * p * (1 - p)


def pnl_realistic(f):
    """2-tier ladder: L1 = l1 ask-depth (shares) at quoted ask; next tier =
    l2_depth_ask_2c (shares within 2c) at ask+1c; remainder at ask+2c."""
    ask = f['ask_buy'].values
    l1 = np.where(f['buyside'].values == 'YES', f['yes_ask_depth'].values,
                  f['no_ask_depth'].values).astype(float)
    d2c = f['l2_depth_ask_2c'].values.astype(float)  # total shares within 2c (incl L1)
    tier2 = np.maximum(d2c - l1, 0.0)                 # shares between best and +2c
    oc = f['outcome_up_clean'].values
    won = np.where(f['buyside'].values == 'YES', oc == 1.0, oc == 0.0)
    shares = np.empty(len(f)); avg = np.empty(len(f))
    for i in range(len(f)):
        rem = STAKE; sh = 0.0; cost = 0.0
        for lvl_sh, lvl_px in ((l1[i], ask[i]),
                               (tier2[i], min(ask[i] + TICK, 0.999)),
                               (1e18, min(ask[i] + 2 * TICK, 0.999))):
            if rem <= 1e-9:
                break
            usd_here = lvl_sh * lvl_px
            if usd_here >= rem:
                sh += rem / lvl_px; cost += rem; rem = 0.0
            else:
                sh += lvl_sh; cost += usd_here; rem -= usd_here
        avg[i] = cost / sh if sh > 0 else ask[i]; shares[i] = sh
    p = np.clip(avg, 0, 1)
    return won, shares * won - STAKE - shares * FEE * p * (1 - p)


def boot_ci(pnl, n=5000, seed=0):
    if len(pnl) < 2:
        return (float('nan'), float('nan'))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(pnl), size=(n, len(pnl)))
    means = pnl[idx].mean(axis=1)
    return np.percentile(means, 2.5), np.percentile(means, 97.5)


def report(f, label):
    if f is None or len(f) == 0:
        print(f"{label}: N=0"); return
    for split in ['future', 'holdout', 'dev']:
        ff = f[f['split'] == split]
        if len(ff) == 0:
            continue
        wo, po = pnl_optimistic(ff); wr, pr = pnl_realistic(ff)
        bo = boot_ci(po); br = boot_ci(pr)
        # drop top-5 winners robustness (on realistic)
        pr_sorted = np.sort(pr)[::-1]
        pr_drop5 = pr_sorted[5:] if len(pr_sorted) > 5 else pr_sorted
        print(f"  [{split:7s}] N={len(ff):4d} WR={wo.mean()*100:4.1f}%")
        print(f"      OPT  EV={po.mean():+6.3f} boot95[{bo[0]:+5.2f},{bo[1]:+5.2f}] median={np.median(po):+5.2f}")
        print(f"      REAL EV={pr.mean():+6.3f} boot95[{br[0]:+5.2f},{br[1]:+5.2f}] median={np.median(pr):+5.2f}")
        print(f"      REAL minus top-5 winners: EV={pr_drop5.mean():+6.3f} (tot {pr_drop5.sum():+.0f} vs {pr.sum():+.0f})")


CONFIGS = {
  'cfg_late_panic_v1 (0.10-0.20, last 5m)': dict(side='BOTH', pmin=0.10, pmax=0.20, min_time_left=60, min_sec_into=600, tod='ALL', min_depth=20, max_spread=0.05),
  'v2_gold_03_down_all (NO 0.10-0.25)': dict(side='DOWN', pmin=0.10, pmax=0.25, min_time_left=540, min_sec_into=0, tod='ALL', min_depth=150, max_spread=0.12),
}


def main():
    df = load()
    print(f"Loaded {len(df):,} ticks, {df['slug'].nunique()} windows\n")
    for name, cfg in CONFIGS.items():
        print(f"### {name}")
        f = candidates(df, cfg)
        report(f, name)
        print()

    # POOLED generic longshot bucket: buy whichever side's ask in [0.08,0.25], first tick.
    print("### GENERIC LONGSHOT (buy any side ask in [0.08,0.25], 1st tick, no other filter)")
    cfg = dict(side='BOTH', pmin=0.08, pmax=0.25, min_time_left=0, min_sec_into=0, tod='ALL', min_depth=0, max_spread=1.0)
    f = candidates(df, cfg)
    report(f, 'generic_longshot')
    print()
    print("### GENERIC FAVOURITE (buy any side ask in [0.55,0.92], 1st tick)")
    cfg = dict(side='BOTH', pmin=0.55, pmax=0.92, min_time_left=0, min_sec_into=0, tod='ALL', min_depth=0, max_spread=1.0)
    f = candidates(df, cfg)
    report(f, 'generic_favourite')


if __name__ == '__main__':
    main()
