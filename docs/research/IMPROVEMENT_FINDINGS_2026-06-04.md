# Improvement, Robustness & Live-Readiness — Findings + Recommendation
**2026-06-04.** Full clean window 2026-05-23 → 06-04 (13 days, fresh Coinbase-WS spot). Two validation regimes on the full window (chronological walk-forward = realism gate; Combinatorial Purged CV = robustness/PBO). Pre-registered, every test logged in [`test_ledger.md`](test_ledger.md).

---

## TL;DR — honest verdict

1. **Determinism (`det_lwd`) is real and robust — deploy it first, small.** Passes every generalization test (9/9 walk-forward days, 15/15 CPCV folds, PBO 0.107, all cost/latency stress, all 4 coins, smooth plateau) and **survives realistic worse fills** (+$0.59/tr on the freshest OOS even under worst-case fill). It is *small* (~$1/tr net) and rests on only 13 days, but it is the trustworthy edge. → **$100 live probe.**
2. **Stale-quote (`det_sqp`) is real but fragile — keep on paper.** OOS-positive but high-variance, **execution-fragile on 3 axes**, and its **frozen probability curve has drifted** (over-predicts on June data). Don't risk real money on it yet; fix the curve and trade only the filtered v2 variant.
3. **One genuinely new edge found and verified: E4 "disagreement-determinism."** Buy the cheap spot-implied side in the last 60s when the book still has the *wrong* favourite: +$13–36/tr OOS, 84% WR, all splits, all coins. **Add to paper, fast-track.**
4. **Three confirmed improvements:** the v2 loss filters (OOS-confirmed; sq-v2 is the single biggest lift), a det⊕sq **ensemble** (anti-correlated → Sharpe 1.4→**2.0**), and a **rolling sq curve** (the frozen one is stale).
5. **Honest negatives (so we don't fool ourselves):** the sq "cheap-zone floor" is refuted, ML meta-labeling doesn't beat the simple filters, mean-reversion is dead (6th confirmation), and 3 of 6 new-edge ideas are dead/untestable.

**The biggest gap between our testing and live is execution, not the strategy** — and we've now quantified it (below).

---

## 1. The two existing edges

### Determinism `det_lwd` — robust, low-variance, SMALL
Last 60s, |spot−strike|≥5bps, book favourite agrees with spot, buy favourite (ask 0.50–0.90), hold to resolution.

| Test | Result |
|---|---|
| Full window | n=626, 87.7% WR, **+$1.23/tr**, total +$773 |
| Fresh OOS (06-01..04) | 84.8% WR, **+$0.89/tr**, CI[+0.30,+1.46] (>0) |
| Walk-forward | **9/9 days positive** (mean +$1.36) |
| CPCV | **15/15 folds positive** (+$1.24, p5 +0.87) |
| PBO (overfit prob.) | **0.107 — low** |
| Cost-stress (ALL combined) | +$0.88/tr CI[+0.50,+1.24] — **survives** |
| Per-coin (LOCO) | all 4 positive (SOL weakest) |
| Parameter plateau | smooth/monotonic — **no knife-edge** |
| Worst-case drawdown | ~**−$4** (block-bootstrap) |
| Deflated Sharpe (n_trials 36→1000) | 0.996 → 0.94 |
| **A2 execution** | survives worst-case fill (+$0.59/tr future, CI>0), scales to **$50/trade**, 10s-latency-robust |

**Caveats (honest):** per-trade edge is *small*; the strict calibrated-market reality-check is **marginal (p=0.054)**; track record (13d) is right at the statistical minimum (minTRL ≈14d). → Real and robust, but size small and don't over-extrapolate.

### Stale-quote `det_sqp` — real, high-variance, fragile, drifted
Mid-window, model_p (frozen curve) vs mid mispriced by [0.08,0.30] + spot jump ≥8bps, buy underpriced side, hold.

| Test | Result |
|---|---|
| Full window | n=2119, 47.5% WR, +$1.63/tr, total +$3452 |
| Fresh OOS | 44.0% WR, +$1.20/tr CI[+0.40,+2.03] (>0) |
| Walk-forward (curve refit per fold) | 7/9 days positive, pooled +$1.33 CI[+0.68,+2.00] |
| CPCV (curve refit) | 15/15 folds positive, +$1.80 CI[+1.22,+2.43] |
| Deflated Sharpe (n_trials 36→1000) | 0.95 → **0.65** |
| minTRL @ high trial-count | **~211 days** (we have 13) |
| Tail concentration | top-20 winners = 64% of profit |
| **A2 execution** | adverse fill keeps only 43% → +$0.51/tr CI **crosses 0**; size cap ~**$25**; needs **sub-5s** latency; **maker = −$1.99/tr** (must take, never rest) |
| **A3 curve drift** | frozen curve **over-predicts Up by 5.6pp** on June data (vol 4×, DOWN tape); calibration error 8.4× dev |

→ Generalizes positive but is **not statistically established** and is **execution-fragile**. Keep on paper; fix the curve; trade only v2.

---

## 2. Improvements found (OOS-confirmed)

| # | Improvement | Effect (fresh OOS) | Status |
|---|---|---|---|
| H2 | **sq v2 filter** (margin≥0.12 + dist≤19bps) | +$1.20 → **+$2.82/tr**, CI lower bound +0.41→**+1.50** | ✅ adopt (biggest single lift) |
| H1 | **det v2 filter** (ask≤0.88 + adverse_vel≤2; +crossings≥1 TODO) | +$0.89 → +$1.14/tr | ✅ adopt |
| E4 | **NEW edge: disagreement-determinism** | +$13–36/tr, 84% WR, 15/15 CPCV | ✅ add to paper |
| P1 | **det⊕sq ensemble** (anti-corr −0.28) | Sharpe 1.41/1.04 → **2.03** (risk-parity) | ✅ run combined book |
| A3 | **rolling sq curve** (replace frozen 05-23..29) | frozen is stale; 3-day refit tracks within 0.03 | ✅ required for live sq |

## 3. Honest negatives (logged, not hidden)

- **H3 — sq cheap-zone floor: REFUTED.** Full data shows ask 0.05–0.15 is the *highest*-EV zone (the jackpots). My earlier live-only "loser" read was a 6-day adverse-sample artifact. A floor *lowers* EV.
- **M1 — meta-labeling:** beats ungated but **does not beat the hand-crafted v2 filters** → keep it simple, no ML model.
- **H4 — daily cap:** a tight cap **costs sq 33–48%** of profit (truncates the positive skew); free safety for det. → det: $50 `hard_worstcase`; sq: no tight cap.
- **E1 lead-lag DEAD** (no capturable cross-coin lag even on fresh 1–2s spot). **E5 momentum DEAD.** **E3 oracle UNTESTABLE** (`chainlink_price` all-zeros — collector gap). **E2 order-flow / E6 cross-window INCONCLUSIVE** (real signals, don't clear cost/generalization).
- **A1 — mean-reversion:** all 26 retired configs net −$31.5k; refuted for sound causal reasons (not survivorship). Keep retired.

## 4. Paper → live gaps (what could change results live — quantified in A2)

| Gap | Determinism | Stale-quote | Mitigation |
|---|---|---|---|
| Realistic (worse) fills | keeps 66% (+$0.59/tr, CI>0) | keeps 43% (+$0.51/tr, CI crosses 0) | budget to ~70–80% of paper EV |
| Latency | robust to 10s | **needs <5s** (10s → edge halves) | abort if book >5s stale |
| Capacity | scales to ~$50/tr | **cap ~$25/tr** ($100 → 22% unfilled) | size small |
| Maker vs taker | n/a | **maker = −$1.99/tr** (adverse selection) | always TAKE, never rest |
| Spot-feed parity | edge dies on stale spot | same | use identical fresh WS feed |
| Curve drift | n/a | **active drift now** | rolling re-fit |
| Paper-engine fill model | fills at quoted best-ask (optimistic) | same | the realistic number is +1c/skip-L1 |

**The only way to close these is a tiny real-money probe** — more paper just re-confirms the same simulated best-ask fills.

---

## 5. Recommendation — what to do now

1. **Go live, small, with determinism.** Build the minimal order path (templated on the working `elon-tweets` executor), run a **$100 probe** at $10/trade on `det_lwd_v2`, with a `hard_worstcase` $50/day cap and a KILL switch. Purpose = **measure the execution gap** (fill rate, slippage, latency, live-vs-paper WR), not profit. Take liquidity (FAK), hook into the fast 1Hz path (the last-60s entry can't wait for a 5-min poll).
2. **Add E4 (disagreement-determinism) to paper** and fast-track it — it shares the determinism mechanism and is a strong, verified extension; just capacity-limited.
3. **Adopt the v2 filters** (det v2 + sq v2) as the traded variants.
4. **Fix the stale-quote curve** (rolling 3–7-day re-fit + base-rate recalibration) and **keep sq on paper** until it's re-validated on the rolling map and has more track record.
5. **Run the combined det⊕sq book** (risk-parity weights) for the better Sharpe — but inherit sq's "paper-only" status until it's fixed.
6. Optionally add **E2/E6 as paper experiments** to gather more data; **fix the chainlink collector** to enable the E3 test later.

### Concrete next actions (need your go — they touch the running bot / real money)
- **Paper:** add `det_disagree_v1` (E4) + finish `det_lwd_v2` (crossings gate) + switch sq to a rolling curve, in `strategies.yaml`, then restart. *(modifies the live paper bot)*
- **Live:** build `src/mean_reversion_live/live/` (reuse elon-tweets `clob_trade`/`guards`/`portfolio`/`reconciler`) + a det signal→order bridge, run the $100 probe. *(real money — needs wallet env + allowance confirmation)*
