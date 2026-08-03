# polymarket-mean-reversion — Project Instructions

24/7 live data collector + paper trader for validated 15m mean-reversion strategies on Polymarket crypto Up/Down markets.

**Read order for new sessions:**
1. **`GOAL.md`** — the *why*: the user's manual strategy, the end goal, the rules that don't change
2. **`STATE.md`** — where we left off last session, what's running now
3. **`BACKTEST_VERDICT.md`** — statistical proof the edge is real (Bonferroni-passing on 4 symbols)
4. This file (`CLAUDE.md`) — *how* we built the bot

If you read only ONE file, read `GOAL.md`. The whole project is a translation of the manual strategy described there into a 24/7 bot.

---

## Project goal

Run a multi-strategy paper trader 24/7 for 1+ week, collecting per-second tick data + simulated trades for the validated configs from `polymarket-arb`. After the run, do a comprehensive backtest on (historical + new) data combined to refine the strategies and find new edges.

**Out of scope (this week):** real-money trading. Live trading is a separate plan once the paper run validates the engine.

---

## Architecture (TL;DR)

```
Gamma REST (/markets?slug=...)   →  MarketDiscovery (every 30s)
                                         ↓
                                    asset_ids list
                                         ↓
CLOB WS (wss://...market)         →  WsCollector
                                         ↓
                                    OrderBook per token_id
                                         ↓
                                    1Hz aggregator
                                         ↓
                              ┌──────────┴──────────┐
                              ↓                     ↓
                    tick_writer (CSV.gz)     PaperEngine (asyncio.Queue)
                              ↓                     ↓
                  data/live/{symbol}_{date}.csv.gz  PerMarketState per (strategy, slug)
                                                    ↓
                                            signals.entry/exit_signal  ← from polymarket-arb
                                                    ↓
                                            Trade → portfolio.json + jsonl
```

**Single source of truth for decisions:** `polymarket-arb/scripts/mean_reversion/{signals,simulate,config,portfolio}.py`. This repo imports them via `sys.path` injection in `src/mean_reversion_live/adapters/arb_imports.py`. **Do NOT copy.** If polymarket-arb renames a public symbol, update arb_imports.py and re-run the replay parity test.

**Load-bearing test:** `tests/test_paper_engine_replay.py` proves `PerMarketState` produces bit-for-bit identical trades to `simulate.simulate_market` on a historical CSV replay. Run it after ANY change to `engine/per_market_state.py`.

---

## Strategies running

See `strategies.yaml`. Initial set (week 1):

| ID | Description | Status |
|---|---|---|
| `cfg_21c8c00165b3` | Validated #1 — DOWN-only deep dip (0.075–0.125), 88% WR in backtest | enabled |
| `cfg_333fde9cecb8` | Validated #2 — BOTH ASIA hours, trailing stop, 93% WR in backtest | enabled |
| `relaxed_v1` | Exploratory variant — wider band, no daily cap | enabled |
| `cfg_5m_control` | 5m baseline — expected to lose (engine sanity check) | enabled |

To add or toggle a strategy: edit `strategies.yaml` and **restart** the combined process (`./scripts/stop_all.sh && ./scripts/start_all.sh`). The bot reads the YAML once at boot — there is no SIGHUP / hot-reload handler in the source, so changes only take effect on the next start.

---

## Operating

```bash
# Start
./scripts/start_all.sh

# Status
uv run python -m mean_reversion_live.scripts.status

# Tail logs
./scripts/tail_logs.sh

# Graceful stop (SIGTERM; falls back to SIGKILL after 30s)
./scripts/stop_all.sh

# Kill via sentinel (alternative)
touch data/KILL    # processes exit at next check (≤1s)
```

After stop: remove `data/KILL` before restarting.

### The LIVE executor is a separate lifecycle (real money)

`start_all.sh` / `stop_all.sh` manage the ENGINE (`run_combined`) only. The real-money
executor is deliberately outside them: its supervisor is `hourly_monitor.sh` (cron `37 * * * *`),
which relaunches it whenever it is down and no KILL switch is set. So a bare `pkill` races the
monitor, and adding it to `stop_all.sh` would leave real money down after an engine-only stop.

