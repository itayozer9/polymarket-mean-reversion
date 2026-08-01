# Dual-oracle gate + det_d12 refinement (2026-06-09)

## Why
`det_d12_wide_live` (real money) lost ~$18 overnight (06-08→09): 16W/8L, where the favourite-
longshot payoff (win ~+$1, loss ~−$5) turns a bad win-rate night sharply red. Root cause: the
determinism SIGNAL is **Coinbase** (`move_pct`), but Polymarket SETTLES on **Chainlink** — near-
strike windows flip and a flip is a ~$6 swing. Overnight, 4 of 24 matched windows flipped
(net −$11.28). Goal: cut the flip-losers (dual-oracle gate + tighter band) and capture the
in-band survivors (laddered fill), validated on Chainlink before any live change.

## Data
Rebuilt `joined_15m` through **06-09** (5.9M rows, 18 days 05-23→06-09; chainlink_price 99.6%).
Chainlink feed `data/live_chainlink/` covers 05-15→06-09. All EV Chainlink-settled via
`edge_lab.simulate`; future split = 06-01→06-09 (held-out, includes the recent losing windows).
Stake $10 (research convention; live is $5, per-share economics scale linearly).

## A1 — flip risk (research/analysis/dual_oracle_gap.py)
935 det_d12 entries resolved on Chainlink: **flip% 13.2, WR_cl 84.8%, EV_cl +$1.34/tr (+$1251)**.
det_d12 is POSITIVE over the full 2.5 weeks — the overnight loss was variance, not a dead edge.
- **AGREE gate** (require Chainlink to agree with Coinbase on direction at entry): keeps 81% of
  volume, flip 13.2→11.3%, WR 84.8→88.0%, EV +1.34→+1.54/tr. Lifts EV on the **future** block too
  (+1.18→+1.35) → not overfit. The 19% disagreement trades it cuts are genuinely bad (71% WR, +0.47).
- `fav_ask` 0.78–0.85 is the worst bucket (EV +0.48, 434 trades) → lower `max_ask`.
- `dist_min` best at 12 on Chainlink (not monotonic — unlike the old Coinbase sweep). oracle_age flat.

## A3 — gate + param sweep (research/analysis/dual_oracle_sweep.py)
edge_lab harness (window-clustered CI, CPCV, DSR, latency survival). Future-blind selection.
- baseline: FULL +1.26 [0.92,1.59], future +1.20, CPCV 100%, DSR 0.997, latency 10s +1.01.
- `max_ask` is the EV lever: 0.85→0.78 lifts future +1.20→**+1.68**; 0.75→+1.82 (WR 81%, thinner).
- AGREE gate ≈ EV-neutral on future but raises WR and cuts the flip tail (the overnight failure mode).
- `adverse_vel≤2` a small free lift; `t_max` best 180 (240 dilutes); both sides kept.

### FINAL config — AGREE + max_ask 0.78 + adverse_vel≤2
**FULL +$2.12/tr [+1.56,+2.66], future +$1.97/tr [+1.31,+2.59] n=164, WR 87%, CPCV 100%, DSR 0.996,
latency 2s +2.12 / 5s +1.70 / 10s +1.66.** ≈ +64% over baseline future (+1.20→+1.97), robust on the
held-out block, latency-proof. All three levers stack.

## A4 — fill capture (research/analysis/fill_capture_backtest.py)
Single-shot FAK(limit=ask+0.05,cap0.92) vs laddered-within-band(cap max_ask), real L2 ladder, $5:
- single-shot 88% fill, EV +0.64; laddered 90% fill, EV +0.64; net +$10.
- Incremental trades laddering captures: 84% WR, +$0.45/tr (POSITIVE — adds value).
- Single-shot OVERPAID above the band on 37 trades (avg 0.865) + 24 above-band fills the cap refuses.
- Modest at $5 but structurally correct (no overpay into −EV); matters more at max_ask=0.78.

## A5 — adaptive max_ask (research/analysis/dynamic_max_ask.py)
Flat 0.78 is the best EV/tr but caps volume; 0.85 adds volume at lower EV. Conditioning the ceiling
on market state: **volatility-conditioning did NOT help** (all below flat-0.78); **Chainlink-lock-depth
did** — raise the ceiling to 0.85 only when |cl_dist|>=20bps (deep lock = the expensive favourite is
genuinely safe), else 0.78:
- flat 0.78: n=269, future +$1.76 [+1.10,+2.37], total $454, CPCV 100%, DSR 0.997.
- **cl-dyn 0.78→0.85 @|cl_dist|>=20: n=315 (+17%), future +$1.75 [+1.25,+2.23] (TIGHTER CI), total
  $522 (+15%), CPCV 100%, DSR 0.994.** Matches flat-0.78 per-trade EV with more volume + more total +
  a better downside CI. Mechanistic (deep lock = safe), though the 20bps threshold is somewhat fitted
  on 2.5 weeks. Deployed (max_ask 0.78, max_ask_hi 0.85, cl_dist_hi_bps 20). The intent carries the
  per-trade EFFECTIVE ceiling so the laddered fill caps consistently.

## Deployment
- New PAPER twin `det_d12_dual_v1` (live:false, $1000) with the FINAL config, alongside
  `det_d12_wide_v1` (the A/B control).
- New LIVE `det_d12_dual_live` (live:true, $100, $25/day, oracle_gate=agree, on_missing=skip,
  max_ask 0.78, adverse_vel 2.0) as the new primary; `det_d12_wide_live` → live:false (backup,
  +$21.93 history preserved).
- Engine: dual-oracle gate (determinism_state + registry + TICK_DTYPE cl_dist_bps + ws_collector
  live wiring) and laddered fill capped at the per-strategy max_ask (live_executor + intent),
  generic for all current/future live strategies. All no-op-safe by default; 306 tests green.

## Caveats
~2.5-week Chainlink sample — the AGREE gate is mechanistic (0 free params); max_ask/adverse_vel
are simple, monotone-ish knobs; future-block + CPCV + latency all hold, but the live forward-run
remains the real arbiter. Keep `det_d12_wide_live` as the one-flag backup.
