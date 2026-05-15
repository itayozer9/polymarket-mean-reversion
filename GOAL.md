# Why This Project Exists

> **For future Claude sessions:** Read this file BEFORE doing anything else. It explains the end goal and the manual strategy the bot is built to automate. Everything else (CLAUDE.md, STATE.md, strategies.yaml) is implementation detail in service of this goal.

---

## The end goal

A **24/7 live trading bot on Polymarket** that exploits the rapid odds movement (the "jumps") in short-window crypto up/down markets to generate consistent profits.

Not a research project. Not a one-off backtest. A real bot, running locally, taking real money trades, **robust and profitable over time**.

**Operator philosophy:**
- Bot must trade like a *patient human* would — not a high-frequency algo. The edge is mean-reversion on volatility shocks, not latency.
- Fixed position size ($10/trade for now) — explicitly *not* Kelly or position-scaling. The user wants the bot to mirror the human discipline that produced the edge.
- Statistical robustness over raw returns: a strategy with $50/day at p<0.001 across 4 symbols is **infinitely better** than $500/day on one symbol with p=0.4.

---

## The manual strategy (the edge we're automating)

The user discovered this trading manually on Polymarket's 15-minute BTC up/down markets and validated it works for them in real money over recent days. The bot is a direct mechanical translation:

### The setup

Polymarket's 15-minute BTC Up/Down markets work like this:
- A window opens (e.g. 14:00 UTC) and runs for 15 minutes
- BTC's spot price at window open = the **strike** (the price BTC must end above for UP to win)
- Traders bet on UP or DOWN — odds (priced 0.00–1.00) move in real time as BTC moves and time decays
- Window closes at 14:15 UTC, market resolves Up or Down

### The pattern the user spotted

When one side's odds **drop sharply** (e.g. UP falls from 0.40 → 0.15 in a few seconds because BTC just dipped), the market often **overshoots**. Within seconds to minutes, the odds bounce back up by 40–200%.

This is classic mean-reversion: short-term noise (a knee-jerk reaction to a price wiggle) gets corrected once volatility settles.

### The exact manual rules

1. **Watch a 15m BTC Up/Down market that has at least 7 minutes left** (early-window noise is too random; late-window odds are too close to resolution).
2. **Wait for one side's odds to drop into the 0.10–0.30 band** after a visible drop from higher.
3. **Verify BTC's current price is close to the strike** — e.g. user's recent winning trade: bought UP at 0.15 when BTC was $81,300 and the strike was $81,550 ($250 away, ~0.3% — close enough that a small BTC bounce could flip the market).
4. **Buy that side with a fixed $10 stake.**
5. **Wait for the bounce.** Exit at +40% to +200% profit (so if entry was 0.15, exit at 0.21–0.45).
6. Hold time: typically seconds to a few minutes.

### Why it works (mechanically)

- Polymarket's order book on short-window markets is thin — small spot price moves cause oversized odds reactions
- The mean-reversion edge comes from being on the *other side* of panic-sellers
- The "7 min left + price near strike" filter is critical: it ensures the market still has real uncertainty (so the bounce is plausible) and isn't a near-certain Up/Down (where the cheap side is cheap *because* it's losing)

---

## What the backtest proved (Mar 4–17 2026 data, ~14 days)

Statistical validation on historical orderbook tick data:

- **Two configs survived Bonferroni-corrected significance on ALL 4 symbols (BTC, ETH, SOL, XRP)** at 15m out-of-sample.
- Top config `cfg_21c8c00165b3`: 88% win rate, +$214 in-sample BTC, **+$258 ETH OOS, +$249 SOL OOS, +$214 XRP OOS** — generalizes cross-symbol.
- Top config `cfg_333fde9cecb8`: 93% win rate, +$183 BTC IS, **+$229 ETH, +$392 SOL, +$284 XRP** OOS.
- 5m markets: **0 of 149 random configs profitable** — the strategy is structurally 15m-only.

Full numbers in `BACKTEST_VERDICT.md`.

---

## What the bot does (current state, week 1)

Live runs both validated configs + 1 exploratory + 1 5m control (expected to lose; sanity check) in parallel. See `strategies.yaml`.

- Paper trading only this week — no real money yet
- Per-second tick data captured + saved to `data/live/<symbol>_<date>.csv.gz`
- Every signal, trade, and portfolio snapshot logged to `data/jsonl/<strategy>/`
- Atomic JSON portfolio writes survive crashes
- 24/7 via `nohup` + PID file + `data/KILL` sentinel

**End of week 1 (2026-05-22):** combined-data backtest + live-vs-paper drift analysis → decide whether to go live with real money in week 2.

---

## The path forward

| Phase | Status | What |
|---|---|---|
| Backtest | ✅ done | 1000-config LHS sweep, top 15 cross-validated, edge survives Bonferroni on 4 symbols |
| Live infra | ✅ done | WS collector + multi-strategy paper engine, replay parity test passes |
| Week 1 paper run | 🟡 in progress | 4 strategies trading in parallel, daily-ish status check |
| Week 1 review | ⏳ 2026-05-22 | Comprehensive sweep on combined (historical + live) data + drift analysis |
| Week 2 live | ⏳ pending review | Small daily-loss cap ($50), $10/trade, real money |
| Scaling | ⏳ later | If live PnL matches paper for 2+ weeks, consider sizing up |

---

## Non-negotiables

These are the rules the user gave us. Do not deviate without explicit confirmation:

1. **Fixed $10 per trade.** No Kelly. No bankroll-fraction sizing. Match the manual discipline that produced the edge.
2. **Bot must run 24/7 locally.** Not fly.io, not a one-shot CLI. A long-lived process.
3. **Paper before live.** Always. Never enable real trades without a fresh plan.
4. **Reuse `polymarket-arb/scripts/mean_reversion/{signals,simulate,config,portfolio}.py` verbatim.** The replay-parity test (`tests/test_paper_engine_replay.py`) is load-bearing.
5. **Open architecture for new strategies.** The data we collect (rich tick logs, signal-not-fired logs, latency) must support discovering NEW strategies in week 2+, not just the current two.
6. **Robust over flashy.** A strategy that's modestly profitable on 4 symbols beats one that's wildly profitable on BTC alone.

---

## What "success" looks like

End of month 1 — the user has:
1. A bot running unattended for 4+ weeks
2. Live PnL within 30% of paper PnL (proves execution is honest)
3. At least 3 strategies enabled simultaneously, all positive over the period
4. A growing tick + trade dataset that fuels weekly strategy refinement
5. Confidence to scale position sizing beyond $10

End of month 3 — the user has a meaningful side-income from this. That's the goal.