```bash
./scripts/restart_executor.sh          # EXEC_KILL -> clean exit -> relaunch -> verify
                                       # refuses inside the intent-firing window (min 9-14)
touch data/live/EXEC_KILL              # halt ONLY the executor (engine keeps paper-trading)
```

Which restart does a change need?
- `strategies.yaml` (bet size, max_ask, bands, `live:`/`enabled:`) -> **engine** restart only.
  These ride on each intent, so the executor never needs to bounce.
- `.env` `EXEC_*` knobs and the symbol allowlists -> **executor** restart (module constants).

---

## Data layout

- `data/historical/` — symlink to the 14-day Mar 4–17 2026 dataset (lives in this repo; `polymarket-arb/data_v2/` is a symlink to here)
- `data/live/<symbol>_<date>.csv.gz` — per-second tick rows, EXACT 23-column schema matching historical so backtest loaders work on both
- `data/outcomes.csv` — appended on each window close, same schema as historical
- `data/portfolios/<sid>.json` — atomic per-strategy snapshot
- `data/jsonl/<sid>/{trades,signals,portfolio_snapshots}.jsonl` — per-strategy event log
- `data/state/last_tick.json` — heartbeat (5s)
- `logs/combined.log` — rotating 10MB × 5

---

## Conventions

- **UTC everywhere.** All ts_ms / window_start_ts / endDate. ISO-8601 in JSON.
- **Atomic JSON writes:** tempfile + `os.replace` (`engine/persistence.py:atomic_write_json`).
- **structlog** for all logging. JSON in prod, console-pretty in dev (set `LOG_FORMAT=json`).
- **No mocking in tests.** Real CSV fixtures. Real network is fine; just be quick.
- **Never run the full test suite un-niced while the live executor is trading.** It is a ~9min,
  340+ test, parquet-heavy run; on 2026-08-03 two concurrent suites pushed load average to 9.7
  and starved the executor's 2 Hz poll loop for 3 minutes, so two live intents aged to 158s/178s
  and were correctly dropped by the 10s staleness gate. Real intents, lost to a test run. Use
  `nice -n 19 uv run pytest ...`, or run it while the executor is stopped. This is the same
  external-CPU-hog saturation mode that hit the engine on 06-06.
- **Strategies live in `strategies.yaml`**, not in code. Add new ones there.
- **Paper P&L must be quoted on OFFICIAL on-chain labels**, never the engine tape. The paper
  engine's own settlement disagrees with real money on ~17.6% of identical markets, biased
  2.4:1 in our favour, so engine dollars run ~3x hot (measured 2026-08-03). Honest source:
  `data/research/paper_official/daily_scores.parquet` (+ `scoreboard.md`), regenerated by
  `./scripts/nightly_honest.sh`. Gate reads use `research/analysis/score_gates.py`, which is
  official-only by construction.
- **The 5m strategy in the registry is a control.** It's expected to lose money — that's a feature, not a bug. If it suddenly starts winning, investigate the engine.

---

## Things NOT to do

- **Don't edit `signals.py` / `simulate.py` / `config.py` / `portfolio.py` in polymarket-arb** without coordinating with `src/mean_reversion_live/adapters/arb_imports.py`. Run the replay parity test after any change.
- **Don't re-implement the FLAT/ARMED/HOLDING state machine.** `PerMarketState.on_tick` is a mechanical translation of `simulate_market`'s inner loop. Drift breaks the parity test.
- **Don't go live with real money this week.** Paper only. Live trading needs its own plan.
- **Don't bypass `atomic_write_json` for portfolio files.** A crash mid-write must not corrupt the portfolio.
- **Don't delete `data/historical/` or rename `polymarket-arb/data_v2`.** They're symlinked and the backtest CLI in polymarket-arb depends on it.

---

## Next-steps timeline

- **Week 1 (now → +7d):** Paper trader runs 24/7. Daily status check via `/mean-rev-status` skill (TODO: create skill).
- **End of week 1:** Run `end_of_week_review.py` (TODO: implement) — combines historical + live data, runs a fresh comprehensive sweep, compares paper trades to backtest predictions, surfaces new top configs.
- **Week 2:** Decide on live deployment with a small daily-loss cap. New plan required.
