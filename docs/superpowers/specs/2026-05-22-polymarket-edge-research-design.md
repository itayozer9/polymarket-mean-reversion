# Polymarket Mean-Reversion — Edge Research Design

**Date:** 2026-05-22
**Status:** Approved — Phase 0–1 plan written; revised 2026-05-22 after a critical
self-review (added: resolution-oracle check, lead–lag study, edge-map dev-internal
CV, feature-importance diagnostic, σ-proximity disaster exit, top-of-book data
limitation, regime-gap honesty)
**Approach:** A (Physics-first), fresh start, no commitment to existing strategies

---

## 1. Why this exists

The project ran a 24/7 paper trader on Polymarket 15m crypto Up/Down markets for ~1 week.
**Every strategy lost money.** Live paper PnL since the 2026-05-17 restart:

| Strategy | Trades | Win rate | PnL |
|---|---|---|---|
| `cfg_21c8c00165b3` ("validated #1", 88% WR backtest) | 164 | 38% | −$345 |
| `cfg_333fde9cecb8` ("validated #2", 93% WR backtest) | 145 | 19% | −$382 |
| `cfg_max_pnl_v1` (backtest 92.6% WR, +$4962) | 2008 | 72% | −$2923 |
| `v2_gold_01..08` ("seed-stable 8/8") | 33–154 | 23–43% | −$62 to −$393 each |
| `relaxed_v1` | 1992 | 34% | −$4721 |

When a process produces ~20 candidates and all 20 fail, the parameters are not the
problem — the **method** is.

### Diagnosis of the old method

1. **Overfitting machine.** Sweep 1000 configs → pick top → "validated" → loses →
   sweep 3000 more → pick top → "GOLD" → loses. Each round fits noise harder.
2. **The "Bonferroni validation" corrects the wrong axis** (confirmed by code
   audit — see `docs/research/interim_code_audit.md`). `validate.py` divides α by
   the 7 cross-validation *splits* of one config — but the real multiple-testing
   problem is the ~1000–3000 configs *screened and ranked by PnL*. The correction
   is too weak by 2–3 orders of magnitude. And the pass/fail gate runs on a
   Wilcoxon p-value computed over *per-trade* PnL as if trades were independent
   (they are correlated within a day/window) — inflated, then under-corrected.
   Cross-symbol "out-of-sample" is not independent either: BTC/ETH/SOL/XRP are
   ~0.8+ correlated intraday, and there was no forward-in-time test at all.
3. **"Seed-stability 8/8" tests the wrong thing** — insensitivity to execution-noise
   RNG, not whether the edge is real.
4. **The optimizer deleted the user's actual edge.** The manual rule buys odds
   0.10–0.30 near the strike; the "validated" config buys 0.075–0.125 (deeper, worse)
   and several GOLD configs set `proximity_max_pct: 100` (proximity filter disabled).
   In a noisy backtest, more trades + bigger nominal bounces + luck beats a
   disciplined low-frequency rule.
5. **Payoff math ignored.** `cfg_max_pnl_v1` won 72% of trades and still lost $2923:
   buy at 0.10, win ≈ +$1.50, loss ≈ −$10. Break-even win rate is far above what was
   delivered once round-trip costs are included. (The `fee_rate: 0.07` is *not* a
   flat 7% — the formula is `0.07 × p × (1−p)` per share ≈ 0.6–1.7% of notional; the
   real cost driver is the bid/ask **spread**, which Phase 0 quantifies.)
6. **The phenomenon was never characterized.** Nobody measured what odds actually do
   after a drop before optimizing strategies on top of it.

### The user's manual strategy (ground truth, from GOAL.md + clarifying answers)

- Buys the temporarily-cheap side of a market that is **still fundamentally a
  coin-flip** (spot price near the strike), after a visible drop, with enough time
  left for resolution.
- **Patient.** On a trade going against them they *wait* — it "got back to entry or
  close to it." Takes profit on a bounce; exits near breakeven otherwise.
- **No stop-loss.** The bot's `stop_loss` (50–80%) and short `max_hold` (180–360s)
  force a realized loss exactly where the user would have waited — mechanically the
  opposite of the user's discipline.
