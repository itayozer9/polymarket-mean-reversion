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

To add a strategy: edit `strategies.yaml`, send `SIGHUP` to the combined process (or restart).

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
- **Strategies live in `strategies.yaml`**, not in code. Add new ones there.
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
