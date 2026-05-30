# Mean-Reversion Live — State Log

> **For new sessions:** read `GOAL.md` first (the why), then this file (where we left off), then `CLAUDE.md` (the how). Append a dated section to this file when you finish a session.

---

## 2026-05-15 — Week 1 start

**Status:** Sibling repo `polymarket-mean-reversion` bootstrapped today. Live combined collector + paper trader works end-to-end:
- WebSocket consumes Polymarket CLOB books → 1Hz aggregator → 23-column CSV.gz (matches historical schema)
- 4 strategies route ticks through `PerMarketState` (the everted `simulate_market` loop)
- First live trades captured during the 2-minute smoke test
- All 12 unit tests pass including the **load-bearing replay parity test** (`tests/test_paper_engine_replay.py`)

**Backtest reference:** `/Users/itayozer/dev/polymarket-arb/data_v2/analysis/mean_reversion/SUMMARY_2026-05-15.md`

**Strategies running** (`strategies.yaml`):
- `cfg_21c8c00165b3` — DOWN-only validated #1 (88% WR backtest)
- `cfg_333fde9cecb8` — BOTH ASIA validated #2 (93% WR backtest)
- `relaxed_v1` — exploratory variant
- `cfg_5m_control` — 5m sanity check, expected to lose

**Data layout:**
- `data/historical/` ← physical move of `polymarket-arb/data_v2/`. `polymarket-arb/data_v2/` is now a symlink to here so the backtest CLI works on both old + new files.
- `data/live/` — new per-second tick CSVs starting today
- `data/outcomes.csv` — appended on each window close

**Key implementation note:** Polymarket's `/events` endpoint sorts by `startDate` (when trading opened, often 24h ago). To find markets currently IN their observation window, `clients/gamma.list_active_markets` PROBES `/markets?slug=...` directly for the next ±2 5m/15m boundaries.

