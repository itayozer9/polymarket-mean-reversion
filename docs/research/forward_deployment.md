# Phase 5 — Forward validation & deployment status (determinism edge)

**Date:** 2026-05-29  ·  **Status:** edge PASSED the gauntlet; live deploy BLOCKED
on one engine fix; forward validation running the SAFE way.

## Critical catch — a fake-positive avoided before deployment

Building the live strategy (`engine/determinism_state.py`) and validating that it
reproduces the backtest surfaced a fatal feed mismatch:

- The research edge keys on distance-to-strike from the **fresh WS spot**
  (`live_spot` → `cb_spot`). The live tick's `coinbase_price` / `move_pct` is the
  **stale ~14s poll**: median **1.75 bps** off the fresh spot, p90 6 bps, and the
  **sign disagrees 12.8%** of ticks — fatal for a rule thresholded at 5 bps.
- A live `DeterminismState` reading `move_pct` selected the wrong windows and, when
  settled honestly, was a **loser (WR 0.48, −$3.9/trade)** — yet its self-settle
  (same stale signal) reported a fake **+$2.4/trade**. This is precisely the
  artifact class that sank the prior effort; the Phase-0 "live must reproduce the
  backtest" gate caught it.

**Action:** `det_lwd_v1` is in `strategies.yaml` but **`enabled: false`**. It will
NOT run on the stale feed. The strategy code + tests stay (correct given a fresh
spot input); deployment is gated on the fix below.

## Forward validation — the safe mechanism (running now)

Instead of live paper on a stale feed, forward-validate by re-running the LOCKED
rule on each clean day as it lands, using the fresh `cb_spot` and true-outcome
settle — the same harness the edge was validated on (`research/forward_validate.py`).

Per-day track (rule: last 60s, dist≥5 bps, fav ask≤0.90, hold; fresh spot):

| date | split | trades | WR | $/trade | cum $ |
|---|---|---|---|---|---|
| 05-23 | dev | 71 | 0.845 | +1.16 | +82 |
| 05-24 | dev | 20 | 0.950 | +1.64 | +115 |
| 05-25 | dev | 43 | 0.814 | −0.10 | +111 |
| 05-26 | dev | 58 | 0.879 | +1.27 | +184 |
| 05-27 | dev | 54 | 0.981 | +2.80 | +336 |
| **05-28** | **OOS** | 69 | 0.899 | **+1.62** | +447 |
| **05-29** | **OOS** | 18 | 0.944 | **+1.90** | +482 |

OOS (post-05-27): n=87, WR 0.908, **+$1.68/trade, CI [+0.97, +2.39]**. 6/7 days green.

**Daily workflow** (cron-able): `uv run python -m research.build_joined` (rebuild
with the new day) → `uv run python -m research.forward_validate` (append to track).

## Pre-LIVE requirement (the one engine fix)

Before real money, wire the **fresh WS spot** (`live_spot`/`spot_ws_collector`)
into the paper engine so the live strategy computes distance-to-strike from the
fresh feed, not the stale tick `coinbase_price`. Then re-enable `det_lwd_v1`,
confirm live paper reproduces the backtest (drift < 30%), and only then a small
live test ($50–100, $10/trade, daily-loss cap). This is a deliberate, separate
step — not done here — consistent with "paper-prove, then small live."

## Bottom line

The determinism edge is real and gauntlet-clean (OOS +$1.68/trade, p<0.0001 vs a
calibrated null, survives combined cost-stress). It is being forward-validated
safely on fresh-spot data. Live deployment is one well-scoped engine fix away, and
intentionally gated behind continued forward confirmation.
