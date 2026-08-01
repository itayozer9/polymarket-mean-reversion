export const meta = {
  name: 'edge-hunt',
  description: 'First-pass scan of 6 mechanistic new-edge hypotheses + 3 audits on the full clean window, each gauntlet-gated with structured verdicts',
  phases: [
    { title: 'New-edge scan', detail: 'one agent per hypothesis, OOS + null-gated' },
    { title: 'Audits', detail: 'survivorship, execution-realism, sq-curve drift' },
  ],
}

// ---- shared harness preamble given to every agent -----------------------
const PRE = `
You are a quant research agent in repo /Users/itayozer/dev/polymarket-mean-reversion
(Polymarket 15m crypto Up/Down paper trader). Run everything with \`uv run python\`.
This is a FIRST-PASS scan: find whether the hypothesis has a real, generalizing
edge worth rigorous follow-up. Be ruthlessly honest; a clean NEGATIVE is a success.

DATA: data/research/joined_15m.parquet — one row per tick per 15m window per coin,
13 clean days 2026-05-23..06-04, 4.4M rows, 71 cols. To save memory, load ONLY the
columns you need: pd.read_parquet(path, columns=[...]).
  Identity: symbol, slug, window_start_ts(sec), seconds_into_window, time_left_sec, date, split.
  FRESH spot (USE THESE): cb_spot, dist_strike_bps, spot_vel_3s_bps, spot_vel_10s_bps.
  NEVER USE (stale ~15s poll): move_pct, coinbase_price.
  Book: yes_best_bid, yes_best_ask, no_best_bid, no_best_ask, yes_mid, spread_yes,
        yes_ask_depth, no_ask_depth, l2_imbalance, microprice, l2_best_bid, l2_best_ask.
  Flow: tr_signed_usd, tr_bull_usd, tr_bear_usd, tr_n, tr_signed_5s.
  Oracle: chainlink_price (fresh, 100% present last-60s), start_price (strike).
  Vol/label: realized_vol, outcome_up_clean (1=Up,0=Down), book_healthy (bool).
SPLITS via column 'split': dev(05-23..27), holdout(05-28..31), future(06-01..04).
  future = the FRESHEST out-of-sample (postdates all prior discovery). That is the
  split your decision rule keys on.

RULES of the game (match the live edges):
 - Always filter book_healthy==True, outcome_up_clean.notna(), cb_spot.notna(), start_price>0.
 - ONE trade per window = the FIRST qualifying tick (sort by seconds_into_window,
   groupby slug, first). Hold to RESOLUTION; settle on outcome_up_clean.
 - Cost truth: taker fee = 0.07*p*(1-p)*shares, ONE-WAY (hold-to-resolution, no exit fee).
   For a first pass you MAY fill at the quoted best ask of the side you buy (note this
   is optimistic vs a real L2-ladder walk; realistic fills are a later step). $10 stake.
   pnl = shares*won - stake - fee, shares=stake/ask, won=1 if your side resolves correct.
 - The COST WALL is real: round-trip-equivalent taker cost ~16-21% of stake; an edge
   must clear it. A signal is only interesting if NET EV/trade > 0 with CI off zero.

MANDATORY NULLS/BASELINES (a positive backtest is NOT enough):
 - PRICE-MATCHED BASELINE: any signal that tends to buy CHEAP sides looks positive
   purely from the known longshot effect. You MUST compare against buying the SAME
   price-bucket sides WITHOUT your signal. The edge must BEAT that baseline.
 - SHUFFLED-OUTCOME null: permute outcome_up_clean across windows; recompute EV; the
   real EV must beat this null (report p).
 - OOS GATE: compute EV on the 'future' split with a window-clustered CI:
   from research.lib.stats import window_clustered_bootstrap(values, groups=slug) -> (p5,p50,p95).
   Verdict 'promising' ONLY if future EV>0 AND its CI lower bound (p5) > 0 AND it beats
   the price-matched baseline AND clears the shuffled null (p<0.10).
 - Helpful: research.lib.rigor has combinatorial_purged_cv(days,...) and walk_forward_splits.

Write your script to research/analysis/<short_name>.py, run it, and report the
STRUCTURED verdict (the StructuredOutput tool). Put the exact numbers in key_numbers.
Do NOT edit strategies.yaml or any engine file. Read-only on data.`

