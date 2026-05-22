# Polymarket Mean-Reversion — Final Research Report

**Date:** 2026-05-22
**Branch:** `edge-research`
**Scope:** A from-scratch, physics-first investigation into whether a robust,
profitable trading strategy exists on Polymarket 15-minute crypto Up/Down
markets — replacing the earlier sweep-and-deploy approach that lost money on
every paper strategy.

---

## 1. Bottom line

**No profitable strategy was found.** Every directional approach — the user's
manual buy-the-dip strategy, its conditioned variants, a spot-vs-market
divergence signal — and market-making were tested rigorously on corrected data
and are **not profitable** after costs. This is a genuine, honest negative, not
a tuning failure.

**Three would-be "edges" were discovered to be data artifacts** and caught *in
research, before any real money* — exactly the failure mode that sank the
previous effort. The old "sweep 1000s of configs → pick the winner → deploy"
process would have deployed all three.

The engagement's deliverables are real and valuable even though the headline is
negative: two live-bot bugs found and one fixed, a corrected dataset, a richer
data feed, and a rigorous reusable analysis pipeline — and, most importantly, an
honest answer instead of a comfortable illusion.

---

## 2. The data bugs (the core story)

The previous project's "validated edge" and every subsequent positive result
traced back to **corrupted data**, not real market inefficiency:

1. **March order-book corruption.** The March 16–17 tick data — the exact data
   the original 1000-config "Bonferroni-validated" backtest (`BACKTEST_VERDICT.md`)
   was built on — has the order book inverted: recorded `bid > ask` in 83–88% of
   ticks. The simulator buys at the ask and sells at the bid, so it mechanically
   "bought low, sold high" — ≈$2 of fake PnL per $10 trade before any signal.
   That alone produced the 88–93% backtest win rates. All of March is quarantined.
   `BACKTEST_VERDICT.md` is **invalid**.

2. **The strike bug (live bot).** `discovery.py` discovered each market ~30 min
   early and froze its strike (`start_price`) as the spot price *30 minutes
   before the window opened*. This corrupted `move_pct`, `outcome`, and every
   derived label for **all** May data — the outcome labels were wrong on **31%
   of windows**. **This bug is now fixed**; the bot records the strike at
   window-open and is collecting correct data going forward.

3. Two intermediate "edges" — a +12.5¢ calibration gap and a +$6/trade
   ($1,650/day) spot-divergence signal — were both **artifacts of the strike
   bug**. With correct labels (the real Polymarket-resolved outcomes, fetched
   from their API for 100% of May windows) they vanish entirely.

4. A +$1.83/trade "patient policy" result was a fourth artifact — a simulator
   bug pricing exits as `1 − cheap_ask`, which on decided-market books inverts a
   worthless losing position into a phantom ~$0.97 sale. Priced honestly: −$2.19.

The methodology that caught all of this: audit the data and the simulator
*before* trusting any result; treat every positive result as guilty until
forensically proven innocent; use the real resolved outcomes as ground truth.

---

## 3. The findings — there is no edge

All analyses below were run on the **corrected** dataset (real Polymarket
outcomes, corrected strikes), development split only (May 15–20), with the
May 21–22 hold-out sealed and untouched.

| Question | Analysis | Honest result |
|---|---|---|
| Is the market mispriced? | Phase 2 — calibration | **No.** Well-calibrated; −0.9¢. |
| Do odds bounce after a drop? | Phase 3 — drop event study | **No.** Odds continue *down* (−6.8% by +5min). Sell-the-bounce loses −$2.20/trade — worse than random entry. |
| Is the user's patient policy profitable? | Phase 4 — reconstruction | **No.** −$2.19/trade, honestly priced. |
| Can market-making work here? | Feasibility study | **No-go.** Spread captured ~1¢ < adverse selection ~2.25¢; net ≈ −$0.75/round-trip; inventory into a binary is unhedgeable. |

Three independent angles — static pricing, dynamic post-drop behaviour, and a
faithful policy simulation — **all agree**: buying the cheap side of these
markets, however conditioned or timed, does not beat the cost of trading.

A note on what *was* briefly real-looking: σ-proximity (a theoretical
"how-decided-is-this-market" feature) was found **broken** — markets it rates
4-sigma decided have the favourite winning only 64%, not 99.99%. The Gaussian
model fails on jumpy crypto. It was dropped.

---

## 4. The honest read on the 95% win rate