- Claims ~95% win rate in real money. Honest caveat: patience has a hard deadline
  (the window resolves); the memory may under-count the times it did not get back
  before close (a −100% trade). Measuring that true rate is the single most
  important number in this project.
- **No time-of-day pattern** ("all over") — so the backtest's "ASIA hours win"
  finding is an overfit artifact, not the user's edge.

### Goal

Find **robust, profitable, executable** trading strategies on these markets —
the user's strategy and/or others — validated honestly enough to risk real money.

**Acceptance bar: daily profitability.** The target is not profit per trade but
profit *per day* — a deployable strategy (or portfolio of strategies) should be
green on the large majority of days, with small and bounded down-days. Daily PnL
consistency is a first-class objective, not net PnL alone. Caveat: requiring
strictly-positive on 100% of *in-sample* days is itself overfitting bait — a
strategy can be cherry-picked to fit that. The honest test is daily consistency
holding up on the walk-forward and leave-one-day-out folds and the sealed
hold-out, where it was never tuned.

---

## 2. Data inventory

- **Historical (March):** Mar 4–17 2026, ~14 days. BTC/ETH/SOL/XRP, 5m + 15m
  markets. 23-column 1Hz tick CSVs. `outcomes.csv`. Binance spot 5m candles (180d).
- **Live (May):** May 15–22 2026, ~8 days. Same 23-col ticks, plus `live_macro/`
  cross-symbol snapshots, a ~1GB `signals.jsonl` decision-funnel log, per-strategy
  `trades.jsonl`.
- **Total ~22 days across two regimes** (March "friendly", May "shallower bounces"),
  separated by an ~8-week collection gap (no data Mar 18 – May 14) — two regimes is
  an asset for robustness testing, but they are two samples, not one series.

Tick schema carries everything needed: top-of-book bid/ask + depth for yes & no,
`chainlink_price` / `coinbase_price` (spot), `start_price` (strike), `move_pct`,
mids, spreads. **Proximity to strike and a corrected spread-based fill model are
fully reconstructable from the data.**

---

## 3. Method — six phases

### Phase 0 — Audit: is the data and the simulator honest?

If any of these fails, every later number is fiction.

1. **Outcome correctness.** Recompute each window's resolution from spot at close;
   cross-check `outcomes.csv`. Map tick/outcome coverage per coin per day.
2. **Cost realism.** The sim already enters at the ask and exits at the bid, and
   charges `fee = 0.07 × p × (1−p)` per share. Verify against Polymarket's real 2026
   fee schedule whether that fee is correct (or zero), and quantify the true
   round-trip cost — fee **plus** the bid/ask spread crossed — in the entry-relevant
   odds band. The spread, not the fee, is the likely killer; this number is the
   hurdle every strategy must clear.
3. **Sim vs reality.** Reconcile a sample of recorded paper trades against the raw
   tick book — was each fill price actually available at that moment?
4. **Look-ahead leakage audit.** Read `features.py` / `signals.py` / `simulate.py`
   for any decision that peeks at a future tick.
5. **Tick data-quality report.** Sampling gaps, corrupt gzip tails, stale books
   (repeated quotes = WS gap / dead liquidity), crossed books, `yes_mid + no_mid`
   deviation from 1.0. The March 4–13 files are smaller — possibly a thinner early
   collection. Weight later statistics by where data is trustworthy.
6. **Executability / depth realism.** Record, at every entry candidate, the size
   actually resting at the quoted price. If books are routinely thinner than $10 the
   strategy is not executable at quote regardless of signal quality.
7. **Resolution oracle.** Determine which price feed Polymarket actually settles
   these markets on — `chainlink_price` or `coinbase_price` — by checking which
   feed's sign at window close best predicts the recorded `outcome`. The fair-value
   model and every outcome calculation must use the *resolution* feed, not an
   arbitrary spot.
8. **Known data limitation — top of book only.** The tick schema carries only the
   best bid/ask and the depth *at* that best level — no deeper book. Walk-the-book
   slippage and true capacity therefore cannot be measured exactly; the fill model's
   beyond-best assumptions must be flagged as assumptions, and collecting full-depth
   book data should be recommended for the live phase.

