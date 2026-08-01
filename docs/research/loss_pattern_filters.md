# Loss-pattern mining → robust loss-avoidance filters

**Date:** 2026-05-30 (updated same day to the full 8-day clean window 05-23..30;
hold-out widened to 05-28..30)
**Question (owner):** scan every losing trade per active strategy; find a
*robust, causal* pattern behind the losers and a filter that removes/minimises
them without overfitting. Consider time, day-of-week, pricing, prior resolution,
**other-crypto pricing**, and combinations.

**Code:** `research/analysis/loss_patterns.py` (ledger builder),
`research/analysis/loss_filter_eval.py` (dev → both-halves → sealed-holdout eval).
Re-runnable: `uv run python -m research.analysis.loss_patterns && uv run python -m research.analysis.loss_filter_eval`.

## Method (anti-overfit, same discipline as the edge hunt)

1. Build the **full per-trade ledger** (winners + losers) for each live edge by
   replaying its exact entry rule on `joined_15m.parquet` — one trade/window,
   real L2 fills (`fills_v2`), hold-to-resolution, TRUE Chainlink outcome. A
   "loser" here is the same loser the live bot books.
   - determinism (`det_lwd_v1`): last 60s, |spot−strike|≥5bps, buy favourite
     ask≤0.90. **dev n=246, WR 88.6%, 28 losers.**
   - stale-quote (`det_sqp_v1`): mid-window, |model_p−mid|≥0.08 + |spot jump|≥8bps.
     **dev n=655, WR 58.9%, 269 losers.**
2. Engineer only **causal, decision-time-observable** conditioners. Discovery on
   **DEV (05-23..27)** only.
3. A filter is kept only if its lift holds in **both dev halves** AND on the
   **sealed hold-out (05-28..29, opened once)** — window-clustered bootstrap CIs.
4. Triangulate against the **real live-bot trades** (`trades_detailed.jsonl`,
   fully independent of the backtest).

## What the losers told us

### Stale-quote (`det_sqp_v1`) — strong, triple-confirmed

The loss axis is **monotonic and causal**: the smallest model-vs-market
disagreements are noise, not edge, and trades taken when spot is already far from
strike are betting against a near-certain outcome the market has priced.

| dev slice | n | WR | EV/trade |
|---|---|---|---|
| `abs_mis` 0.079–0.11 (just over threshold) | 131 | 41% | **−$1.15** |
| `abs_mis` 0.34–0.81 (large) | 131 | 73% | +$15.0 |
| `dist` >19 bps (far from strike) | 131 | 46% | **−$1.42** |

**Filter S4 = raise margin to `abs_mis ≥ 0.12` AND drop `dist > 19 bps`:**

| split | baseline EV | S4 EV | S4 WR | n kept |
|---|---|---|---|---|
| dev | +$4.63 | **+$6.71** | 65% | 427/655 |
| dev early(23-24) | +$2.75 | +$4.64 | | |
| dev late(25-27) | +$5.61 | +$7.74 | | |
| **hold-out(28-30, 3 days)** | +$2.06 (CI[+0.19,+4.20]) tot $796 | **+$4.31 (CI[+1.74,+7.38]) tot $1043** | 53% | 242/387 |
| **live bot (real fills)** | +$1.28 | **+$5.12** | 56% | 57/111 |

Both halves up, hold-out up (and the CI lower bound lifts well **off zero** — the
edge becomes reliable, not knife-edge), and the live bot independently confirms
(+$1.28→+$5.12). On the full 8-day window the filter **also raises TOTAL profit**
($796→$1043 on hold-out) while removing ~35–40% of trades and shrinking the left
tail — i.e. it is not merely a per-trade rescaling.

### Determinism (`det_lwd_v1`) — modest but robust

Only 28 dev losers → too thin to slice by hour/symbol without overfitting. Two
**continuous, causal** levers survive:

- **Don't overpay the favourite** (`ask ≤ 0.88`, vs live 0.90): above 0.88 the
  cushion-to-cost is too thin. dev EV +1.37→+1.78, hold-out +1.68→**+2.09**.
