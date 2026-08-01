# Chainlink-true edge hunt — 2026-06-05

Goal: find a **durable, latency-independent, profitable** edge for the live 15m-crypto
goal, settled on **Chainlink** (the oracle Polymarket actually pays), judged on
**fresh-OOS** data, under a **"we are not the fastest"** fill model. Driven by the
user's constraints: (1) no latency/speed edge ever; (2) survivors → 1-week paper, then
decide live; (3) keep the data bot + $5 det_lwd live probe running.

## Phase 0 — data foundation (DONE)
- Merged `data/live_chainlink/` (15s cadence, 309k rows) into `joined_15m.parquet`:
  `chainlink_price` now 99.6% populated, `cl_cb_basis_bps` added (|basis| median 1.92 bps).
  Code: `research/dataset/chainlink_merge.py` (+ wired into `joined.py`), one-time fill via
  `research/dataset/augment_chainlink.py`. Backup: `joined_15m.parquet.coinbase.bak`.
- Slim prepped frame `joined_15m_slim.parquet` (50 cols, 202 MB) for memory-light agents.
- Shared gauntlet `research/analysis/edge_lab.py`: filter → first qualifying tick → depth-gated
  realistic fill at (entry_sec+latency) → **Chainlink resettle** → per-split window-clustered CIs
  → **latency-survival sweep** → CPCV → daily DSR. Validated: reproduces det baseline exactly
  (+$0.87 vs +$0.88 FULL).

## Baseline — existing edges on the oracle that pays (depth-gated, realistic)
Per-$10 trade, Chainlink-settled. `future` = fresh-OOS split.

| edge | FULL EV | FULL 90% CI | fresh-OOS EV | fresh CI | latency 2→10s | verdict |
|---|---|---|---|---|---|---|
| det_lwd | +$0.87 | [+0.48,+1.26] | **−$0.06** | [−0.84,+0.67] | flat (0.87→0.72) | break-even fresh; latency-robust but no edge |
| det_sqp | +$0.71 | [+0.11,+1.36] | +$0.33 | [−0.44,+1.12] | n/a | marginal, CI crosses 0 |
| E4 (resettle, partial-fill) | +$16.74 | [+10.3,+23.9] | +$18.93 | [+7.3,+32.3] | — | headline, but… |
| **E4 (edge_lab, depth-gated $10)** | **+$8.89** | [+4.7,+13.7] | **+$2.30** | [−2.3,+7.1] | **decays 8.89→4.70** | thin once fillable + **latency-exposed** |

Takeaway: on the correct oracle, with realistic fills, **none of the existing edges has a
robust fresh-OOS, latency-surviving edge**. E4 is best but small-n (199 raw / 73 fillable),
54% oracle-flip, and decays with latency.

## First-cut new edges (all through edge_lab; KILLED/marginal)
det/E4 reproduced + 7 first-cut new edges:

| edge | n | FULL EV | fresh-OOS EV | verdict |
|---|---|---|---|---|
| E-oracle (buy chainlink side on oracle disagreement) | 147 | −$3.51 | −$3.03 | dead (near-strike coin-flips) |
| E-persist momentum | 2914 | −$0.27 | −$0.44 | dead |
| E-persist reversal | 2933 | −$0.78 | −$0.70 | dead |
| E-flb (buy favourite mid-window) | 2315 | −$0.15 | −$0.34 | dead after fees |
| E-model (calibration, no jump) | 2252 | −$0.05 | +$0.02 | break-even |
| E-mom (late momentum) | 332 | +$0.76 [+0.33,+1.17] | −$0.28 | works FULL, **fails fresh-OOS** |
| E-imb (book imbalance) | 1359 | −$1.21 | −$0.68 | dead |

→ Triggered the comprehensive workflow `edge-refine-hunt` (8 agents, each refining one family
+ wildcards) to test whether ANY creative variant/regime survives. Results pending.

## Phase 1.1 — restart-safe hard daily cap (VERIFIED in place)
- `det_lwd_live` (live $5 probe) already uses `daily_loss_mode: hard_worstcase`, `max_daily_loss_usd: 25`.
- Startup replay wired: `strategy.py:_replay_existing_trades` → `DailyLossGuard.replay_settled`,
  keyed by `exit_ts_ms` UTC-day → rebuilds today's settled PnL across restarts. Unit-tested
  (`test_daily_loss_guard.py::test_replay_settled_rebuilds_realized_in_hard_mode`).
- Live executor (`scripts/live_executor.py`): bankroll cap $100 is the hard real-money bound and
  **is restart-safe** (`deployed` persisted). Hard total-loss ≈ $100.
- **Open items (minor, bounded by the $100 cap):**
  - executor's own `DAILY_CAP_USD`/`realized_by_day` is declared but **not enforced** (dead code;
    daily throttle relies on the engine guard).
  - `filled = f.making_amount` is the likely cause of the first sol live fill reporting
    13.95 sh @ avg 0.36 vs quoted 0.66 (share over-count); **$5.02 spent is correct**, only
    per-share P&L is suspect → verify the on-chain position before trusting per-share numbers.
- For the new improved **sqp** paper strategy (Phase 4): use `hard_worstcase` + correlation-netting
  (existing `_capped` twins stay on `soft_settled` as controls).