The user reported a ~95% win rate trading these markets manually, and that on
losing trades they "waited, and it got back to entry or close to it."

On an efficient market (which Phase 2 shows this is), that pattern — patiently
holding until you can exit near breakeven — produces a *feeling* of ~95% wins
because losses are rarely *realised*. But it is not an edge: the expected value
is ≈ zero, and it carries a catastrophic tail — the trade where the window
resolves before it "gets back" is a −100%. An efficient market plus
patient-breakeven-exits explains both the 95% memory and the paper bot's losses
with no contradiction.

This cannot be stated with certainty. The one input that could overturn it —
the user's actual historical trade records — was never available (the user had
"just the pattern"). Pinning real trades to the tick data is the only remaining
way to find a genuine, capturable discrepancy. Absent that, the user's described
pattern was reconstructed faithfully and it loses money.

---

## 5. Why there is no edge — the structural reason

The round-trip cost of trading these markets as a taker is **16–21% of the
stake** (Polymarket's crypto fee `0.07·p·(1−p)` per share on both legs, plus the
bid/ask spread crossed). A $10 patient taker therefore needs an edge of **>20%
per trade** just to break even.

That is an enormous edge to demand of any market. The calibration study shows
these markets are priced about right; there is no 20%-per-trade inefficiency to
harvest. Maker execution removes the fee but not the spread reality or adverse
selection, and market-making is independently a no-go. **The cost structure
itself makes this market category unviable for a small taker** unless a very
large, genuine mispricing exists — and, on this data, none does.

---

## 6. What was built (the assets that remain)

Even with a negative research outcome, the branch leaves the project materially
better off:

- **Live-bot strike bug fixed** — the bot now records correct strikes and is
  collecting trustworthy data.
- **Full-depth (L2) order-book capture added** — `data/live_l2/` now records the
  top 10 levels per side at 1 Hz; richer data accumulating for any future work.
- **Corrected dataset + reproducible pipeline** — `research/` contains a clean
  loader, feature library, dataset builders, and the full analysis suite
  (calibration, edge map, event study, policy simulator), all unit-tested
  (65 tests passing). Re-runnable as more data arrives.
- **A complete audit trail** — `docs/research/` documents every bug, every
  finding, and every verdict.

---

## 7. Recommendations

1. **Do not deploy real money** on the strategies tested. The paper bot already
   proved they lose; the corrected research confirms why.
2. **`BACKTEST_VERDICT.md` is invalid** — it is superseded by this report and
   should not be cited as evidence of an edge.
3. **The bot may keep running** purely as a *data collector* — it now records
   correct strikes and full L2 depth. After several weeks, the (already-built)
   pipeline can be re-run on a larger, richer sample. But set expectations low:
   the cost wall is structural, and the prior on finding an edge is now small.
4. **The highest-leverage missing input is the user's real manual trade
   records.** If the user can supply even 10–20 actual trades (date, market,
   entry/exit price), pinning them to the tick data is the only way to test
   whether the manual edge was real and capturable, or the breakeven-exit
   illusion. This was requested at the outset and remains the one open door.
5. **If pursuing prediction markets further**, the realistic options are: a
   genuinely different market category with lower costs; a non-taker structural
   role; or accepting that these specific 15m crypto markets are
   efficient-after-cost. Chasing a directional edge here is not supported by the
   evidence.

---

## 8. Where everything is

- This report: `docs/research/FINAL_REPORT.md`
- Phase 0 data/sim audit: `docs/research/phase0_audit.md`, `phase0_verdict.md`,
  `interim_code_audit.md`
- Corrected labels: `docs/research/corrected_labels.md`
- Phase 2 (calibration / edge map / divergence): `docs/research/edge_map.md`,
  `PHASE2_RERUN_VERDICT.md`, `divergence_edge.md`
- Phase 3 (drop event study): `docs/research/bounce_atlas.md`
- Phase 4 (policy reconstruction) + its forensic: `docs/research/reconstruction.md`,
  `phase4_forensics.md`
- Market-making: `docs/research/market_making_feasibility.md`
- Pre-registered hypotheses (H1–H11, all closed): `docs/research/market_hypotheses.md`
- Design + plans: `docs/superpowers/specs/`, `docs/superpowers/plans/`
- Analysis code: `research/` — pipeline (`lib/`, `features/`, `dataset/`,
  `analysis/`, `audit/`); tests in `tests/research/`.
