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
