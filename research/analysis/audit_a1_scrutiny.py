"""AUDIT A1 — SCRUTINY of the surprising positive EVs in the hold-to-resolution
upper-bound test. Three adversarial checks on whether the 'missed edge' is real:

(1) DEPTH at entry: is the quoted-ask fill optimistic? Measure level-1 ask depth
    vs the $10 stake's share need at cheap-longshot entries. If $10 > L1 depth,
    the optimistic fill inflates the win payoff (which IS the whole edge).

(2) NULL test: does the 'drop trigger' do anything, or is this just 'buy ANY
    cheap longshot and hope'? Compare drop-triggered entries to RANDOM-tick
    entries in the same price band / same time gates.

(3) REALISTIC-FILL HAIRCUT: re-price the win payoff assuming the $10 walks the
    ask ladder. We don't have a full ladder, but we DO have l2_ask_depth (total)
    and l2_depth_ask_2c (within 2c). Model: fill what sits at best, the remainder
    1c worse (one tick). Recompute shares/EV. Does the FUTURE-split positive survive?

The two FUTURE-split CI-positive configs to stress: cfg_late_panic_v1, v2_gold_03_down_all.
Also stress the pooled longshot bucket.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

PARQUET = "data/research/joined_15m.parquet"
STAKE = 10.0
FEE = 0.07
TICK = 0.01

COLS = ['symbol','slug','window_start_ts','seconds_into_window','time_left_sec',
        'date','split','cb_spot','yes_best_ask','no_best_ask','spread_yes',
        'yes_ask_depth','no_ask_depth','l2_ask_depth','l2_depth_ask_2c',
        'start_price','outcome_up_clean','book_healthy']


def load():
    df = pd.read_parquet(PARQUET, columns=COLS)
    m = (df['book_healthy'].astype(bool) & df['outcome_up_clean'].notna()
         & df['cb_spot'].notna() & (df['start_price'] > 0))
    df = df[m].copy()
    df.sort_values(['slug', 'seconds_into_window'], inplace=True, kind='mergesort')
    df['hour'] = pd.to_datetime(df['window_start_ts'].values, unit='s', utc=True).hour
    return df.reset_index(drop=True)


def trailing_max(df, valcol, win_sec):
    out = np.empty(len(df), dtype=float)
    sw_all = df['seconds_into_window'].values; val_all = df[valcol].values
    codes = df['slug'].values; n = len(df); i = 0
    from collections import deque
    while i < n:
        j = i
        while j < n and codes[j] == codes[i]:
            j += 1
        sw = sw_all[i:j]; val = val_all[i:j]; dq = deque(); res = np.empty(j - i)
        for k in range(j - i):
            while dq and sw[dq[0]] < sw[k] - win_sec:
                dq.popleft()
            while dq and val[dq[-1]] <= val[k]:
                dq.pop()
            dq.append(k); res[k] = val[dq[0]]
        out[i:j] = res; i = j
    return out


def tod_mask(hour, tod):
    if tod == 'ASIA':
        return (hour >= 0) & (hour < 8)
    if tod == 'OVERNIGHT':
        return (hour >= 0) & (hour < 6)
    return np.ones(len(hour), dtype=bool)


def entry_index(df, cfg, ref_no, ref_yes, use_drop=True):
    """Return boolean per-row qualifying mask + buyside + ask + depth-of-buyside
    (l1 ask depth) + l2 depth-2c of buyside. First-qualifying handled by caller."""
    hour = df['hour'].values
    base = (tod_mask(hour, cfg['tod'])
            & (df['time_left_sec'].values >= cfg['min_time_left'])
            & (df['seconds_into_window'].values >= cfg['min_sec_into'])
            & (df['spread_yes'].values <= cfg['max_spread']))
    side = cfg['side']; pmin = cfg['pmin']; pmax = cfg['pmax']
    qual = np.zeros(len(df), dtype=bool)
    bs = np.empty(len(df), dtype=object); ask = np.full(len(df), np.nan)

    def one(askcol, ref, sname):
        a = df[askcol].values
        if use_drop:
            with np.errstate(divide='ignore', invalid='ignore'):
                drop = np.where(ref > 0, (ref - a) / ref * 100.0, -1.0)
            dmask = drop >= cfg['drop_pct']
        else:
            dmask = np.ones(len(df), dtype=bool)
        m = base & (a >= pmin) & (a <= pmax) & dmask
        # depth filter uses YES/NO ask depth (l1) per config min_depth
        dcol = 'no_ask_depth' if sname == 'NO' else 'yes_ask_depth'
        m = m & (df[dcol].values >= cfg['min_depth'])
        take = m & ~qual
        qual_local = m
        bs[take] = sname; ask[take] = a[take]
        return qual_local

    qmask = np.zeros(len(df), dtype=bool)
    if side in ('DOWN', 'BOTH'):
        qmask |= one('no_best_ask', ref_no, 'NO'); qual |= qmask
    if side in ('UP', 'BOTH'):
        m2 = one('yes_best_ask', ref_yes, 'YES'); qmask |= m2; qual |= qmask
    return qual, bs, ask


def first_per_slug(df, qual, bs, ask):
    if not qual.any():
        return None
    sub = df[qual].copy()
    sub['buyside'] = bs[qual]; sub['ask_buy'] = ask[qual]
    f = sub.groupby('slug', sort=False).first().reset_index()
    f = f[(f['ask_buy'] > 0) & (f['ask_buy'] < 1)]
    return f


def settle_optimistic(f):
    shares = STAKE / f['ask_buy'].values
    oc = f['outcome_up_clean'].values
    won = np.where(f['buyside'].values == 'YES', oc == 1.0, oc == 0.0)
    p = np.clip(f['ask_buy'].values, 0, 1)
    fee = shares * FEE * p * (1 - p)
    pnl = shares * won.astype(float) - STAKE - fee
    return won, pnl


def settle_realistic(f):
    """Walk a 2-level proxy ladder: L1 = the buy-side ask depth (shares) at quoted
    ask; remainder fills one tick (1c) worse. Caps shares -> lowers win payoff."""
    ask = f['ask_buy'].values
    # buy-side L1 depth in SHARES: depth columns are in USD notional? Check: the
    # live book yes_ask_depth is summed size (shares) per arb convention. Treat as shares.
    depthcol = np.where(f['buyside'].values == 'YES', f['yes_ask_depth'].values,
                        f['no_ask_depth'].values).astype(float)
    l1_shares = depthcol  # shares available at best ask
    oc = f['outcome_up_clean'].values
    won = np.where(f['buyside'].values == 'YES', oc == 1.0, oc == 0.0)

    # deploy STAKE: first l1_shares*ask USD at price=ask, remainder at ask+1c
    l1_usd = l1_shares * ask
    shares = np.empty(len(f)); avg = np.empty(len(f))
    for i in range(len(f)):
        if l1_usd[i] >= STAKE:
            sh = STAKE / ask[i]; av = ask[i]
        else:
            sh1 = l1_shares[i]; rem_usd = STAKE - l1_usd[i]
            p2 = min(ask[i] + TICK, 0.999)
            sh2 = rem_usd / p2
            sh = sh1 + sh2; av = STAKE / sh
        shares[i] = sh; avg[i] = av
    p = np.clip(avg, 0, 1)
    fee = shares * FEE * p * (1 - p)
    pnl = shares * won.astype(float) - STAKE - fee
    return won, pnl


def summ(won, pnl, label):
    n = len(pnl)
    if n == 0:
        return f"{label:40s} N=0"
    wr = won.mean(); ev = pnl.mean()
    se = pnl.std(ddof=1) / np.sqrt(n) if n > 1 else float('nan')
    lo, hi = ev - 1.96 * se, ev + 1.96 * se
    star = '  <== CI>0' if lo > 0 else ''
    return f"{label:40s} N={n:5d} WR={wr*100:5.1f}% EV={ev:+7.3f} CI[{lo:+6.2f},{hi:+6.2f}] tot={pnl.sum():+8.0f}{star}"


CONFIGS = {
  'cfg_late_panic_v1': dict(side='BOTH', pmin=0.10, pmax=0.20, min_time_left=60, min_sec_into=600, tod='ALL', min_depth=20, max_spread=0.05, drop_pct=20.0, drop_win_sec=60),
  'v2_gold_03_down_all': dict(side='DOWN', pmin=0.10, pmax=0.25, min_time_left=540, min_sec_into=0, tod='ALL', min_depth=150, max_spread=0.12, drop_pct=10.0, drop_win_sec=90),
  'cfg_21c8c00165b3': dict(side='DOWN', pmin=0.075, pmax=0.125, min_time_left=180, min_sec_into=15, tod='ALL', min_depth=15, max_spread=0.10, drop_pct=15.0, drop_win_sec=30),
}


def main():
    df = load()
    print(f"Loaded {len(df):,} ticks, {df['slug'].nunique()} windows\n")

    # caches
    refs = {}
    for w in sorted({c['drop_win_sec'] for c in CONFIGS.values()}):
        refs[('no', w)] = trailing_max(df, 'no_best_ask', w)
        refs[('yes', w)] = trailing_max(df, 'yes_best_ask', w)

    print("DEPTH-vs-STAKE at entry (is the top-of-book fill optimistic?):")
    print("  At a $0.10 ask, $10 needs 100 shares. If L1 ask depth < that, fill is optimistic.\n")

    for name, cfg in CONFIGS.items():
        w = cfg['drop_win_sec']
        qual, bs, ask = entry_index(df, cfg, refs[('no', w)], refs[('yes', w)], use_drop=True)
        f = first_per_slug(df, qual, bs, ask)
        if f is None or len(f) == 0:
            print(f"### {name}: N=0"); continue
        depthcol = np.where(f['buyside'].values == 'YES', f['yes_ask_depth'].values,
                            f['no_ask_depth'].values).astype(float)
        need = STAKE / f['ask_buy'].values
        cover = depthcol / need  # >=1 means L1 covers the whole stake
        print(f"### {name}  (N={len(f)})")
        print(f"    median L1 ask depth (shares): {np.median(depthcol):8.1f} | median shares NEEDED: {np.median(need):7.1f}")
        print(f"    frac of entries where L1 depth covers full $10: {(cover>=1).mean()*100:5.1f}%")
        print(f"    median coverage ratio (L1/need): {np.median(cover):.2f}")
        # EVs: optimistic vs realistic-haircut
        wo, po = settle_optimistic(f)
        wr_, pr = settle_realistic(f)
        for sp in ['dev', 'holdout', 'future']:
            mk = f['split'].values == sp
            print("    OPT  " + summ(wo[mk], po[mk], sp))
            print("    REAL " + summ(wr_[mk], pr[mk], sp))
        print()

    # NULL TEST: drop trigger vs random-tick entry in same band/time gates
    print("=" * 78)
    print("NULL TEST — does the DROP TRIGGER add anything vs buying the cheap side at a")
    print("random qualifying tick (same band/time/depth, NO drop requirement)?\n")
    for name, cfg in CONFIGS.items():
        w = cfg['drop_win_sec']
        # with drop
        q1, b1, a1 = entry_index(df, cfg, refs[('no', w)], refs[('yes', w)], use_drop=True)
        f1 = first_per_slug(df, q1, b1, a1)
        # without drop (use_drop=False)
        q0, b0, a0 = entry_index(df, cfg, refs[('no', w)], refs[('yes', w)], use_drop=False)
        f0 = first_per_slug(df, q0, b0, a0)
        print(f"### {name}")
        for tag, f in [('WITH drop ', f1), ('NO drop   ', f0)]:
            if f is None or len(f) == 0:
                print(f"    {tag}: N=0"); continue
            wo, po = settle_optimistic(f)
            mk = f['split'].values == 'future'
            print(f"    {tag} (FUTURE, opt-fill): " + summ(wo[mk], po[mk], ''))
            wo2, po2 = settle_optimistic(f)
            print(f"    {tag} (ALL,    opt-fill): " + summ(wo2, po2, ''))
        print()


if __name__ == '__main__':
    main()
