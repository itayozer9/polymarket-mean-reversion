# Interim Code Audit — decision & validation logic

**Date:** 2026-05-22
**Status:** Interim — manual code review done during a tooling outage, *before*
Phase 0 execution. Phase 0 Tasks 5/7/8 will confirm the simulator-correctness
items with runnable checks; the validation-method items feed the research
report's diagnosis section.

**Scope reviewed:** `polymarket-arb/scripts/mean_reversion/{config,loaders,
features,signals,simulate,portfolio,sweep,validate}.py` and
`polymarket-mean-reversion/src/mean_reversion_live/engine/per_market_state.py`.

Severity tags: **BUG** (wrong result) · **STAT-BUG** (invalid statistics) ·
**WEAKNESS** (not wrong, but doesn't do what's claimed) · **LATENT** (dormant
bug) · **CONCERN** (modelling artifact) · **OK** (verified sound).

---

## Headline findings

### 1. BUG — the proximity filter never fires

`features.py::proximity_pct_from_move` (line 54) returns `|move_pct| / 100` — a
*fraction*. `signals.py::entry_signal` (line 107) rejects a tick only when
`proximity > proximity_max_pct`. Configs set `proximity_max_pct` as a *percent*
(`0.5` meaning 0.5%). `0.0019 > 0.5` is never true; the filter would require BTC
to move 50% inside a 15-minute window. **Every backtest and every live config ran
with no effective proximity filter** — the user's core "BTC near the strike" rule
was never tested. Confirmed at runtime by Phase 0 Task 5.

### 2. STAT-BUG — "Bonferroni validation" corrects the wrong axis

`validate.py::bonferroni` (lines 129–133) divides α by `k = len(p_values)`, and
`validate_top_configs` (lines 348–351) passes `k = 7` — the number of
cross-validation **splits** for one config. But the real multiple-testing problem
is the **~1000–3000 configs screened and ranked by PnL** (`top_k_configs`,
lines 275–279, sorts by `total_pnl`). The correction is applied to the wrong axis
and is too weak by **2–3 orders of magnitude**. This is the single statistical
error that let overfit configs earn a "Bonferroni-passing" label.

### 3. STAT-BUG — significance test treats correlated trades as independent

