# PORTFOLIO.md - Arming Doctrine and Risk Rails

Adopted 2026-08-08 (user-ratified consolidation plan). This file is the LAW for what gets
real money and how it scales. Every future session applies these rules mechanically; a
change to this file is a user decision, recorded in the ledger.

Context in one paragraph: after 9 weeks and $3,327 deployed, exactly one live book is
profitable and free of adverse selection (`fav_disagree_live`, ask 0.05-0.45). The 08-07
filled-vs-missed measurement closed the ask 0.45-0.90 band for taker execution (the book
reprices past our ceiling exactly when we are right; CI clear of zero), which is where 10
of the 17 paper strategies live. Therefore: coverage, execution quality, and scaling of
the proven cheap-band edge beat any further discovery. Full evidence: STATE.md 2026-08-07
and docs/research/test_ledger.md.

---

## 1. Arming rules (ALL required before any strategy gets `live: true`)

1. **Cheap band only.** Taker arming only for entries at ask <= 0.45. The 0.45-0.90 band
   is closed permanently for taker execution regardless of paper EV (filled-minus-missed
   gap CI clear of zero; hi_live -$26.64 lifetime; per-$ penalty -0.396 in 0.45-0.60).
2. **Coin decomposition.** Score per executable coin (the EXEC_SYMBOLS set plus any
   per-sid grants), CI per coin, before proposing. A pooled all-coin headline is not a
   promotion metric (the early_disagree 08-07 near-miss: pooled CI-lo>0 was carried
   entirely by a coin the executor does not trade).
3. **Additivity.** Jaccard overlap < 0.2 vs every armed book.
4. **Isolation.** Own $100 bankroll (loss-based balance) + `hard_worstcase` daily cap in
   executor_state.json v2. One book can never block or drain another.
5. **Stop rule registered at arming.** Kill when official-settled <= -$0.50/fill AND
   CI-hi < 0, OR book drawdown reaches -$50 from high-water. No book runs without one.
6. **Global caps** (live_executor): GLOBAL_MAX_CONCURRENT=3 reservations; ONE position
   per (window, direction) across all books and coins - the live coins move together on
   one spot move, so a same-direction sibling is leverage, not diversification.
7. **One calculator.** Every gate/rung read via `research/analysis/score_gates.py`,
   official on-chain labels only. Engine-tape P&L is never quoted (runs ~9.5x hot).
8. **User sign-off** for: first arming of a book, coin grants (EXEC_SYMBOLS_EXTRA), and
   any change to real-money execution semantics. NOT needed for ladder rungs (below).

## 2. The mechanical size ladder (fav_disagree_live; user-approved 2026-08-08)

- Rungs: **$10 -> $15 -> $25 -> $40**. Read every **14 days** after the last rung change
  (first: 2026-08-17, the pre-committed $15 read since 2026-07-03), window = since the
  last rung change (except the first read's registered window).
- **Rung up** iff ALL of: per-fill CI-lo > 0 AND per-$ CI-lo > 0 on the window,
  fill-rate >= 45%, and the recorded-book depth check supports the next size.
- **Rung down** one step immediately on any 14d read with EV < 0.
- **Kill**: -$50 drawdown from high-water, or the standing stop rule trips.
- Daily cap scales: $50/day at $10-15, $75/day at $25, $100/day at $40. Bankroll top-up
  from the wallet at each rung (capital is not the constraint; ~$675 available).
- Rung changes are executed mechanically with a notification to the user (status skill +
  STATE entry). Anomalies (rung-down, kill, fill-rate collapse) are surfaced immediately.

## 3. Research freeze (user-approved 2026-08-08)

After the August gate calendar closes (last date: 08-28), NO new discovery campaigns
until the portfolio is stable at the $25+ rung. Analysis of already-collected data is
allowed; new experiments, sweeps, and hypothesis families are not. The standing gate
calendar (STATE.md 2026-08-08) runs to completion with terminal branches; nothing on it
extends.

## 4. Closed doors (never re-propose; each died with evidence)

- Taker arming at ask > 0.45 (adverse selection, CI clear of zero, 2026-08-07)
- Kelly / variable / confidence sizing; correlation-aware ensembles (2026-06-05)
- Maker / resting-limit execution (adverse selection -$1.99/tr, killed twice)
- External avoid-gates; global symbol-allowlist widening (per-sid grants only)
- Flow family (0/11), cross-horizon sweep family (0/145) (sealed reveals 2026-07-03)
- Hour-exclusion knobs (R4 died inverted, 2026-08-07)
- A 5m live executor during the freeze (the 15m executor's window/settle/claim paths
  have never run a 5m slug; 5m oracle basis ~10x the 15m one)
- Quoting engine-tape or settlements.jsonl P&L as truth (official labels only)
- Re-settling the paper engine itself (research labels come from official_outcomes;
  paper_engine.py settlement stays deliberately unchanged)

## 5. Current armed set

| book | size | band | caps | stop rule |
|---|---|---|---|---|
| `fav_disagree_live` | $10 (ladder above) | ask 0.05-0.45 | $100 bankroll, $50/day | <= -$0.50/fill AND CI-hi<0, or -$50 drawdown |

Retired/disarmed live books (paper twins stay enabled): `det_lwd_live` (2026-08-08),
`fav_disagree_hi_live` (2026-08-07), `early_disagree_live` (demoted 06-18; hype-grant
gate 08-28), `det_d12_dual_live` (06-18), `det_d12_wide_live` (backup, dead).