### Phase 1 — Canonical research dataset

One clean, reproducible per-window dataset spanning all 22 days. For every window:
every tick with implied prob (mid and bid/ask), spot, strike, time-left, book
depth/spread, underlying realized vol, and the final outcome. Derived features:

- **σ-proximity** — distance of spot from strike measured in standard-deviations of
  the underlying's expected remaining move (realized vol × √time-left), **not** raw
  %. This is the cleanest mechanical proxy for "is this still a coin-flip"; nothing
  in the current project uses it.
- Odds velocity; rolling odds drop over multiple windows; book imbalance; and
  **spot-move features** — the underlying's own signed move over matching windows,
  the direct ingredient for the noise-vs-signal split. (Cross-coin / macro state —
  "are all coins dropping together" — is built in the Phase 2 plan, where it is an
  analysis input; `data/live_macro/` already provides it for the May data.)
- **Noise-drop vs signal-drop label** — did odds fall while spot stayed put (noise →
  expect reversion) or while spot genuinely moved (signal → expect continuation).

All downstream analysis runs off this one dataset.

### Phase 2 — Calibration / fair-value study (centerpiece)

Is the market's implied probability an honest estimate of the true resolution
probability — and where is it wrong?

- **Reliability curve.** Bucket every state by implied prob; measure realized
  resolution frequency. Calibrated → no edge; biased → measurable ¢ edge.
- **Window-clustered.** Unit of analysis is the window, not the tick (all ticks in a
  window share one outcome). Window-level bootstrap CIs.
- **Conditioned edge map.** Re-run calibration within strata of time-left,
  σ-proximity, drop magnitude/speed, vol regime, symbol, hour, timeframe. Output: a
  heatmap of (implied prob × condition) → realized freq → ¢ edge → CI.
- **Model-based fair value.** Compute a theoretical Bachelier-style "P(spot ends
  above strike)" per tick. Triangulate market odds vs theoretical fair value vs
  empirical frequency — agreement in one direction = high-confidence mispricing.
- **Isotonic calibration fit** with window-clustered CV — smooth, overfit-resistant
  odds→true-probability mapping.
- **Favorite–longshot bias check.** If cheap longshots resolve YES *less* often than
  priced, "buy cheap and hold to resolution" is structurally losing, and the real
  edge must be selling the bounce *before* resolution. This decides which strategies
  are even possible.
- **Net-of-cost calibration.** Repeat using ask-to-enter / bid-to-exit.
- **Regime stability.** Compute the edge map separately for March and May and
  overlay. An edge present in *both* regimes is real; March-only is the old trap.
- **Cheap-side framing.** YES and NO sum to 1 — the same window-moment seen twice.
  The calibration is framed as *the side a dip-buyer would take* (the cheap side),
  so an edge is never double-counted. The reliability curve is also computed
  **unconditionally first** (all states, all prices) so non-dip edges — favourites,
  window-open mispricing, late-window drift — surface instead of being assumed away.
- **Dev-internal cross-validation of the edge map.** A cell is only eligible to
  become a strategy if its edge appears in *both* halves of a development-data split
  (and in both regimes). This makes the edge map itself robust, not just the final
  strategy — selecting *which* cells matter is the most dangerous step.
- **Lead–lag study (open-minded).** Cross-correlate the 1 Hz `coinbase_price`
  against the implied odds: do the Polymarket odds *lag* spot by a measurable delay?
  GOAL.md frames the edge as mean-reversion, not latency — but a systematic lag
  would be a separate, possibly cleaner edge, so it is checked explicitly rather
  than assumed absent.

### Phase 3 — Event study of odds drops (mean-reversion physics)

- **Drop events.** Detect every sharp odds drop (multiple X%/Y-sec thresholds); plot
  the average forward odds path at 30/60/120/300s and to window close.
- **Noise-drop vs signal-drop, conditioned.** Confirm or kill: noise-drops revert
  hard, signal-drops go to zero. If confirmed, the entry filter becomes "an odds
  drop *not* accompanied by a real spot move."
