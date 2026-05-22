# Phase 0 — Verdict & Sealed-Holdout Decision

This document synthesizes Tasks 3–8 of the Phase 0 data & simulator audit
(`docs/research/phase0_audit.md`) and the pre-Phase-0 manual code review
(`docs/research/interim_code_audit.md`). It states which data is trustworthy,
what every later phase must account for, where the sealed hold-out boundary is
drawn, and whether the project may proceed to Phase 1.

**One-line summary:** The data and the simulator are sound *only on May 15–22*.
March is corrupt and quarantined; `BACKTEST_VERDICT.md` is invalid; the old
"validated" edge was an encoding artifact. The simulator itself does not cheat —
the bot's live losses are genuine. **GO for Phase 1**, on May data only.

---

## 1. The headline finding

**The original "validated" edge does not exist.** `BACKTEST_VERDICT.md`'s
headline 1000-config Bonferroni-passing sweep ran on `BTC 15m, Mar 15–17` — and
~95% of that window is **corrupt order-book data** (Task 3b). On Mar 16–17 the
recorded `yes_best_bid` exceeds `yes_best_ask` in 83–88% of ticks. The simulator
buys at the ask and sells at the bid, so on the great majority of those ticks it
**bought low and sold high mechanically**, before any signal skill — a median
**≈$2.2 of fake PnL per $10 trade**. The 88–93% backtest win rates are an
artifact of that inversion, not a strategy edge. This is also the direct
explanation for why the live bot lost money: live ran on correctly-encoded May
data where the free edge does not exist.

`BACKTEST_VERDICT.md` is therefore **invalid** and must not be cited as
evidence of an edge. Its Bonferroni claim must be re-established from scratch on
May data alone (or discarded).

---

## 2. Every BUG and CONCERN, with impact on later phases

### BUGs (a wrong result — must be corrected or the affected work discarded)

- **BUG — March 16–17 order book is corrupt (Task 3b).** `yes_best_ask <
  yes_best_bid` in 83–88% of two-sided ticks; `spread_yes` is a genuine negative
  number. It is *not* a clean bid↔ask swap (swapping leaves ~15% of ticks
  mislabeled and yields a nonsensical 22¢ pseudo-spread). It cannot be
  normalized by any column permutation.
  *Impact:* Mar 16–17 is quarantined. No backtest, calibration, or event study
  may touch it. The loader (Task 3c) excludes all of March by default.

- **BUG — Mar 04–14 has no usable bid price (Task 3b).** `yes_best_bid` is
  pinned at the 0.01 floor in 95.8% of rows (100% of rows are ≤0.02). Round-trip
  trades cannot be priced — there is no exit price.
  *Impact:* All of March is quarantined for any round-trip / mean-reversion
  work. 15m markets do not even exist before Mar 15.

- **BUG — `BACKTEST_VERDICT.md`'s headline sweep ran on corrupt data (Task 3b).**
  See Section 1.
  *Impact:* `BACKTEST_VERDICT.md` is treated as invalid. The validation gauntlet
  (Phase 6) starts from zero on May data.

- **BUG — the proximity filter is permanently inert (Task 5, code-audit #1).**
  `features.proximity_pct_from_move` returns `|move_pct|/100` (a fraction) but
  `signals.entry_signal` compares it to `proximity_max_pct` (a percent). The
  comparison is e.g. `0.014 > 0.5` → always False; firing would require a >300%
  spot move inside 15 minutes.
  *Impact:* Every backtest and every live config ran with **no effective
  "near-the-strike" filter** — the user's core intuition was never tested.
  Phase 1's canonical dataset carries a *corrected* `proximity_pct` (= |move_pct|
  in percent) and a `sigma_proximity` feature. The broken arb function is left
  untouched on purpose (changing it would break the live bot's replay-parity
  test — out of scope for this work).

