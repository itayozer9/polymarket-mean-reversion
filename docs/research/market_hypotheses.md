# Market-Behaviour Hypotheses — mined from the live diary + bot post-mortem

**Date:** 2026-05-22
**Status:** Pre-registered. The bot lost on every strategy, but its 111-hour
hourly diary (`data/diary.md`) is a detailed observational record of how these
markets behave. Each hypothesis below is stated *before* the analysis, with its
evidence and the Phase 2–3 test that will confirm or kill it. Pre-registering
them is itself an anti-overfitting measure: we commit to the test now.

---

## H1 — The loss tail is forced resolution

**Evidence.** ~20+ "correlated FR clusters" logged; repeated finding: *"the trio
bleeds ONLY when an FR cluster hits, otherwise breaks even"* (confirmed 5×). A
held position whose window resolves against it = −100%.

**Hypothesis.** Per-trade EV ≈ (many small profit-target wins ≈ +$1) − (rare
resolve-against losses ≈ −$10). The strategy is roughly flat *without* the tail;
the tail is the whole deficit.

**Test.** Event study: for entries in the band, measure the resolve-against rate
and its share of total PnL.

**Implication.** The edge is in *avoiding the resolve-against trades*, not in
entry selection — directly motivates the σ-proximity disaster exit.

## H2 — Noise-drop vs signal-drop

**Evidence.** FR clusters are described as *"directional crypto crash hit the
trio"*; *"the deep-dip filter buys dips that keep falling in a trend."*

**Hypothesis.** An odds drop *alone* does not predict reversion. Whether the
**spot moved with it** does: odds fell + spot flat → noise → reverts; odds fell
+ spot genuinely moved → signal → continues to $0.

**Test.** Event study split by `spot_move` over the drop window.

**Implication.** The entry filter must be "odds dropped, spot did not" —
the `spot_move` feature paired with σ-proximity.

## H3 — Trending regimes kill mean-reversion

**Evidence.** *"The registry is structurally DOWN-tilted and has no trend
protection — its worst case is a sustained up-move."* *"FR clusters revert when
the cause is transient, RECUR when the cause is a trend."*

**Hypothesis.** The mean-reversion edge is conditional on a non-trending
underlying; in a trend the dip you buy keeps going.

**Test.** Condition calibration / event study on a trend measure (underlying
momentum over the last N minutes; recurring-FR frequency).

**Implication.** A trend / regime filter is mandatory — without it the strategy
is most exposed exactly when it is most wrong.

## H4 — The edge and the losses both scale with volatility

**Evidence.** Four ASIA windows declined monotonically −$303 → −$225 → −$174 →
−$142 per hour while per-strategy win rates stayed flat — *"pure
market-volatility decay, NOT strategy adaptation."*

**Hypothesis.** Bounce magnitude *and* tail risk scale with realized vol; a
fixed-parameter strategy is progressively mistuned as vol drifts.

**Test.** Calibration / event study stratified by vol regime (data-calibrated,
not the hardcoded guesses).

**Implication.** Vol regime is a first-class conditioning variable; parameters
or trade frequency should track it.

## H5 — Big reversions are real, rare, and fat-tailed

**Evidence.** `late_panic` wins of +$25–$44 = 200–273% moves; *"XRP DOWN
0.24–0.30 → 0.89"* ≈ 270%; validated #1's three biggest wins all BTC DOWN
deep-dips of ~+100%. The user's manual memory of "20–500%" is corroborated.

**Hypothesis.** The bounce-size distribution is fat-tailed — a few huge winners,
many small ones.

**Test.** The bounce atlas — the full forward-return distribution, not the mean.

**Implication.** A small fixed profit target (15–25%) caps the fat right tail
while leaving the −100% left tail uncapped — the worst possible payoff shape.
The user's "exit +40–200%, be patient" is the correct instinct: let winners run.

## H6 — Coins are not interchangeable

**Evidence.** *"XRP triggers 6 of 11 clusters (55%)"*; *"XRP markets resolve to
extreme $0/$1 more often than BTC/ETH/SOL."* The May-17 proposal: *"SOL is the
weakest performer."* Diary: BTC most stable.

