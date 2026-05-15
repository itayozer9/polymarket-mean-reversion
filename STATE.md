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
