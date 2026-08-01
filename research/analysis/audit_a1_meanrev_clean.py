"""AUDIT A1 — Re-test the RETIRED mean-reversion configs on the CLEAN parquet.

Question: were the disabled mean-reversion strategies killed for sound CAUSAL
reasons, or is there a missed edge? Their live-paper PnL was generated under known
data bugs (strike bug -> 31% wrong labels; stale move_pct). So we re-simulate the
core mean-reversion ENTRY thesis on the clean joined_15m parquet (fresh spot,
TRUE outcomes), one trade per window, hold to resolution, realistic one-way cost.

FAITHFUL-ENOUGH entry replay (not the exact FLAT/ARMED/HOLDING machine):
 - drop trigger: a price drop in the side we BUY, detected as the buy-side ask
   falling >= drop_pct below its trailing-max over drop_win_sec (the side got cheap).
 - side: DOWN->buy NO; UP->buy YES; BOTH->whichever side dropped into the band first.
 - band on the ask of the side bought; time/spread/depth gates as configured;
   time_of_day ASIA(00-08 UTC)/OVERNIGHT(00-06 UTC).
 - ONE trade/window = first qualifying tick. Hold to resolution; settle on
   outcome_up_clean. Cost = one-way taker 0.07*p*(1-p)*shares. $10 stake.

HOLD-TO-RESOLUTION IS THE UPPER BOUND on a buy-the-dip taker (real exits only add
round-trip cost). Negative here => the real exit-laden strategy is even worse
(conservative-strong negative). Positive here would be the only route to a missed
edge and would warrant the full state-machine replay.

Vectorized: per-config single pass; rolling-max drop detection via groupby.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

PARQUET = "data/research/joined_15m.parquet"
STAKE = 10.0
FEE = 0.07
COLS = ['symbol','slug','window_start_ts','seconds_into_window','time_left_sec',
        'date','split','cb_spot','yes_best_ask','no_best_ask','spread_yes',
        'yes_ask_depth','no_ask_depth','start_price','outcome_up_clean','book_healthy']


def load():
    df = pd.read_parquet(PARQUET, columns=COLS)
    m = (df['book_healthy'].astype(bool) & df['outcome_up_clean'].notna()
         & df['cb_spot'].notna() & (df['start_price'] > 0))
    df = df[m].copy()
    df.sort_values(['slug', 'seconds_into_window'], inplace=True, kind='mergesort')
    df['hour'] = pd.to_datetime(df['window_start_ts'].values, unit='s', utc=True).hour
    return df.reset_index(drop=True)


def rolling_max_within(df, valcol, win_sec):
    """Per-slug trailing max of valcol over the last win_sec seconds (inclusive).
    Time index is seconds_into_window. Uses a time-based rolling on a per-slug
    Datetime-like integer index. Vectorized via groupby.rolling on an int window
    is not exact for irregular gaps, so we build a proper time-rolling."""
    out = np.empty(len(df), dtype=float)
    sw_all = df['seconds_into_window'].values
    val_all = df[valcol].values
    # iterate groups but with a two-pointer O(n) trailing-max deque per slug.
    start = 0
    codes = df['slug'].values
    # find group boundaries (df is sorted by slug then sw)
    n = len(df)
    i = 0
    while i < n:
        j = i
        while j < n and codes[j] == codes[i]:
            j += 1
        sw = sw_all[i:j]; val = val_all[i:j]
        # monotonic deque of indices for max
        from collections import deque
        dq = deque()
        lo = 0
        res = np.empty(j - i, dtype=float)
        for k in range(j - i):
            # pop from front out-of-window
            while dq and sw[dq[0]] < sw[k] - win_sec:
                dq.popleft()
            # maintain decreasing deque
            while dq and val[dq[-1]] <= val[k]:
                dq.pop()
            dq.append(k)
            res[k] = val[dq[0]]
        out[i:j] = res
        i = j
    return out


def fee_oneway(shares, price):
    p = np.clip(price, 0.0, 1.0)
    return shares * FEE * p * (1.0 - p)


def tod_mask(hour, tod):
    if tod == 'ASIA':
        return (hour >= 0) & (hour < 8)
    if tod == 'OVERNIGHT':
        return (hour >= 0) & (hour < 6)
    return np.ones(len(hour), dtype=bool)


def simulate(df, cfg, roll_cache):
    side = cfg['side']; pmin = cfg['pmin']; pmax = cfg['pmax']
    win = cfg['drop_win_sec']; drop_pct = cfg['drop_pct']
    hour = df['hour'].values
    base = (tod_mask(hour, cfg['tod'])
            & (df['time_left_sec'].values >= cfg['min_time_left'])
            & (df['seconds_into_window'].values >= cfg['min_sec_into'])
            & (df['spread_yes'].values <= cfg['max_spread']))

    rows = []
    # build per-side qualifying mask
    def side_mask(askcol, depthcol):
        ask = df[askcol].values
        depth = df[depthcol].values
        ref = roll_cache[(askcol, win)]
        with np.errstate(divide='ignore', invalid='ignore'):
            drop = np.where(ref > 0, (ref - ask) / ref * 100.0, -1.0)
        return base & (ask >= pmin) & (ask <= pmax) & (depth >= cfg['min_depth']) & (drop >= drop_pct), ask

    masks = {}
    if side in ('DOWN', 'BOTH'):
        m, ask = side_mask('no_best_ask', 'no_ask_depth'); masks['NO'] = (m, ask)
    if side in ('UP', 'BOTH'):
        m, ask = side_mask('yes_best_ask', 'yes_ask_depth'); masks['YES'] = (m, ask)

    # combine: a tick qualifies for a side. Pick FIRST qualifying tick per slug
    # across allowed sides (earliest seconds_into_window).
    qual_any = np.zeros(len(df), dtype=bool)
    qual_side = np.empty(len(df), dtype=object)
    qual_ask = np.full(len(df), np.nan)
    for sname, (m, ask) in masks.items():
        # prefer not to overwrite an earlier-set side at the same row; both rare.
        take = m & ~qual_any
        qual_any |= m
        qual_side[take] = sname
        qual_ask[take] = ask[take]

    if not qual_any.any():
        return pd.DataFrame()

    sub = df[qual_any].copy()
    sub['buyside'] = qual_side[qual_any]
    sub['ask_buy'] = qual_ask[qual_any]
    # first qualifying tick per slug (already sorted by sw)
    first = sub.groupby('slug', sort=False).first().reset_index()
    first = first[(first['ask_buy'] > 0) & (first['ask_buy'] < 1)]
    shares = STAKE / first['ask_buy'].values
    oc = first['outcome_up_clean'].values
    won = np.where(first['buyside'].values == 'YES', oc == 1.0, oc == 0.0)
    fee = fee_oneway(shares, first['ask_buy'].values)
    pnl = shares * won.astype(float) - STAKE - fee
    return pd.DataFrame({'slug': first['slug'].values, 'side': first['buyside'].values,
                         'ask': first['ask_buy'].values, 'won': won, 'pnl': pnl,
                         'split': first['split'].values})


def summ(tr, label):
    if len(tr) == 0:
        return f"{label:34s} N=0"
    n = len(tr); wr = tr['won'].mean(); ev = tr['pnl'].mean()
    se = tr['pnl'].std(ddof=1) / np.sqrt(n) if n > 1 else float('nan')
    lo, hi = ev - 1.96 * se, ev + 1.96 * se
    star = '  <== CI>0' if lo > 0 else ''
    return (f"{label:34s} N={n:5d} WR={wr*100:5.1f}% EV/tr={ev:+7.3f} "
            f"CI[{lo:+6.2f},{hi:+6.2f}] tot={tr['pnl'].sum():+9.1f}{star}")


CONFIGS = {
  'cfg_21c8c00165b3 (DOWN dip)': dict(side='DOWN', pmin=0.075, pmax=0.125, min_time_left=180, min_sec_into=15, tod='ALL', min_depth=15, max_spread=0.10, drop_pct=15.0, drop_win_sec=30),
  'cfg_333fde9cecb8 (BOTH ASIA)': dict(side='BOTH', pmin=0.05, pmax=0.15, min_time_left=180, min_sec_into=15, tod='ASIA', min_depth=50, max_spread=0.10, drop_pct=15.0, drop_win_sec=60),
  'relaxed_v1 (wide BOTH)': dict(side='BOTH', pmin=0.05, pmax=0.25, min_time_left=120, min_sec_into=15, tod='ALL', min_depth=15, max_spread=0.20, drop_pct=10.0, drop_win_sec=60),
  'cfg_velocity_v1 (fast DOWN)': dict(side='DOWN', pmin=0.075, pmax=0.125, min_time_left=180, min_sec_into=15, tod='ALL', min_depth=15, max_spread=0.10, drop_pct=12.0, drop_win_sec=15),
  'cfg_max_pnl_v1 (0.20-0.45)': dict(side='BOTH', pmin=0.20, pmax=0.45, min_time_left=300, min_sec_into=60, tod='ALL', min_depth=20, max_spread=0.05, drop_pct=5.0, drop_win_sec=90),
  'cfg_max_pnl_v3 (0.20-0.50)': dict(side='BOTH', pmin=0.20, pmax=0.50, min_time_left=300, min_sec_into=60, tod='ALL', min_depth=20, max_spread=0.05, drop_pct=5.0, drop_win_sec=90),
  'cfg_balanced_v1 (tight spr)': dict(side='BOTH', pmin=0.25, pmax=0.45, min_time_left=300, min_sec_into=60, tod='ALL', min_depth=20, max_spread=0.005, drop_pct=8.0, drop_win_sec=90),
  'cfg_late_panic_v1 (last 5m)': dict(side='BOTH', pmin=0.10, pmax=0.20, min_time_left=60, min_sec_into=600, tod='ALL', min_depth=20, max_spread=0.05, drop_pct=20.0, drop_win_sec=60),
  'v2_gold_01_both_asia': dict(side='BOTH', pmin=0.18, pmax=0.255, min_time_left=180, min_sec_into=15, tod='ASIA', min_depth=15, max_spread=0.05, drop_pct=10.0, drop_win_sec=20),
  'v2_gold_03_down_all': dict(side='DOWN', pmin=0.10, pmax=0.25, min_time_left=540, min_sec_into=0, tod='ALL', min_depth=150, max_spread=0.12, drop_pct=10.0, drop_win_sec=90),
  'v2_gold_05_up_overnight': dict(side='UP', pmin=0.05, pmax=0.15, min_time_left=180, min_sec_into=60, tod='OVERNIGHT', min_depth=150, max_spread=0.20, drop_pct=18.0, drop_win_sec=60),
  'sv2_baseline (BOTH ASIA)': dict(side='BOTH', pmin=0.090, pmax=0.319, min_time_left=223, min_sec_into=94, tod='ASIA', min_depth=76.45, max_spread=0.0281, drop_pct=15.04, drop_win_sec=296),
}


def main():
    df = load()
    print(f"Loaded {len(df):,} clean ticks, {df['slug'].nunique()} windows")
    print(f"Windows/split: {dict(df.groupby('split')['slug'].nunique())}\n")

    wins = sorted({c['drop_win_sec'] for c in CONFIGS.values()})
    roll_cache = {}
    for askcol in ('yes_best_ask', 'no_best_ask'):
        for w in wins:
            roll_cache[(askcol, w)] = rolling_max_within(df, askcol, w)
    print("rolling-max caches built\n")

    results = {name: simulate(df, cfg, roll_cache) for name, cfg in CONFIGS.items()}

    for name, tr in results.items():
        print(f"### {name}")
        if len(tr):
            for sp in ['dev', 'holdout', 'future']:
                print("    " + summ(tr[tr['split'] == sp], sp))
            print("    " + summ(tr, 'ALL'))
        else:
            print("    N=0 (no qualifying trades)")
        print()

    print("=" * 72)
    print("DECISION SLICE — FUTURE split (06-01..04), freshest OOS:")
    any_pos = False
    for name, tr in results.items():
        t = tr[tr['split'] == 'future'] if len(tr) else tr
        s = summ(t, name)
        print("  " + s)
        if 'CI>0' in s:
            any_pos = True
    print(f"\nANY config CI-positive on FUTURE split: {any_pos}")

    # pooled all-config aggregate (each window can appear once per config)
    allc = pd.concat([tr for tr in results.values() if len(tr)], ignore_index=True)
    print("\n" + "=" * 72)
    print("POOLED across all 12 representative configs:")
    for sp in ['dev', 'holdout', 'future']:
        print("  " + summ(allc[allc['split'] == sp], sp))
    print("  " + summ(allc, 'ALL'))


if __name__ == '__main__':
    main()