const EDGE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    hypothesis: { type: 'string' },
    mechanism_verified: { type: 'boolean', description: 'did the necessary precondition hold (e.g., a capturable lead-lag >=5s exists)?' },
    n_trades_full: { type: 'number' },
    ev_full: { type: 'number' },
    n_trades_future: { type: 'number' },
    ev_future: { type: 'number' },
    future_ci_lo: { type: 'number' },
    future_ci_hi: { type: 'number' },
    beats_price_matched_baseline: { type: 'boolean' },
    baseline_desc: { type: 'string' },
    shuffled_null_p: { type: 'number' },
    verdict: { type: 'string', enum: ['promising', 'dead', 'inconclusive'] },
    key_numbers: { type: 'string', description: 'one-paragraph dump of the decisive numbers per split + baseline + null' },
    code_path: { type: 'string' },
  },
  required: ['hypothesis', 'verdict', 'ev_future', 'future_ci_lo', 'future_ci_hi', 'beats_price_matched_baseline', 'shuffled_null_p', 'key_numbers', 'code_path'],
}

const AUDIT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    topic: { type: 'string' },
    summary: { type: 'string' },
    key_findings: { type: 'array', items: { type: 'string' } },
    numbers: { type: 'string' },
    recommendation: { type: 'string' },
    code_path: { type: 'string' },
  },
  required: ['topic', 'summary', 'key_findings', 'recommendation'],
}

const EDGES = [
  { key: 'leadlag', label: 'E1 cross-coin lead-lag', prompt: `${PRE}

HYPOTHESIS E1 — cross-coin lead-lag (RE-TEST on FRESH spot).
BTC (or BTC+ETH basket) leads ETH/SOL/XRP. When the basket's cb_spot jumps and a
follower coin's cb_spot has not yet moved, the follower's 15m binary is predictable
before its book reprices. CRITICAL: a prior test (research/analysis/cross_coin_leadlag.py)
found NO capturable lag — but it used the STALE 15s coinbase_price poll on old May
15-20 data. cb_spot is now genuinely fresh (~1-2s cadence). RE-TEST honestly:
  PART 1 (gates part 2): build a 1Hz wall-clock grid of cb_spot per coin on the clean
  window; cross-correlate basket log-returns vs each follower at lags 1..60s. Is there
  a CAPTURABLE lead (peak at lag>=5s with real EXCESS over contemporaneous corr)? A
  peak at <5s is an HFT race, not capturable by an API bot -> declare mechanism_verified
  =false and verdict=dead, do NOT run part 2.
  PART 2 (only if capturable): when basket moves and follower lags, BUY the follower's
  basket-implied side mid-window, hold to resolution. OOS + price-matched + shuffled null.` },
  { key: 'orderflow', label: 'E2 order-flow / microprice', prompt: `${PRE}

HYPOTHESIS E2 — order-flow / microprice divergence.
The depth-weighted microprice (col 'microprice') and signed taker flow
(tr_signed_usd, tr_signed_5s) lead the mid. When microprice diverges from yes_mid,
or a one-sided taker burst hits, the mid is stale and drifts toward microprice. Test
BOTH directions (follow the flow vs fade it) mid-window (time_left 60..840). Buy the
indicated side, hold to resolution. This is a microstructure edge (different mechanism
from spot-lag). MANY features -> be strict on the shuffled null and price-matched
baseline; report which direction (follow/fade) if any survives OOS.` },
  { key: 'oracle', label: 'E3 settlement-oracle divergence', prompt: `${PRE}

HYPOTHESIS E3 — Chainlink-vs-Coinbase settlement-oracle divergence.
Polymarket settles on CHAINLINK (col chainlink_price), but we observe COINBASE WS
(cb_spot). Near window close (time_left<=60s), when chainlink_price and cb_spot
disagree about which side of the strike (start_price) we're on, the SETTLEMENT follows
chainlink. Test: in the last 60s, if chainlink-implied side != coinbase-implied side
(or chainlink is decisively past strike while the book underprices it), buy the
chainlink-implied favourite, hold to resolution. NOTE prior art (oracle_mechanics.py)
says the two oracles track closely and the determinism edge already uses Coinbase as a
fast proxy — so this may be THIN or redundant. Quantify: how often do they disagree,
and is there residual EV BEYOND the plain determinism rule (control for it)?` },
  { key: 'disagree', label: 'E4 two-sided determinism', prompt: `${PRE}

HYPOTHESIS E4 — two-sided / "disagreement" determinism.
The live determinism edge only fires when the book favourite AGREES with spot
(consistent). Test the COMPLEMENT: last 60s, when the book favourite (yes_mid>=0.5 ->
yes) DISAGREES with the spot-implied favourite (sign of dist_strike_bps), the book may
be wrong -> FADE the book = buy the SPOT-implied side. Require |dist_strike_bps|>=5.
Hold to resolution. OOS + shuffled null. (This is a directed bet the spot-implied side
beats the book-implied side when they disagree.)` },
  { key: 'momentum', label: 'E5 momentum-continuation', prompt: `${PRE}

HYPOTHESIS E5 — late-window momentum-continuation.
When spot is moving AWAY from strike with momentum in the final minute (sign of
spot_vel_10s == sign of dist_strike_bps, i.e. the favourite's lead is GROWING), the
outcome is even more locked than the book prices. Test last 60-120s: among consistent
favourites, does conditioning on momentum-away (vel reinforcing the lead) lift EV over
the plain determinism baseline? This must BEAT the plain determinism rule (control), not
just be positive. OOS + that control baseline.` },
  { key: 'crosswin', label: 'E6 cross-window persistence', prompt: `${PRE}

HYPOTHESIS E6 — cross-window persistence / autocorrelation.
Does a symbol's PRIOR 15m window outcome or move predict the NEXT window? E.g., after a
strong directional window, does the next window's open drift continue (momentum) or
revert? Build per-symbol consecutive-window features (prev outcome_up_clean, prev
|move|, prev fav_won) and test if they predict a tradeable entry early in the next
window. Prior art found prev_fav_lost weak -> high bar. OOS + shuffled null. Likely dead;
confirm cleanly.` },
]