`validate.py::mann_whitney_vs_zero` (lines 111–126) runs a Wilcoxon signed-rank
test over **per-trade** PnL. Trades within the same day/window are correlated
(shared regime, overlapping windows), so the effective sample size is far below
the trade count → **p-values are inflated** (too significant). An honest
day-block bootstrap exists (`block_bootstrap_by_day`, lines 89–108) — but the
pass/fail gate (`bonferroni_pass_by_split`, line 349) uses the **inflated
Wilcoxon p**, not the bootstrap. Inflated, then under-corrected (see #2).

### 4. WEAKNESS — no walk-forward in time; "out-of-sample" isn't

`validate_top_configs` (line 282+) evaluates each config across `splits` that are
other **symbols / timeframes** (e.g. `btc_5m_test`, `eth_15m`). There is **no
split that is a later time period of the same market**. BTC/ETH/SOL/XRP are
~0.8+ correlated intraday, so "works on 4 symbols" is close to one test, not
four — and nothing tested whether an edge *persists forward in time*. The
March→May regime break (which killed the live configs) was therefore invisible
to validation.

---

## Simulator / fill-model findings

### 5. CONCERN — the parameter sweep used a more optimistic fill model than deployment

`sweep.py::_build_config` (line 143) always constructs `SimConfig(..., fill=
FillParams())` — i.e. the **defaults**: `realistic_fill_model=False`,
`reject_prob=0.03`. The legacy `realistic_fill_model=False` path (`simulate.py`
lines 206–212) fills the whole order at the *best* ask. The deployed
`strategies.yaml` configs use `realistic_fill_model: true` and
`reject_prob: 0.05–0.06`. **The sweep that "found" the winning configs ran with
cheaper fills and fewer rejects than even the deployed sim** — part of why sweep
PnL > live PnL before any regime effect.

### 6. WEAKNESS — the random-entry null is informational only, and mechanically inconsistent

`validate.py::random_entry_null` is a good idea — a baseline that keeps the exit
logic but enters at random times. But: (a) its result is stored
(`random_null_mean`) and never used in the pass/fail gate; (b)
`_random_entry_simulate` tracks the trailing-stop peak off the **mid**
(`_side_mid`, line 238) whereas the real `simulate_market` tracks it off the
**bid** (`simulate.py` line 118); (c) it skips `realistic_fill_model`,
`reject_prob`, reaction delay, and `signal_skip_prob`. So the null is both
un-gated and not a like-for-like comparison.

### 7. LATENT BUG — the live engine computes realized volatility on too short a buffer

`per_market_state.py` (line 134) sizes the tick buffer at
`_buf_size = cfg.entry.drop_window_sec + 5`. But `features.realized_vol_60s_from_move`
(features.py lines 70–85) looks back **60 ticks**. For any config with
`drop_window_sec < ~55`, the live engine computes `realized_vol_60s` over far
fewer than 60 ticks while the batch simulator (full array) uses 60 →
**`vol_bucket` can differ → entry decisions can differ between backtest and
live.** Currently dormant: the configs that actually use `vol_regime != ALL`
(e.g. `cfg_high_vol_wr`, the `cfg_max_pnl_*` family) all happen to have
`drop_window_sec ≥ 90`. **Fix:** `_buf_size = max(drop_window_sec, 60) + 5`.

### 8. CONCERN — cross-market portfolio state is market-ordered, not wall-clock-ordered

`simulate_market` is called once per market in `window_start_ts` order
(`loaders.iter_markets`), and each market is simulated start-to-finish before the
next begins. The shared `Portfolio` therefore evaluates `concurrent_position_cap`,
`daily_trade_cap`, and `post_loss_cooldown` in **market-start order, not true
wall-clock tick order**. Markets that overlap in real time (a 15m window every
few minutes) are simulated sequentially, so concurrency caps never reflect
reality and cooldowns are applied against out-of-order timestamps. The **live
bot** processes ticks in true arrival order — so the batch backtest and the live
bot diverge on anything cross-market, even though the per-market replay-parity
test passes. **Implication:** Phase 5–6 strategy backtests must simulate all
markets interleaved in wall-clock tick order, not market-by-market.

### 9. NOTE — volatility-regime thresholds are uncalibrated guesses

`features.py::vol_regime_thresholds` (lines 61–67) returns hardcoded constants
`(0.0005, 0.0015)` with the comment "calibrated later from data if needed" —
never done. Every `vol_regime` LOW/MED/HIGH filter and the
`cfg_high_vol_wr` "98.5% WR" claim rest on guessed cutoffs. Phase 1 will set
these from data quantiles.

---

## Verified sound

### 10. OK — no look-ahead leakage in the decision path

Checked and clean: entry fills use the tick reached *after* the reaction delay
(`simulate.py` lines 174–175, `delay_ticks = max(1, …)`); `rolling_max_drop` and
`realized_vol_60s_from_move` slice strictly `[lo : i+1]` (indices ≤ i); rolling
features look only backward; `exit_signal` uses the current tick's bid;
`forced_resolution` consults the true outcome only at
`seconds_into_window ≥ window_duration − 2`. **The bot's paper losses are genuine
strategy failure, not a backtest that cheated.** That matters: it means the edge
problem is real and must be solved with a real edge, not by fixing a leak.

---

## Live-engine findings (paper_engine / strategy / market_context)

### 11. BUG — the live engine settles forced-resolution at the last bid, not the true outcome

`paper_engine.py::_on_tick` (line 100) passes `outcome=None` into
`PerMarketState.on_tick` ("outcomes are resolved by the collector via
outcomes.csv; we don't pass it through here"). But `per_market_state.py::
_close_position` settles a `forced_resolution` exit at the true 0/1 payout only
`if reason == "forced_resolution" and outcome is not None`. With `outcome=None`
the live engine falls through to the `else` branch and settles at the **last
observed bid**. The batch simulator and the replay-parity test *do* pass the
outcome, so they settle at the true 0/1.

Consequence: the parity test does not actually exercise the live
forced-resolution path. Near the close the last bid is usually ≈ the outcome, so
the divergence is second-order — but for a stale/thin book, or a last tick a few
seconds early, it is real and always in the same direction (a won held-position
is credited below 1.0, a lost one above 0.0 → the live engine understates the
win/loss spread on held-to-resolution trades). Captured for the bot
re-architecture deliverable; Phase 5–6 backtests must settle on the true outcome.

### 12. OK — MarketContext is observable-only and sound

`market_context.py` records per-symbol YES-mid and spot, emits
`n_symbols_dipping_5pct_60s` and per-symbol drop / realized vol. No strategy
consumes it as a filter (week-1 by design). It is clean, and is a ready-made
input for the Phase 2 cross-coin / macro analysis (hypothesis H7 in
`market_hypotheses.md`).

---

## What this means for the project

- Findings **1–4** are the post-mortem: the old pipeline could not have worked.
  Selection by raw PnL over thousands of configs, a significance test that
  inflates p-values, a multiple-testing correction aimed at the wrong axis, and
  no forward-in-time test. These belong in the research report's diagnosis.
- Findings **5–9** are corrections the new work must respect: realistic costs
  from line one (#5), gated and like-for-like null tests (#6), the live-buffer
  fix (#7), wall-clock-ordered portfolio simulation (#8), data-calibrated vol
  regimes (#9).
- Finding **10** is the reassuring one: there is no leak to hide behind. The
  research has to find a genuine edge — which is exactly the plan.

The physics-first approach (Phases 2–3: calibration + event study) sidesteps
#2–#6 and #8 entirely, because it *measures the market* with window-clustered
statistics instead of simulating a portfolio and ranking it. The simulator
findings (#5, #7, #8) matter when Phases 5–6 build and backtest actual
strategies — and are captured in the bot re-architecture deliverable.