- **Skip when spot is sprinting back toward strike** (`adverse_vel_10s ≤ 2 bps`,
  where adverse = −sign(dist)·spot_vel): the late-window "lock" is being undone in
  real time. dev EV +1.37→+1.55, hold-out +1.68→+1.83.

A third lever comes from the **creative "15m checkpoint" features** (owner's idea):

- **Require the window to have crossed the strike ≥1 time before entry**
  (`strike_crossings ≥ 1`). The 0-crossing windows are already-decided blowouts:
  the favourite is pinned at a high ask (thin edge) and the only downside is a
  sudden late reversal that costs the full $10. 0-crossing cell = WR 67%, **EV
  −$1.53** (n=15). Layered on D4 it improves BOTH splits consistently
  (dev +1.93→+2.17, hold-out +2.04→+2.17) — a robust, causal add.

**Filter D6 = `ask ≤ 0.88` AND `adverse_vel_10s ≤ 2` AND `strike_crossings ≥ 1`:**
dev EV **+2.17**, hold-out EV **+2.17**, WR 91% (both splits). Honest caveat: these
levers lift **EV/trade, WR and tail** but barely change **total** PnL — they drop
~35% of trades, most winners. Worth it because the edge is capacity-limited
(~$10–50/trade), so per-trade quality > count. DET live (n=30, 3 losers) is too
thin to confirm directly.

## What we RETIRED or could not test (honest negatives)

- **Cross-coin macro stress ("other crypto pricing").** A window-aggregate
  `n_coins_volatile` *looked* like a clean filter (dev/hold-out both improved
  dropping ≥3 coins hot). But that feature peeked at post-entry ticks. The
  **point-in-time** recompute (other symbols' |vel| at the exact entry second)
  shows **no robust monotonic effect** — non-monotonic, noisy cells (hold-out
  n_hot=3 → +$18 on n=13). **Retired as lookahead.** It remains a real-time hook
  (`MarketContext.n_symbols_dipping_5pct_60s`) worth a clean re-test later.
- **Prior resolution** (`prev_fav_lost`, same-symbol previous window's favourite
  lost): weak/neutral on hold-out — not recommended.
- **Day-of-week:** only 5 dev days (~1 obs/level). Untestable; do not filter on it.
- **Time-of-day:** with this little data the per-hour cells are too thin to act on.
- **Creative features on STALE-QUOTE (RSI, window high/low, strike crossings,
  realized-vol).** Code: `research/analysis/loss_features_creative.py`. None
  robustly beat S4. The real, causal signal is sq WR **degrades monotonically
  with realized vol** (low-vol 72% → high-vol 40%), but EV does **not** improve
  when you cut high-vol windows — sq's payoff is right-skewed, so the big winners
  live in the same vol bucket as the losers. Excluding high-vol / the RSI 45–55
  "chop" zone *raised hold-out EV but LOWERED dev EV* (the signature of noise, not
  edge) and cut total profit. So: a **variance/WR lever**, not free EV — not a
  default. (For DET the same family produced the `strike_crossings≥1` win above —
  it helped there because DET is binary-payoff, not skewed.)

## Recommendation (NOT auto-applied — surfaced for owner)

Propose two filtered v2 strategies, run them **in parallel paper** alongside the
current four so live forward data adjudicates:

- `det_sqp_v2` = `det_sqp_v1` + **margin 0.08→0.12** + **skip dist>19bps**.
  (Strongest result: dev + both-halves + 3-day hold-out + live bot all agree;
  raises EV/trade ~2× AND total profit.)
- `det_lwd_v2` = `det_lwd_v1` + **max_ask 0.90→0.88** + **skip adverse_vel_10s>2bps**
  + **require strike_crossings≥1**. (Modest EV/WR/tail improvement; the
  crossings≥1 lever is the owner's "15m checkpoint" idea, OOS-confirmed.)

Keep v1 enabled as the unfiltered control. Re-check at the 2026-06-05 review on
fresh OOS days. Caveats: 8 clean days only, wide CIs, fat left tail on sq;
margin/max_ask are plain config params but the dist-cap, adverse-velocity and
strike-crossings gates need small additive engine checks (parity-safe, unit-tested).