const AUDITS = [
  { key: 'survivorship', label: 'A1 survivorship audit', prompt: `${PRE.split('MANDATORY')[0]}

AUDIT A1 — survivorship of the 26 RETIRED strategies.
26 mean-reversion strategies were disabled (enabled:false in strategies.yaml). Were
they killed for sound CAUSAL reasons, or are we cherry-picking survivors (survivorship
bias)? Read strategies.yaml (the disabled ones) + their data/portfolios/<id>.json and
data/jsonl/<id>/trades.jsonl. Summarize: how many, their aggregate PnL, WHY each family
was retired (per STATE.md / docs), and whether any disabled config actually looks
positive on the clean window (a missed edge) vs genuinely dead (mean-reversion refuted).
Output the AUDIT structured fields. Read-only.` },
  { key: 'execrealism', label: 'A2 execution-realism', prompt: `${PRE.split('MANDATORY')[0]}

AUDIT A2 — execution realism for the two LIVE edges (determinism, stale-quote).
Quantify the paper->live execution gap using the parity ledgers
(data/research/ledgers/{det,sq}_full.parquet) and the joined data:
  (a) ADVERSE-SELECTION fill: research.sim.fills_v2.maker_buy_fill is adverse-selected
      by construction. Compare taker walk_buy fills vs a pessimistic fill model for both
      edges; how much EV survives a realistic (worse) fill?
  (b) CAPACITY/DEPTH: using research.dataset.feeds.load_l2_ladders, walk_buy at stake
      $10/$25/$50/$100 and report EV decay vs size (where does slippage eat the edge?).
  (c) LATENCY for stale-quote specifically (determinism latency already done in gauntlet):
      re-key sq entries on the book 1/3/5/10s after the signal; EV decay.
Output AUDIT fields with the decay numbers. This is the single most decision-relevant
gap for going live.` },
  { key: 'sqdrift', label: 'A3 sq-curve drift', prompt: `${PRE.split('MANDATORY')[0]}

AUDIT A3 — stale-quote frozen-curve stability/drift.
The sq edge depends on a FROZEN empirical curve P(Up|z) (data/research/stale_quote_curve.json,
fit 05-23..29). Is that curve STABLE over time, or does it drift (silently decaying the
edge)? Re-fit the curve per UTC day (or per 3-day block) over the clean window using the
loss_patterns._fit_curve machinery; measure calibration drift (how much p_up shifts per z
bin across periods; reliability of the frozen curve on later days via
research.lib.stats.reliability_curve). Does the FROZEN (05-23..29) curve stay calibrated
on the fresh 06-01..04 days, or has it drifted? Output AUDIT fields + a drift metric.` },
]

// ---- run: all scans + audits in parallel (single-stage, independent) ----
phase('New-edge scan')
const edgeJobs = EDGES.map(e => () =>
  agent(e.prompt, { label: e.label, phase: 'New-edge scan', schema: EDGE_SCHEMA }))
const auditJobs = AUDITS.map(a => () =>
  agent(a.prompt, { label: a.label, phase: 'Audits', schema: AUDIT_SCHEMA }))

const results = await parallel([...edgeJobs, ...auditJobs])
const edges = results.slice(0, EDGES.length).filter(Boolean)
const audits = results.slice(EDGES.length).filter(Boolean)

const promising = edges.filter(e => e && e.verdict === 'promising')
log(`edge scan done: ${promising.length}/${edges.length} promising — ${promising.map(e => e.hypothesis).join(', ') || '(none)'}`)

return { edges, audits, promising_count: promising.length }
