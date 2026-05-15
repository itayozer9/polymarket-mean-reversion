# polymarket-mean-reversion

24/7 live paper trader for validated 15m mean-reversion strategies on Polymarket Up/Down crypto markets (BTC, ETH, SOL, XRP).

**Start here:**
1. **[`GOAL.md`](GOAL.md)** — the manual strategy this bot automates + the end goal
2. **[`STATE.md`](STATE.md)** — rolling log: where things stand right now
3. **[`BACKTEST_VERDICT.md`](BACKTEST_VERDICT.md)** — the statistical proof the edge is real
4. **[`CLAUDE.md`](CLAUDE.md)** — architecture + conventions

**Strategies are validated** in the sibling repo at `/Users/itayozer/dev/polymarket-arb/scripts/mean_reversion/`. The decision logic (`signals.py`, `simulate.py`, `config.py`, `portfolio.py`) lives there as canonical source and is imported here via `sys.path`. We do not copy it.

## Quick start

```bash
uv sync                                  # install deps
cp .env.example .env                     # configure endpoints
bash scripts/start_all.sh                # start collector + paper trader (24/7)
python -m mean_reversion_live.scripts.status  # health check
bash scripts/stop_all.sh                 # graceful shutdown
```

## Status / control

- `STATE.md` — running log, read this first when resuming
- `CLAUDE.md` — project conventions
- `strategies.yaml` — which strategies the bot runs
- `data/portfolios/` — per-strategy state JSON
- `data/jsonl/<sid>/{trades,signals,portfolio_snapshots}.jsonl` — per-strategy event log
- `logs/{collector,paper_trader}.log` — process logs

## Architecture

See `CLAUDE.md`. TL;DR: WebSocket consumer → 1Hz aggregator → fan-out to multiple `PerMarketState` instances → atomic JSON portfolio writes.
