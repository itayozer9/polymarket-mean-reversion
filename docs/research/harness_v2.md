# Harness v2 — trustworthy foundation for the edge hunt (Phase 0)

**Date:** 2026-05-29
**Status:** COMPLETE — null-test gate PASS ✅. Everything downstream may trust it.

Built so the next edge hunt cannot repeat the prior disasters (corrupt-data
artifacts, fill-at-quoted-best, optimistic costs). The rule: no edge claim is
admissible until it runs through this harness and clears the gauntlet (Phase 5).

## What was built

1. **Settlement feed nailed down** (`phase0a_settlement_feed.md`): Chainlink
   **Data Streams** (not Coinbase, not the on-chain aggregator we collect). Ties
   resolve **Up** (`end >= start`). Fee = `0.07·p·(1−p)` taker-only + 20% maker
   rebate; 1¢ ticks; $5 min order; a **liquidity-rewards program** exists
   (4.5¢ max spread, $50 min) — new input for Phase 3.

2. **Clean window** (`research/clean_window.py`): the fully-instrumented,
   post-strike-fix data = **2026-05-23 → now** (all of L2/spot/trades/chainlink
   start 2026-05-22; strike fix landed 2026-05-22 15:55). Split: dev 05-23..05-27,
   sealed hold-out 05-28+. Grows daily; re-seal on each weekly re-run.

3. **Joined dataset** (`research/dataset/{feeds,joined}.py`, `build_joined.py`)
   → `data/research/joined_15m.parquet`: **2,195,300 ticks · 2,456 windows**
   (614/symbol; dev 1,920 / holdout 536). Per tick: the existing features PLUS
   - L2 depth summary (full-ladder bid/ask depth, depth-within-2¢, imbalance,
     microprice) — raw 10-level ladders stay on disk for the fill sim;
   - Coinbase-WS spot, asof-merged → `dist_strike_bps`, `spot_vel_3s/10s_bps`;
   - trade-tape per-second YES-space flow (`tr_bull_usd`, `tr_bear_usd`,
     `tr_signed_usd`, rolling `tr_signed_5s`, `tr_bear_10s`);
   - corrected label `outcome_up_clean` (`end >= start → Up`);
   - **`book_healthy`** guard (91.3% of ticks) — the 8.7% crossed/one-sided are
     decided-market collapses (`yes_mid→0/1`); skipping this guard is exactly
     what produced prior fantasy edges. **All analyses must filter on it.**

4. **Realistic fill+cost simulator** (`research/sim/fills_v2.py`, 9 unit tests):
   pure functions on ladder arrays. `walk_buy`/`walk_sell` walk the real L2 book
   (partial fill if too thin); `settle_pnl` (hold-to-resolution = one-way cost,
   winners redeem at $1); `taker_roundtrip_pnl`; `maker_buy_fill` (resting limit
   fills only on a real later trade-through, queue-position haircut, 0 fee +
   rebate — adverse-selected by construction).

## The null-test gate (`research/sim/null_test.py`) — PASS ✅

- **Gate 1 (integrity/calibration):** random-side entries, realized WR vs entry
  price → slope **0.91**, mean |WR−price| **0.0298**. Labels/fills/settlement are
  correctly wired (a misalignment would shatter this). Independently re-confirms
  the market is calibrated on clean May-23..27 data.
- **Gate 2 (no manufactured EV):** random-side net PnL **−$0.33/trade**, not
  significantly positive (the March artifact was ~+$2 — would be caught).
- **Null B (no phantom edge):** buy-favourite real-label PnL (−$0.58) ≤ the
  shuffled-label band ([−0.29, −0.22]). No edge survives label destruction.

## How downstream phases must use it

- Load `joined_15m.parquet`; **filter `book_healthy`**; split by `split` column;
  fit/se­lect on `dev` only; open `holdout` once per candidate (Phase 5).
- Price all fills through `fills_v2` (taker walks L2; maker via trade tape).
- Hold-to-resolution pays one-way cost only — favours selective, fat-tail bets
  (the only shape that can clear the cost wall: PT≈120% break-even WR ≈ 49.6%).
- Re-run `null_test` whenever the dataset builder changes.