**Hypothesis.** XRP windows are more often genuinely *decided* (less
mean-reverting); SOL is noisiest; BTC most stable.

**Test.** Calibration computed per symbol.

**Implication.** Per-coin calibration; XRP may need exclusion or a tighter
filter. Confirms cross-symbol "validation" was never independent evidence.

## H7 — Cross-coin co-movement flags a macro (real) move

**Evidence.** FR clusters hit BTC/ETH/SOL/XRP simultaneously; `MarketContext`
already logs `n_symbols_dipping_5pct_60s`.

**Hypothesis.** 3+ coins dipping together = a macro move (signal-drop, do not
buy); one coin dipping alone = idiosyncratic (more likely noise, buyable).

**Test.** Condition reversion probability on the cross-coin dip count.

**Implication.** Cross-coin macro state is a real entry filter — the macro table
the Phase 2 plan builds.

## H8 — Deep dips are rare and roughly coin-flips — not the edge

**Evidence.** Validated #1 (band 0.075–0.125) *"idle 14 consecutive hours"*, and
*"when the strict deep-dip filter finally fires, it loses."*

**Hypothesis.** The 0.075–0.125 band is both too rare to matter and not where
the edge lives; the user's stated 0.10–0.30 band fires far more often and is the
real candidate zone.

**Test.** Calibration by entry-price bucket.

**Implication.** Do not chase deep dips; the moderate-underdog band is the
candidate. (The sweep drifted *into* the deep band precisely because it overfit.)

## H9 — A fixed profit target is the wrong exit primitive

**Evidence.** *"v2's profit_target=25% is worse than v1/v3's 15% — positions
held longer = more FR exposure"* — yet 15%-target wins are only ≈ +$1, far too
small to cover the tail.

**Hypothesis.** Low target → tiny wins that cannot cover the tail; high target →
misses, position lingers and eats the FR. A fixed % target cannot win.

**Test.** Bounce atlas — *where and when* reversion completes; compare
time-based and σ-based exits against % targets.

**Implication.** Exit logic must come from measured reversion timing plus the
σ-disaster exit — never a profit-target sweep.

## H10 — 5-minute markets are structurally dead

**Evidence.** `cfg_5m_control` reached −$8,000, *"≈ −$1,720/day,
regime-independent"*; *"5m windows rarely resolve at $0 — the price doesn't have
time to swing that far."* `BACKTEST_VERDICT`: 0/149 5m configs profitable.

**Hypothesis.** 5m gives too little time for the reversion to complete (and less
tail, but also far less edge).

**Test.** Run the calibration / event study on 5m as a negative control.

**Implication.** Focus 15m. 5m stays as the engine-honesty control.

## H11 — The losses are a genuine absence of edge, not an artifact

**Evidence.** Code audit (`interim_code_audit.md` #10): no look-ahead leakage.
Diary: losses are consistent across 111h, mean-reverting around a structural
rate, present on all ~20 strategies.

**Implication.** There is no leak or bug to "fix" into profitability. The
physics-first plan must find a *real, measured* edge — confirms the approach.

---

## Synthesis — why the bot is the *inverse* of the user's manual edge

The user's manual method: buy the moderate-underdog dip near the strike, **let
winners run to +40–200%**, and **be patient on losers** (wait for recovery, no
stop). The bot does the exact opposite on both axes:

- **Winners:** capped by a 15–25% profit target (H5, H9) — the fat right tail
  (H5) is given away.
- **Losers:** realised early by a stop-loss / short `max_hold`, *or* held blindly
  into a forced resolution (H1) with no information about whether recovery is
  still possible.

The user's patience works because they only hold when the market is still a
coin-flip and they exit winners late. The synthesis of H1–H9 is a single design
target: **enter in the moderate band on an idiosyncratic, non-trending,
spot-quiet dip (H2, H3, H6, H7, H8); let the winner run per the bounce atlas
(H5, H9); and abandon only when σ-proximity says the market is genuinely decided
(H1).** That is the user's strategy, made mechanical — and it is what Phases 2–5
are built to validate.