## Live probe status (2026-06-05)
First 3 real order attempts (one 10:59 IST window, macro-correlated cluster): sol UP filled
($5.02, accounting suspect), xrp DOWN + eth UP **FAK no-fill** (400 "no orders to match" =
benign kill). Deployed $5.02/$100. Executor up, no kill sentinels.

## Workflow result — VERIFIED survivors (4 keep / 2 marginal / 2 kill)
8-agent creative refinement (`edge-refine-hunt`, ~500 variants). One mechanism recurs:
**the book systematically underprices near-locked favourites** (favourite-longshot bias),
capturable held-to-resolution with a time buffer → **latency-proof** (a laptop trader keeps it).
Independently reproduced from specs via `research/analysis/verify_survivors.py`:

| edge | trigger | n | FULL EV | fresh-OOS EV | latency 2→10s | DSR |
|---|---|---|---|---|---|---|
| wildcard | low rvol(≤1bps) + fav ≥8bps off strike, ask 0.55–0.80, tl 240–420 | 686 | +0.55 | **+0.97** [+0.27,+1.63] | 0.55→0.51 | 0.64 |
| pricestruct | NO-fav ask 0.88–0.93, tl 420–480, basis≥−2 | 434 | +0.47 | +0.66 [+0.42,+0.88] | flat | 0.89 |
| momentum | tl 60–120s, lead ≥12bps & growing, buy fav 0.50–0.90 | 173 | +1.30 | +0.88 [+0.08,+1.65] | 1.30→0.99 | 0.96 |
| crosscoin | BTC fav ask≥0.85 & |dist|≥20 → buy agreeing alt favs, tl 90–180 | 98 | +0.69 | +0.77 [+0.27,+1.18] | flat | 0.64 |

**Overlap (Jaccard on slugs): 0.01–0.09** → nearly disjoint, genuinely different edges (diversifying).
**Combined fav-value book (wildcard ∪ pricestruct ∪ momentum, dedup): fresh-OOS +$0.90/tr
[+0.55,+1.25], n=388, latency-immune, DSR 0.974.**

KILLED (honest): oracle-divergence (98% identical to the Coinbase determinism trade — chainlink
adds nothing); microstructure (efficiently priced / needs sub-2s speed); persistence (reverses
across OOS periods, jackpot-driven). MARGINAL: calibration NO-tilt (pays only because the fresh
window down-drifted — a directional bet, not symmetric skill).

**Honest caveats before sizing:** (1) fresh-OOS is only 4 days (Jun 1–4) — small; the forward
paper test is the real arbiter. (2) These are deep-ish favourites (ask ~0.8–0.92): risk ~$10 to
win ~$1–2, so a rare miss costs a full stake → needs the hard daily-loss cap. (3) ~500 variants
were tried program-wide (multiple-testing) — mitigated by the combined book (not a cherry-picked
single cut), near-disjoint independent confirmation, clear mechanism, and CPCV 100%, but forward
paper is decisive. (4) lookahead-audited: all filters use decision-time features only.

## Next
- **Phase 4 — engine integration + forward test:** implement a generalized favourite-value
  strategy type (mid-window timing + vol/dist/ask/momentum gates, hold to resolution), mirroring
  `DeterminismState` so it slots into PaperEngine + keeps replay parity. Add the survivors as PAPER
  strategies (combined book + the 4 slices for A/B), restart engine, run a 1-week Chainlink-settled
  forward test. Decide live after.
- Lower priority now (the new edges dominate): Phase 1.2 correlation-netting + Phase 2 E4 depth-aware
  sizing — det_lwd/E4/sqp are ~break-even on the correct oracle, so the favourite-value book is the
  better live candidate.

## Phase 1/2 — sqp + E4 (closed)
**sqp "prevent the massive losses":** correlation-netting BACKTESTED + REJECTED — net K=1 worsens
maxDD (−$347→−$637) and longest-loss-streak (1d→3d, bootstrap p95 2d→7d) because the coin clusters
are NOT perfectly correlated, so cutting trades concentrates risk (within-cluster averaging was helping).
The fix that DOES work = the hard_worstcase daily cap (bounds worst-day −$347→~−$55, 0 breaches,
restart-safe). sqp is break-even on Chainlink (+$0.33 fresh-OOS, CI crosses 0) → run only as
bounded-risk, or retire in favour of the fav-value edges. Script: `research/analysis/sqp_corr_netting.py`.
**E4 hardened:** the original last-60s disagree-fade is latency-exposed (not ours). Moving it
mid-window (tl 120–360s) + |dist|≥10 rescues it: future +$5.39 [+1.82,+9.17], n=93, latency-flat to 5s;
5/9 swept cells have fresh-OOS CI>0 (broad, same favourite-underpricing mechanism). Deployed as
`fav_disagree` (4th fav-value paper edge). Unit tests added for the new gates (18 determinism tests green).

## Deployed for the 1-week paper forward test (2026-06-05)
`fav_lowvol`, `fav_deepdown`, `fav_momentum`, `fav_disagree` — all DeterminismState variants,
hard_worstcase $50/day cap, $10 paper bet, Chainlink-settled. Engine restarted; live $5 det_lwd probe
untouched. Decision point: after ~1 week, compare live-paper vs backtest → promote the holders to a
small live probe.