- **BUG (live engine) — forced-resolution settles at last bid, not true outcome
  (code-audit #11).** `paper_engine.py` passes `outcome=None` into
  `PerMarketState.on_tick`, so held-to-resolution trades settle at the last
  observed bid instead of the true 0/1 payout. The batch simulator and the
  replay-parity test *do* pass the outcome, so the parity test never exercises
  this path.
  *Impact:* Second-order for the audit (last bid ≈ outcome near close), but it
  systematically understates the win/loss spread on held trades. Phase 5–6
  backtests must settle on the true outcome. Captured for the bot
  re-architecture deliverable.

### STAT-BUGs (the old validation method was not valid — diagnosis only)

- **STAT-BUG — Bonferroni correction on the wrong axis (code-audit #2).** The
  correction divided α by the 7 cross-validation *splits*, not by the
  ~1000–3000 *configs screened and ranked by PnL*. Under-corrected by 2–3 orders
  of magnitude — this is what let overfit configs earn a "Bonferroni-passing"
  label.

- **STAT-BUG — significance test treats correlated trades as independent
  (code-audit #3).** A Wilcoxon test over per-trade PnL inflates significance
  because trades in the same day/window are correlated. An honest day-block
  bootstrap exists in the code but the pass/fail gate ignored it.

- **WEAKNESS — no walk-forward in time (code-audit #4).** "Out-of-sample" splits
  were other symbols/timeframes, all ~0.8+ correlated intraday — effectively one
  test, not four. Nothing checked whether an edge persists *forward in time*, so
  the March→May regime break was invisible.
  *Impact of all three:* Phase 6's validation gauntlet must correct on the
  config axis, use window-clustered / day-block statistics, and include a true
  forward-in-time hold-out. This is the reason for the sealed hold-out in
  Section 4. The physics-first Phases 2–3 sidestep these by *measuring* the
  market rather than ranking simulated portfolios.

### CONCERNs (modeling artifacts / limitations — later phases must account for them)

- **CONCERN — round-trip cost is 16–21% of stake for a TAKER (Task 6).** Verified
  Polymarket crypto fee = `shares × 0.07 × p × (1−p)`, taker-only. Median
  round-trip cost: BTC 16.4%, ETH 17.0%, SOL 19.5%, XRP 21.0% of a $10 stake
  (≈ half fees, half spread). Break-even win rate at a +50% profit target is
  **75.5%** against a ~50% base rate. Profit targets below ~25% are structurally
  impossible for a taker.
  *Impact:* This is the single largest cost lever — see Section 3. Phase 5 must
  use PT ≥ 50% (BTC/ETH) / ≥ 75% (SOL/XRP) for any taker strategy, and must
  model the maker path explicitly.

- **CONCERN — top-of-book only; walk-the-book cost is unmeasurable (Task 3,
  Task 6).** The schema carries exactly one depth level per side. For SOL/XRP
  (median ask depth ~$14) a $10 fill already consumes ~71% of the quoted depth;
  anything larger walks an invisible book. True capacity beyond the best level
  cannot be measured, only assumed.
  *Impact:* Phase 5 sizing must treat top-of-book depth as a hard cap, not a
  ceiling to work up to. SOL/XRP $10 fills routinely trigger the simulator's 2¢
  depth-walk slippage penalty.

- **CONCERN — ~87% of May ticks are stale (Task 3).** At 1 Hz sampling the book
  is frozen at the same four prices for ~9 ticks in 10 — real microstructure of
  a thin, infrequently-quoted binary market, not a collector bug.
  *Impact:* Per-second momentum signals carry no new information most ticks.
  Phase 2+ features must be computed on *change* events or tolerate long stale
  runs; tick count overstates the independent sample size.

- **CONCERN — 13.4% (15m) / 13.6% (5m) of outcome slugs have no tick data
  (Task 4).** All cluster on May 15–16, the collector's first two days
  (incremental market discovery at startup). Not corruption — a startup gap.
  *Impact:* ~87% of the May outcome set has full tick coverage. Effective
  sample size on May 15–16 is reduced; analyses should expect thinner coverage
  on those two days. The gap shrinks as live data accumulates.

- **CONCERN — `chainlink_price` is never populated (Task 4).** The field is 0.0
  in 100% of May rows. The resolution oracle is **`coinbase_price`** (94.69%
  agreement with recorded outcomes; the residual ~5% is the structural
  last-tick-vs-settlement-instant gap).
  *Impact:* Every fair-value calculation in Phase 2+ must use `coinbase_price`.
  Any code that gates on `chainlink_price > 0` is dead code.

- **CONCERN — 2,157 slugs have conflicting outcome rows (Task 4).** Different
  strategies close the same window at slightly different instants. Each *row* is
  internally consistent (0 mismatches in 65,763 rows). Deduplicate by
  `market_slug` (keep first) → 10,255 canonical outcomes.
  *Impact:* Mechanical — handled by dedup in the loader.

- **CONCERN — March depth is 100–1000× May and not unit-reconcilable (Task 3b).**
  Cannot be distinguished from the data alone (liquidity-regime difference vs
  cumulative-vs-top-of-book). Moot given March is quarantined, but: never feed
  March depth to a shared fill model.

- **CONCERN — the old sweep used a more optimistic fill model than deployment
  (code-audit #5).** `sweep.py` always built configs with `FillParams()`
  defaults (`realistic_fill_model=False`, `reject_prob=0.03`); the deployed
  `strategies.yaml` used `realistic_fill_model=True`, `reject_prob=0.05–0.06`.
  *Impact:* Part of why sweep PnL exceeded live PnL even before the regime
  effect. Phase 5 must backtest with realistic fills from line one.

- **CONCERN — random-entry null is un-gated and not like-for-like
  (code-audit #6).** A good baseline idea, but its result never entered the
  pass/fail gate and it tracked the trailing stop off the mid (vs the bid in the
  real sim) and skipped reject/delay logic.
  *Impact:* Phase 6 must gate against a like-for-like null.

- **LATENT BUG — live realized-vol buffer is too short (code-audit #7).**
  `per_market_state.py` sizes the buffer at `drop_window_sec + 5` but
  `realized_vol_60s_from_move` looks back 60 ticks. Dormant only because every
  config that uses `vol_regime != ALL` happens to have `drop_window_sec ≥ 90`.
  *Impact:* Fix to `max(drop_window_sec, 60) + 5` before any vol-regime strategy
  ships. Captured for the bot re-architecture deliverable.

- **CONCERN — cross-market portfolio state is market-ordered, not
  wall-clock-ordered (code-audit #8).** The batch simulator runs one market
  start-to-finish before the next, so `concurrent_position_cap`,
  `daily_trade_cap`, and `post_loss_cooldown` are evaluated out of true time
  order; the live bot processes ticks in arrival order.
  *Impact:* Phase 5–6 backtests must interleave all markets in wall-clock tick
  order, or cross-market caps will not match live.

- **CONCERN — volatility-regime thresholds are uncalibrated guesses
  (code-audit #9).** `vol_regime_thresholds` returns hardcoded `(0.0005,
  0.0015)` with a "calibrate later" comment that was never honored.
  *Impact:* Phase 1/2 must set these from May data quantiles. Until then, every
  LOW/MED/HIGH vol claim (e.g. `cfg_high_vol_wr`'s "98.5% WR") is unsupported.

### Verified sound (no action needed — these are the reassuring findings)

- **OK — no look-ahead leakage in the decision path (Task 7, code-audit #10).**
  Three runnable assertions pass: the reaction delay guarantees fill tick >
  signal tick; `rolling_max_drop` and `realized_vol_60s_from_move` slice
  strictly `[lo:i+1]`. All five checklist items (exit uses current bid,
  forced-resolution consults outcome only at window end, no window-global stats
  in feature precompute, entry uses only causal features, peak-mid is a causal
  running max) are CLEAN. **The bot's paper losses are genuine strategy failure,
  not a backtest that peeked.** The edge problem is real and must be solved with
  a real edge.

- **OK — the simulator's fills are honest vs the live tick stream (Task 8).**
  267/267 May trades with tick data reconcile to 100% with mean price
  discrepancy 0.00000 once the depth-walk fill model is applied. The 25
  non-reconcilable trades are a May-16 collector data gap, not a fill error.
  The simulator uses exactly the prices the live book quoted — no cherry-picking.

---

## 3. The taker-vs-maker cost lever (single largest controllable cost)

Polymarket's crypto fee `shares × 0.07 × p × (1−p)` is **taker-only**. Confirmed
against `docs.polymarket.com/trading/fees`: **makers pay zero fee and earn a
~20% rebate; only takers pay the 0.07 fee.**

Consequence:

- A **taker** round trip costs **~16–21% of a $10 stake** (Task 6): ~10–11% in
  fees + ~5–9% in crossed spread. Profit targets below ~25% are structurally
  impossible; a +50% PT needs a 75.5% win rate to break even.
- A **maker** round trip (post limit orders inside the spread, wait to be hit)
  costs **≈0** on the fee side and earns a rebate — at the price of fill
  uncertainty and adverse selection.

**Taker vs maker is therefore the single largest cost lever in the entire
project — worth ~16–21% of stake per trade, larger than any plausible signal
edge.** A strategy that is unprofitable as a taker can be profitable as a maker
purely on the cost delta. Every later phase must model **both** execution modes
explicitly (the spec now treats this as a first-class cost lever); a strategy is
not "validated" until its assumed execution mode and its realistic fill
probability in that mode are both stated. This does not mean "assume maker fills
for free" — maker fills are uncertain and Phase 2–5 must estimate fill
probability from the book — but it does mean a taker-cost result is a worst case,
not the only case.

---

## 4. Data trust map

Which (symbol, timeframe, date-range) cells are usable for strategy research:

| Date range   | Timeframes | Symbols       | Status      | Notes |
|--------------|-----------|----------------|-------------|-------|
| Mar 04–14    | 5m only   | btc/eth/sol/xrp| **QUARANTINED** | `yes_best_bid` pinned at 0.01 — no exit price. No 15m markets exist. |
| Mar 15       | 5m + 15m  | btc/eth/sol/xrp| **QUARANTINED** | Transitional/changeover day; only ~1,100 ticks — negligible even if encoding were trusted. |
| Mar 16–17    | 5m + 15m  | btc/eth/sol/xrp| **QUARANTINED** | Corrupt book: ask < bid in 83–88% of ticks; not recoverable by any column permutation. |
| Mar 18–May 14| —         | —              | **ABSENT**  | No data collected in this ~8-week gap. |
| **May 15–16**| 5m + 15m  | btc/eth/sol/xrp| **USABLE (caveat)** | Correctly encoded, but ~13% of outcome slugs have no tick data — collector startup gap. Reduced effective sample. |
| **May 17–22**| 5m + 15m  | btc/eth/sol/xrp| **USABLE**  | Correctly encoded (ask ≥ bid 96–98%, +0.01 median spread). Full tick coverage. The clean core. |

**Net: only May 15–22 (~8 days) is admissible.** May 17–22 is the clean core;
May 15–16 is usable with the coverage caveat. The research loader (Task 3c)
enforces this — it quarantines everything before `2026-05-15` by default.

Cross-cutting caveats that apply to *all* usable cells:
- **Top-of-book only** — no level-2 book; walk-the-book cost is an assumption.
- **~87% stale ticks** — the independent sample is far smaller than the tick
  count; treat per-second momentum with suspicion.
- **Resolution oracle is `coinbase_price`** — `chainlink_price` is always 0.0.
- **SOL/XRP depth ~$14–20** — $10 is already a large fill there.

---

## 5. Sealed hold-out decision

Because March is quarantined, only ~8 clean days of May data exist. A
forward-in-time hold-out is mandatory (the absence of one is code-audit
finding #4 — the reason the old validation failed). With only 8 days the
hold-out is necessarily small; it is re-sealed larger on every weekly re-run as
the bot collects more data.

**Recommended split (written into `research/holdout.py`):**

- **DEVELOPMENT:** `2026-05-15` .. `2026-05-20` (6 days — all fitting,
  calibration, feature selection, strategy construction)
- **SEALED HOLD-OUT:** `2026-05-21` .. `2026-05-22` (2 days — opened exactly
  once, at the end of Phase 6)

The hold-out boundary is a one-way door. No Phase 2+ analysis may fit on or
select against the hold-out dates. `research/holdout.py::is_holdout(date_str)`
is the single source of truth.

---

## 6. Go / No-Go

**GO for Phase 1 (build the canonical dataset).**

Rationale:

- The data is **trustworthy on May 15–22** and the quarantine is enforced in
  code (Task 3c) — no later task can accidentally build on corrupt March data.
- The simulator **does not cheat**: no look-ahead (Task 7) and honest fills
  (Task 8). The bot's losses are genuine strategy failure — there is no leak to
  hide behind, which means a real edge, if found, will be real.
- The resolution oracle is **identified** (`coinbase_price`) — fair-value work
  can proceed.
- Costs are **quantified** (16–21% taker round trip) and the taker/maker lever
  is understood — Phase 5 strategy construction has a concrete hurdle to clear.
- Every known statistical flaw of the old pipeline is **documented** and the
  physics-first Phases 2–3 are designed to sidestep them.

Conditions carried into Phase 1 and beyond:

1. Phase 1 builds on **May 15–22 only**; the loader default must not be
   overridden.
2. The canonical dataset must carry the **corrected** proximity (in percent) and
   `sigma_proximity` — never the inert arb proximity.
3. Volatility-regime thresholds must be **re-derived from May data quantiles**
   before any vol-regime strategy is trusted.
4. Phase 5–6 backtests must: use realistic taker *and* maker fill models,
   interleave markets in wall-clock order, settle forced-resolution on the true
   outcome, and gate against a like-for-like null.
5. Phase 6 must correct for multiple testing on the **config axis** and validate
   on the **sealed forward-in-time hold-out**.

`BACKTEST_VERDICT.md` is **invalid** and is superseded by this document. Any
future edge claim must be re-established from scratch on May data.