- **Bounce distribution.** Full distribution, not just the mean: how often it
  bounces, by how much, how fast, and the fraction that never recovers.
- **Patience-deadline interaction.** For an entry in the user's band, measure
  P(bounces to profit) vs P(recovers to breakeven in time) vs **P(time runs out
  underwater → −100%)**, as a function of time-left at entry.
- **Open-minded sweep.** Same machinery on spikes (is fading a rally an edge?) and
  late-window behavior.
- **Pre-registered hypotheses.** Phases 2–3 explicitly test the 11 hypotheses
  mined from the live bot's 111-hour diary in `docs/research/market_hypotheses.md`
  — loss-tail = forced resolution, noise-vs-signal drops, trend kills reversion,
  vol scaling, fat-tailed bounces, per-coin differences, cross-coin macro filter,
  deep-dips-aren't-the-edge. Committing to the tests now guards against fishing.

Section 2–3 deliverable: an **edge map + bounce atlas** — a documented, CI'd picture
of where these markets are exploitable, before a single strategy is written.

### Phase 4 — Reconstruct "you" (measurement, not optimization)

No real trade records exist, so the user's policy becomes an explicit documented
**model**, treated as a hypothesis to stress-test.

- **Policy:** entry in-band after a visible drop, spot near strike, enough time left;
  exit = take profit on a bounce, else hold patiently, exit near breakeven on
  recovery, **no stop-loss**, resolution is the only forced exit.
- Run across all 22 days; measure true win rate, PnL, average hold, and the
  **resolution-loss rate** — the direct test of the 95% memory.
- **Sensitivity mapping** — vary each assumption to draw the surface and find where
  the policy breaks (plateau = robust, spike = fragile). Not to maximize PnL.
- **PnL attribution** — decompose into overshoot-reversion, favorite–longshot bias
  (possibly negative), and selection.
- **Feature-importance diagnostic.** Train a deliberately *interpretable* model
  (logistic regression / shallow tree) to predict "does this dip recover within the
  window" — not to trade it, but to read off *which* features carry the signal. This
  reverse-engineers what the user's eye implicitly computes and reveals whether the
  rule-based reconstruction is missing a variable.
- **Discretion gap** — the model gets the spot path; genuine human chart-reading is
  acknowledged as not fully capturable and its possible contribution is bounded.

### Phase 5 — Build strategies (pre-registered, no sweep)

- Each strategy is a **hypothesis written down before testing**, traceable to a
  specific Phase 2–4 finding. No config without a measured reason.
- Few parameters, each pinned to a measured quantity.
- Diverse families: (1) refined patient mean-reversion (σ-proximity + noise-drop
  filter); (2) pure noise-drop fade; (3) favorite–longshot-aware variants (possibly
  *fading* cheap longshots); (4) any non-MR edge the data surfaces.
- Exit logic from the bounce atlas, not a profit-target sweep.
- **σ-proximity "disaster exit" — reconciling patience with the daily-green bar.**
  The user's no-stop patience is the edge, but it carries a rare −100% tail when a
  window resolves against an un-recovered position. Test an *information-based* exit
  (distinct from a price stop-loss, which the user rightly rejects): abandon a held
  position only when σ-proximity blows out — spot has genuinely run many σ from the
  strike with little time left, so the market is *decided* and the wait is futile.
  "Odds dropped but still a coin-flip → hold" vs "odds dropped and the market is now
  decided → exit" may be what keeps the high win rate *and* removes the
  account-denting day.
- Cost-aware from line one, using Phase 0's corrected fill model.
- **Designed for daily consistency** — prefer higher-frequency, lower-variance
  edges and/or a portfolio of decorrelated strategies so the *daily* PnL
  distribution is tight and rarely negative, per the acceptance bar.

### Phase 6 — Validation gauntlet

A strategy ships only if it clears **every** gate:

- **Frozen hold-out.** The final ~4–5 days (e.g. May 18–22) are sealed now; opened
  exactly once at the end. One shot, no re-tuning.
