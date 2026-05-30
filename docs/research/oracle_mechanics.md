# Phase 1 — Late-window determinism pickoff  ⭐ FIRST POSITIVE, OOS-CONFIRMED

**Date:** 2026-05-29
**Code:** `research/analysis/oracle_mechanics.py`
**Data:** `joined_15m.parquet`, clean window. Dev = 2026-05-23..27 (1,920 windows);
sealed hold-out = 2026-05-28..29 (523 windows), opened ONCE for confirmation.
**Status:** A real, modest, out-of-sample-confirmed edge — the first in the project.
Carries to Phase 5 (full gauntlet) + forward paper before any live capital.

## The hypothesis (reframed after Phase 0a)

Settlement is the near-real-time Chainlink Data Stream, so this is NOT oracle
staleness. It is **late-window determinism**: in the final seconds, when spot
(Coinbase WS — a fast public proxy for the stream) is decisively away from the
strike, the outcome is near-locked, yet the thin Polymarket late-window book
keeps pricing the favourite below its realized win rate. A taker who buys the
favourite and HOLDS TO RESOLUTION (one-way cost only) captures the gap.

## The rule (locked on dev)

> In the **last 60 s** of a 15m window, if **|spot − strike| ≥ 5 bps** (and the
> favourite agrees with the side spot favours), and the favourite's taker ask is
> **≤ 0.90**, buy the favourite for $10 (walk the real L2) and hold to resolution.
> One trade per window.

## Results (net of taker fee + real book walk; `fills_v2`)

| split | n | WR | $/trade | 90% CI | ~$/day |
|---|---|---|---|---|---|
| **dev** (05-23..27) | 248 | 88.7% | **+$1.41** | [+0.92, +1.88] | ~$70 |
| **hold-out** (05-28..29, OOS) | 87 | 90.8% | **+$1.68** | **[+0.97, +2.39]** | ~$73 |

Stronger/thinner variant `dist≥10, ask≤0.90`: dev +$1.92 [+1.24,+2.55] (79 tr);
OOS +$2.04 [+1.12,+2.88] (36 tr), WR 94%.

## Why it is believable (the skeptic's checklist it passed)

- **Null-tested harness** (Gate 1 calibration slope 0.91; Gate 2 no manufactured
  EV; Null B no phantom edge) — `harness_v2.md`.
- **Latency-robust:** survives a 5 s action delay (+$1.73 at dist≥10), fill rate
  96–100%. The late-window book does NOT instantly reprice to 1.0 — the
  mispricing persists for seconds. This is the core evidence it is real.
- **Both-halves dev CV:** CI-positive in early (05-23..24) AND late (05-25..27).
- **Directionally consistent across all 4 symbols** (eth/sol CI-clean; btc/xrp
  positive point estimate, CI straddles at n≈75–117 — limited per-symbol power).
- **4 of 5 dev days positive** (one −$14 day).
- **OOS hold-out CI-positive** for the `ask≤0.90` family.
- **No look-ahead:** features (distance, time-left) are observable at decision
  time; label is the true future outcome; fills use the entry-second book.
- **book_healthy guard** removes the decided-market artifact that fooled prior work.

## Honest caveats (must be respected before live)

- **Short sample:** 7 clean days total. Forward paper on genuinely unseen data is
  required before sizing up.
- **Fat left tail:** ~9% of trades lose the full stake (−$10.2). Win ≈ +$2–3, lose
  ≈ −$10. A bad cluster of losses is possible; a daily-loss cap is mandatory.
- **Concentration:** dev total leans on a strong 05-27 (+$160 of ~$315), though
  the other 4 days net positive too. OOS (independent) reconfirms.
- **`ask≤0.95` is marginal** (OOS CI straddles 0). The robust rule is `ask≤0.90`.
- **Capacity:** final-60s depth (~$25–137) caps size near $10–50/trade.
- **Multiple testing:** ~20 dev configs were scanned; OOS confirmation on the
  locked rule mitigates, and the whole `ask≤0.90` family (not a lone cell) is
  strong. Phase 5 applies a formal correction.

## Mechanism (the inefficiency, stated plainly)

The Polymarket 15m book is thin and slow in the final minute. When public spot
has moved decisively from the strike with <60 s left, the favourite is genuinely
~91–94% to win, but resting asks remain ≤0.90. Lifting them and holding to
resolution earns the (true prob − ask − fee) gap. It is a microstructure
latency/liquidity edge against the late-window book, not a directional forecast.

## Next

1. **Phase 5 gauntlet:** formal multiple-testing correction; cost-stress (higher
   fee, +1 tick slippage, larger latency); per-regime; re-seal a larger hold-out
   as data grows.
2. **Forward paper:** add as a strategy (`strategies.yaml`) and watch on unseen
   future windows; compare live-paper vs this backtest (drift < 30%).
3. **Then** a small live test ($50–100, $10/trade, daily-loss cap).
4. Continue Phases 2–4 — there may be complementary edges (or this may be the one).