**Polymarket WS protocol note:** the subscription message is only honored at session start; subsequent subscribes are ignored. When the active set changes, `WsCollector` closes the current connection and reconnects with the new asset list. URL: `wss://ws-subscriptions-clob.polymarket.com/ws/market`. Subscribe payload: `{"type":"market","assets_ids":[...]}` (note `assets_ids` typo is Polymarket's).

**Operating:**
- Start: `./scripts/start_all.sh`
- Status: `uv run python -m mean_reversion_live.scripts.status`
- Stop: `./scripts/stop_all.sh`
- Tail: `./scripts/tail_logs.sh`

**Next review:** 2026-05-22 (7 days from start). Tasks:
1. Run a comprehensive sweep on combined historical + 7-day live data (use polymarket-arb's `scripts.mean_reversion.cli sweep`)
2. Compare paper trades to backtest predictions per strategy — measure `mismatch_rate`, `pnl_diff`, `fill_rate`
3. Identify top new configs that emerged from the wider data window
4. Update this STATE.md with findings
5. Decide whether to go live with small size in week 2

**Known issues:**
- None yet. Smoke test was clean.

**Open questions:**
- How many tick rows/day to expect? At 1Hz × ~6 active 15m markets per symbol × 4 symbols ≈ 86k rows/day across all live files. Plenty for analysis.
- Do we collect during long-trading windows (24h pre-observation) or only during the 5/15m observation window? Currently we only collect during observation windows because `seconds_into_window` only makes sense there. The 24h trading-pre-window data is a separate phenomenon — maybe valuable for future strategies but not for THIS strategy.

---

## 2026-05-15 — Hardened for 7-day unattended run

Shipped the changes from `~/.claude/plans/understand-our-goal-and-soft-kay.md` (approved via ultraplan). All 9 tests pass including the load-bearing replay parity. Bot restarted on the new code and is running stable.

**What changed (additive only — no decision-path edits):**

1. **`scripts/respawn_loop.sh` (NEW)** — wraps `run_combined`, respawns on crash with 5s backoff. Cap 100 respawns; bails on 5-in-60s as crash-loop guard. `.combined.pid` now points at the wrapper. `stop_all.sh` SIGTERM is forwarded to the inner process.
2. **`logging_config.py`** — structlog now routes through stdlib `logging` (`structlog.stdlib.LoggerFactory`). RotatingFileHandler (10MB × 5) on `logs/combined.log` actually catches it now; previously it bypassed via `PrintLoggerFactory`. Raw stdout/stderr from the wrapper goes to `logs/combined.console.log`.
3. **`ws_collector.py`** — `_books` is GC'd in `update_subscriptions` when a token leaves the desired set. Bounds memory over a 7-day run.
4. **`run_combined.py`** — added `disk_watcher` (bails gracefully if free < 2GB), extended heartbeat with `books_in_memory`/`disk_free_gb`/`signals_today`, added `macro_dumper` (1Hz write to `data/live_macro/<date>.csv.gz`).
5. **`adapters/arb_imports.py`** — clearer preflight error when the `polymarket-arb` path is missing or doesn't contain `signals.py`.
6. **`scripts/status.py`** — surfaces respawns-today (parsed from `logs/respawn.log`), books_in_memory, disk_free_gb, signals_today.

**Rich data capture (parity-safe):**

7. **`engine/per_market_state.py`** — `PerMarketState.__init__` accepts an optional `observer: Callable[[dict], None]`. Invoked once at the end of `on_tick` via try/finally — AFTER all rng draws and state mutations. Decision values: `flat | near_miss | armed_new | armed_waiting | fired | rejected_fill | skipped_no_fill | skipped_already_traded | skipped_can_enter | skipped_skip_prob | holding | trade_closed_<reason>`. Pure-function near-miss detection via single-param relaxation against `EntryFeatures` (drop ≥ 0.8×, prox ≤ 1.5×, time ≥ 0.5×, price band ±25%).
8. **`engine/strategy.py`** — built observer closure: throttles chatty states (`flat`, `holding`, `skipped_*`, `near_miss`) to 1/sec/slug; ALWAYS logs entries/exits. Writes to `data/jsonl/<sid>/signals.jsonl`. Drops `flat` entirely (would dominate the log).
9. **`engine/market_context.py` (NEW)** — `MarketContext.update(symbol, yes_mid, no_mid, ts_ms)` + `snapshot(ts_ms)` returning `n_symbols_dipping_5pct_60s`, `<sym>_yes_mid`, `<sym>_drop_60s_pct`. O(symbols) per tick. Fed by `paper_engine._on_tick`. Logged into both `signals.jsonl` (as `macro` field) AND `data/live_macro/<date>.csv.gz`.
10. **`collectors/macro_writer.py` (NEW)** — `MacroCsvGzAppender` writes 1Hz cross-symbol snapshots. Schema fixed at construction from `symbols`. Close-and-reopen on each fsync (60-row cadence) so each segment is a complete gzip member — readable mid-write by both `gunzip` and the Python gzip module.
11. **`tests/test_signal_log.py` (NEW)** — verifies observer doesn't break parity AND emits exactly one `fired` event per trade.

**Four new "human-intuition" shadow strategies in `strategies.yaml`:**

| id | Idea |
|---|---|
| `cfg_manual_mirror` | Closest to manual rules — wider band (0.10–0.30), 7-min-left filter, slower reaction (signal_skip_prob=0.15). |
| `cfg_velocity_v1` | Fast knife > slow bleed: `drop_window_sec=15`, otherwise mirrors validated #1. |
| `cfg_imbalance_v1` | Wait for sellers to exhaust: validated #1 + `book_imbalance_min=2.0`. |
| `cfg_wide_band_v1` | Volume generator: 0.05–0.35 band, drop=12, profit_target=40, max_hold=300. |

8 strategies enabled total. Each gets its own `data/jsonl/<sid>/` dir + `data/portfolios/<sid>.json`.

**Verification done:**
- `uv run pytest tests/` — 9 passed including parity tests.
- 10-min smoke: bot up, 25–32 active markets, signals.jsonl populating for every strategy, `data/live_macro/2026-05-15.csv.gz` has 61+ rows with all 10 columns.
- Crash recovery: SIGKILL on inner Python — wrapper detected (rc=137), slept 5s, respawned (`logs/respawn.log` shows the transition). Status shows `respawns today: N`.

**Sample signal record (observer + macro wired):**
```json
{"ts_ms":..., "strategy_id":"cfg_velocity_v1", "decision":"skipped_skip_prob",
 "features": null,
 "macro":{"n_symbols_dipping_5pct_60s":3,
          "btc_yes_mid":0.155, "btc_drop_60s_pct":0.0,
          "eth_yes_mid":0.025, "eth_drop_60s_pct":92.96,
          "sol_yes_mid":0.02,  "sol_drop_60s_pct":95.18,
          "xrp_yes_mid":0.355, "xrp_drop_60s_pct":14.46}}
```

**Small in-flight fixes discovered during smoke (not in the plan):**
- `MacroCsvGzAppender` originally locked schema on first row, but the first row arrives before any ticks have populated `MarketContext` — schema was getting locked to `{ts_ms, n_symbols_dipping_5pct_60s}` and silently dropping every per-symbol column. Fixed by passing `symbols=settings.symbol_list` into the constructor and pre-computing the full schema. Also moved from `flush+fsync` to `close-and-reopen` per fsync segment so the partially-written file is gunzip-readable mid-run.
- `stop_all.sh` had `set -u` interacting oddly with the `for i in {1..30}` loop. Dropped `-u` from the strict-mode flags. Cosmetic — the script worked either way.

**Known gaps (deferred to week 2):**
- `data/portfolios/*.json` files are still session-only (not loaded on restart). Each restart resets the in-memory portfolio to zero. `trades.jsonl` is the durable record — the week-end review reads from there.
- No SIGHUP hot-reload of strategies. Restart suffices for now.
- Chainlink oracle integration for outcome correctness still deferred. Validated configs rarely hit `forced_resolution` since their `max_hold < window_duration`.

**Next review:** still 2026-05-22 (7 days from start) — comprehensive sweep on combined historical + 7-day live, compare paper trades to backtest predictions, surface top new configs. The new data fields are load-bearing for the review:
- `data/jsonl/<sid>/signals.jsonl` for entry-funnel analysis (fired vs near-miss vs skipped — where is each strategy losing potential trades?)
- `data/live_macro/<date>.csv.gz` for "does the edge weaken under macro stress?"
- Compare the 4 shadow strategies' PnL/WR against the 2 validated configs.

---

## 2026-05-22 — Full edge-research engagement (branch `edge-research`)

A from-scratch, physics-first investigation replacing the sweep-and-deploy
approach. **Full write-up: `docs/research/FINAL_REPORT.md`.**

**Outcome: no profitable strategy found — a genuine, honest negative.**

What happened:
- **Phase 0 audit found two data bugs.** (1) March 16–17 tick data has a corrupt
  order book (`bid > ask` 83–88%) — the data the original `BACKTEST_VERDICT.md`
  sweep ran on; that "edge" was a ≈$2/trade encoding artifact. `BACKTEST_VERDICT.md`
  is now marked **invalid**. (2) The live bot's `discovery.py` recorded each
  strike ~30 min too early, corrupting `move_pct`/`outcome` for all May data
  (labels wrong on 31% of windows).
- **The strike bug is FIXED** — `discovery.py` now captures the strike at
  window-open; the bot was restarted. Correct labels were rebuilt from
  Polymarket's API (real resolved outcomes, 100% coverage) and the canonical
  dataset re-derived.
- **L2 capture added** — the bot now also writes full-depth books to
  `data/live_l2/` at 1 Hz.
- **Phases 2–4 on corrected data:** the market is well-calibrated; odds continue
  *down* after a drop (no bounce); the user's patient policy loses −$2.19/trade
  honestly priced. **Market-making feasibility: no-go** (adverse selection >
  spread). Three interim "edges" were all data artifacts, caught in research.
- Root cause of viability: a 16–21% taker round-trip cost wall that no measured
  edge clears.

**Bot status:** running, with the strike fix + L2 capture. It may continue as a
pure data collector; the `research/` pipeline is re-runnable on future data.

**Open door:** the user's real manual trade records were never available — the
one remaining way to test whether the manual edge was real. See FINAL_REPORT §4, §7.

---

## 2026-05-22 — Leaderboard wallet analysis (branch `leaderboard-wallet-analysis`)

Owner asked: do the robust wallets on Polymarket's crypto profit leaderboard
profit from market-making, and can we replicate it?
**Full write-up: `docs/research/leaderboard_mm_verdict.md`.**

**Outcome: the market-making hypothesis is refuted; the dominant winning pattern
has no replicable edge.**

What happened:
- Built a data pipeline and pulled **239 leaderboard wallets** (union of top 100
  of the MONTH/WEEK/ALL crypto boards), fetched each wallet's on-chain activity,
  and classified by archetype. Result: **167 (70%) `directional_holder`** (buy a
  side, hold to resolution), 27 `mint_merge_arbitrageur`, 10 scalpers, **1**
  true `passive_liquidity_provider`, 34 mixed/non-crypto.
- **MM hypothesis refuted, doubly confirmed.** The prior `market_making_feasibility.md`
  said NO-GO from crude economics (spread ~1c vs adverse selection ~2.25c). The
  wallet evidence confirms it independently: if MM were profitable the
  leaderboard would be full of market-makers — it is 1 of 239.
- **The directional pattern is survivorship, not edge.** Backtested the dominant
  pattern (rule M15-DH-1: buy favourite early, hold to resolution) on tick data
  that includes the losers: 15m taker −$0.26/trade (CI straddles 0), 5m taker
  −$0.55/trade (CI negative), maker variants straddle 0 or negative. Nothing
  CI-positive on DEV; sealed hold-out stayed sealed. The favourite side is
  well-calibrated. This is the **4th independent confirmation** the short-dated
  market is efficient-after-cost.
- **Data bug found:** `data/research/ticks_5m.parquet`'s baked-in `outcome_up` is
  ~31% corrupt (1,564 of 5,018 windows); the backtest used corrected 5m labels.
  Future 5m work must do the same. 15m labels are authoritative.

**The one open lead:** the `mint_merge_arbitrageur` cluster (27 wallets, several
with $0.5M–$1.6M lifetime PnL) is the only genuinely non-directional, persistent
pattern — but it lives in **longer-dated crypto price-target markets** (not 15m
Up/Down), is completely untested by us, and pursuing it would be a new research
project (new data collector, new study). Presented as a decision for the owner.

**Recommendation:** NO-GO on market-making and NO-GO on directional strategies
for 5m/15m crypto Up/Down. No edge for a small patient bot in the markets
studied. See `docs/research/leaderboard_mm_verdict.md` §7.

---

## 2026-05-29 — Edge hunt on new-data feeds: FIRST real edge found

Full forensic + research pass using the L2/trade-tape/fast-spot/Chainlink feeds
live since 2026-05-22 (the doors prior research said were untested). See
`docs/research/edge_hunt_synthesis.md` and per-phase docs.

**Phase 0 — trustworthy harness (DONE, null-test PASS ✅):**
- Settlement feed CORRECTED: these markets resolve on the **Chainlink Data Stream**,
  not Coinbase (prior Task-4 verdict was an artifact of no Chainlink data then).
  Ties resolve **Up**. No liquidity-rewards pool exists. (`phase0a_settlement_feed.md`)
- Joined dataset `data/research/joined_15m.parquet` (2.2M ticks, 2456 windows,
  clean window 05-23→29) + realistic fill/cost simulator `research/sim/fills_v2.py`
  (walks real L2; taker 0.07·p(1−p); hold-to-resolution = one-way cost) + null-test
  gate (`research/sim/null_test.py`). Harness re-confirms the market is calibrated.

**The finding — book LAGS spot → momentum/determinism edge (NOT mean-reversion):**
- **Phase 1 (PRIMARY): late-window determinism pickoff.** Last 60s, spot ≥5bps from
  strike, buy favourite at ask ≤0.90, hold to resolution. **OOS hold-out: +$1.68/
  trade, 91% WR, CI [+0.97,+2.39], ~$73/day.** Survives 5s latency, both-halves CV,
  all 4 symbols. (`oracle_mechanics.py`, `oracle_mechanics.md`)
- **Phase 2 (secondary): mid-window stale-quote pickoff**, jump-gated. OOS +$2.7–3.7/
  trade, CI excl. 0, but higher-variance/outlier-sensitive. (`stale_quote.md`)
- **Phase 3: maker = NO-GO** (real round-trip −0.6 to −1.8¢; no rewards; inventory
  risk). (`maker_real.md`)
- **Phase 4: dip-reversion (user's original thesis) = NEGATIVE** — dipped side is
  calibrated; spot-flat filter (never testable before the proximity-bug fix) doesn't
  help. The "buy the dip" intuition is backwards here. (`trade_flow.md`)

**Next — Phase 5 (harden):** gauntlet (multiple-testing correction, cost-stress,
larger re-sealed hold-out); build engine support for the determinism strategy (new
type: late-window favourite-buy + hold-to-resolution, NOT the mean-reversion
machine); forward paper on unseen windows; then small live test ($50–100, $10/trade,
daily cap). Caveats: 7 clean days only; fat left tail; capacity ~$10–50/trade.

## 2026-05-29 (cont.) — Phase 5 gauntlet PASS + forward validation (+ a fake-positive caught)

**Gauntlet on the Phase 1 determinism edge — PASS** (`docs/research/gauntlet_verdict.md`):
cost-stress combined worst-case +$1.28/tr CI[+0.82,+1.74]; per-regime both green;
calibrated multiple-testing null p<0.0001 for the robust rule (dist≥5/ask≤0.90,
N=333). The sweep-max (dist≥10) is within best-of-20 luck (p=0.054) — use the
robust rule, not the max.

**Live engine support built but NOT deployed — critical catch.** Added a new
strategy type `engine/determinism_state.py` (+registry/strategy wiring, all
additive; replay-parity test still green; 8 unit tests). Validating that it
reproduces the backtest exposed a fatal feed gap: the live tick's coinbase_price/
move_pct is the STALE ~14s poll (median 1.75bps off the fresh WS spot, sign
disagrees 12.8%) — a `DeterminismState` reading it is a LOSER (true WR 0.48) while
self-reporting a fake +$2.4/tr. `det_lwd_v1` is in strategies.yaml but
**enabled:false**. (`docs/research/forward_deployment.md`)

**Forward validation — running safely** via daily OOS backtest on fresh cb_spot
(`research/forward_validate.py`, log `docs/research/forward_validation_log.md`):
6/7 clean days green, OOS (28-29) +$1.68/tr WR 0.908 CI[+0.97,+2.39], cum +$482.

**Pre-LIVE requirement:** wire the fresh WS spot (live_spot) into the paper engine,
then re-enable det_lwd_v1, confirm live-paper vs backtest drift <30%, then small
live test. Deliberately gated — paper-prove first.

**Phase 6 (widen to hourly/daily): not triggered** — it was gated on Phases 1-4
all being negative on 15m; a 15m edge exists, so widening is optional (future
capacity play; 15m edge is capacity-limited ~$10-50/trade). Tests: 259 pass.

## 2026-05-29 (cont.) — DEPLOYED: determinism edge to forward paper; all mean-rev disabled

Per user: disabled all 9 prior (mean-reversion) strategies; deployed the Phase 1
determinism edge to live PAPER for forward testing, with a daily max-loss cap.

**Engine work (all additive; replay-parity test green; 13 det/parity tests pass):**
- **Fresh WS spot wired into the engine** (`spot_ws_collector` now updates the shared
  SpotPriceCache). Verified live: tick coinbase_price tracks WS spot to 0.15bps (was
  stale ~1.75bps, sign-wrong 12.8% — which had faked a live +$2.4/tr loser). THIS was
  the pre-live blocker; now fixed.
- New strategy type `DeterminismState` + `DailyLossGuard` (engine/determinism_state.py),
  wired via registry/strategy (det_params). Three hardening fixes found by the
  "live must reproduce backtest" gate: (1) book-health guard (skip decided/collapsed
  late-window books), (2) TRUE-outcome settlement via engine.settle_window() on_close
  (tick-derived settle was optimistic 0.96 vs true 0.89), (3) fresh-spot distance.
  Validated: live replay reproduces the gauntlet exactly — 336 tr, WR 0.893, +$1.581/tr.

**Live now (`strategies.yaml`):** ALL prior strategies enabled:false. Two enabled:
- `det_lwd_v1` — uncapped (measures the true forward edge).
- `det_lwd_v1_capped` — $50/day max-loss cap (live-candidate config).
Both: 15m, last 60s, |spot−strike|≥5bps, buy favourite ask≤0.90, hold to resolution,
$10/trade, $1000 capital. Restarted clean (pid 1346); 28 markets; emitting live.

**Watch:** first det trades appear in the last 60s of 15m windows as they close
(across the day, not just ASIA). Forward track also runs off-engine daily
(research/forward_validate.py). Data note: joined outcome_up_clean is per-row and
corrupted on start_price=0 rows; all harnesses filter start_price>0 so results are
unaffected (live settles on market.start_price = real strike).

## 2026-05-29 (cont.) — Phase 2 added + complete per-trade data capture (1-week forward run)

Per user (let it run 1 week; want complete data to later lift WR/profit via time/
condition filters; add the 2nd edge):
- **Phase 2 (stale-quote pickoff) deployed** as `det_sqp_v1` (uncapped) + `det_sqp_v1_capped`
  ($50/day). New `StaleQuoteState` (engine/stale_quote_state.py) loads a FROZEN empirical
  P(Up|z) curve (data/research/stale_quote_curve.json); mid-window, |model_p-mid| in
  [0.08,0.30] + spot jump>=8bps, hold to resolution. Offline replay reproduces the edge:
  403 tr, WR 0.509, +$3.48/tr, median +$3.69 (higher-variance secondary edge).
- **Rich per-trade data capture** for BOTH edges → data/jsonl/<sid>/trades_detailed.jsonl:
  per trade logs hour, dow, symbol, time_left, dist_bps, entry_ask, spread, ask_depth,
  spot_vel_10s/30s, rvol_60s, (+model_p/z/mispricing for sq), outcome, pnl. This is the
  dataset for the weekly review to find filters (skip hours/regimes) that raise WR+profit.
- 4 enabled strategies (2 det + 2 sq); all mean-rev still disabled. Engine: settle_window
  now settles any hold-to-resolution state (hasattr settle). 266 tests pass, parity green.
  Restarted pid 17253.

**1-WEEK FORWARD RUN started 2026-05-29 ~12:26 UTC → review ~2026-06-05.** Bot runs via
nohup (survives session close). Review: live-paper vs backtest drift <30%; slice
trades_detailed by hour/regime/symbol to propose WR-lifting filters; decide on small live test.