- **Three complementary out-of-sample tests** — all run *inside* the development
  data (the sealed hold-out stays untouched), and the split unit is **always a
  whole day or window, never an individual tick** (splitting ticks leaks the
  window outcome across train/test and inflates every result):
  1. **Walk-forward in time** — train on an earlier block, test on the next, roll
     forward. The only test that answers "will it work *going forward*"; mirrors
     live trading. Must be positive on a majority of forward folds.
  2. **Day-blocked randomized k-fold (80/20)** — randomly assign whole days to 5
     folds; train on 80%, test on the held-out 20%, rotate. Randomizing *which*
     days are held out tests robustness across *any* day, not just the most recent.
  3. **Leave-one-day-out** — train on 21 days, test on the 1 held-out day, rotate
     through all days, with a short embargo around the test day. Directly answers
     "profitable on every day it never trained on."
  Walk-forward is the *honest forward* test; k-fold and leave-one-out are
  *robustness* tests (they let a strategy see both regimes, so they cannot judge
  regime-change — only walk-forward can).
- **Cross-symbol = robustness, not evidence.** Measure actual correlation, discount
  significance accordingly.
- **Multiple-testing accounting.** Log every hypothesis; deflated Sharpe / explicit
  correction; report how many strategies would look this good by chance.
- **Null / placebo tests.** Shuffled outcomes and random entry times must yield ≈$0.
- **Cost stress.** ±50% on spread/fees/slippage and pessimistic fills.
- **Capacity check.** Using depth data — how many entries are actually fillable at
  $10, does the edge survive when restricted to fills with real resting size.

**Ship criteria (all required):** positive on the sealed hold-out · positive on a
majority of walk-forward folds · **green on the large majority of days across
walk-forward and leave-one-day-out testing, with bounded down-days** · survives
±50% cost stress · ≈$0 on null tests · edge present in both March and May regimes ·
CI excludes zero after multiple-testing correction.

---

## 4. Deliverables

1. Written research report — diagnosis, edge map, bounce atlas, user reconstruction,
   validated proposals, every claim sourced to a number.
2. Charts — calibration curves, bounce atlas, regime overlays, equity curves with
   CIs, and **per-day PnL distributions** (the daily-consistency acceptance bar).
3. Reproducible analysis pipeline — committed, re-runnable scripts.
4. Validated strategy proposals — each with written rationale and risk/return shape
   (user makes the robust-vs-flashy call with numbers in hand).
5. Bot / validation re-architecture recommendation — engine, fill model, and
   strategy-selection changes so this failure mode cannot recur.

---

## 5. Execution / agent teams

- **Phases 0–1** — sequential, run by the lead (must be exactly right).
- **Phases 2, 3, 4** — independent; three parallel agents, each bound by the
  sealed-holdout rule as a hard constraint.
- **Phase 5** — strategy construction by the lead after synthesis.
- **Phase 6** — validation dispatched per strategy family.
- **Checkpoints with the user:** after Phase 0 (is the data trustworthy?), after the
  edge map (Phases 2–3), and before opening the sealed hold-out.

---

## 6. Out of scope

- Real-money trading. Paper/research only until a strategy clears Phase 6.
- Re-using the existing strategies as-is. They are a fresh start; prior configs are
  reference material, not a baseline to beat.

## 7. Open risks

- The user's 95% win rate may be memory-biased; Phase 3/4 will test it honestly and
  the result may be that the edge is smaller or differently-shaped than believed.
- The favorite–longshot bias may be strong enough that cheap-side buying is
  structurally unprofitable — in which case the project pivots to selling bounces or
  to fading, per the data.
- 22 days is a limited sample, and the two regimes are separated by an ~8-week gap
  (no data Mar 18 – May 14) — they must be treated as two semi-independent samples,
  not one continuous series. The report will state confidence honestly.
- The bot keeps collecting live data daily; the canonical dataset build is
  re-runnable, and the analysis should be re-run on the extended sample before any
  real-money decision. More data is the cheapest way to raise confidence.
- Top-of-book-only tick data caps how precisely fills and capacity can be modelled
  (see Phase 0 item 8); real-money sizing must stay conservative until full-depth
  data is collected.
