# External inputs — Binance derivatives, macro calendar, 5m cross-book, E6 closeout (2026-06-11)

**Question.** Every validated edge in this repo monetizes ONE mechanism — the order book
reprices toward spot/oracle reality too slowly (EDGE_ATLAS_2026-06-10). Do genuinely NEW
external inputs (a) split the deployed edges' EV into avoid/keep regimes, or (b) carry an
independent edge?

Pre-registration: `docs/research/test_ledger.md` § "External-inputs campaign (2026-06-11)"
(XI1–XI5, written before any conditional/future number was computed, including two
pre-reveal amendments logged there: the robust-z scale floor and the joint-cascade regime).
Tooling: `research/analysis/external_inputs.py` (+ `tests/research/test_external_inputs.py`,
15 unit tests), fetcher `research/dataset/binance_deriv_fetch.py`, calendar
`data/research/events_calendar.csv`. Artifacts: `data/research/external_inputs/`.

## 0. Protocol (frozen before results)

- Splits: `edge_lab.load_base()` + local relabel, dev 05-23..27 / holdout 05-28..06-04 /
  **future 06-05..09** (LC2 campaign convention), future revealed once per workstream.
- Fill models: `rejudge_live_model.simulate_config` — **v2** (idealized fixed-2s L2 walk)
  and **live_guarded** (live-1 calibrated hazard/latency/guards), **$5**, seed-0 CI headline,
  seeds 0–4 mean on live future legs. CIs: window-clustered bootstrap (slug clusters).
- Honesty caveats carried: 06-05..09 is NOT virgin for the deployed configs (their
  unconditional EV was revealed 06-10/11) — XI1/XI3 claim regime SHAPE only, no new edges;
  the live model's random zero-fill hazard is adversely-selected-OPTIMISTIC.
- Jaccard: slug-set overlap vs all `rejudge_live_model.decisions_for` configs +
  `fav_disagree_live045` + `early_disagree`.

## 1. New external data (all fetched/parsed this session)

| input | source | coverage | notes |
|---|---|---|---|
| funding rates | fapi `/fapi/v1/fundingRate`, 8h prints | 05-22..06-10, 4 coins | settled prints, asof-joined at entry (staleness ≤9h) |
| open interest | fapi `/futures/data/openInterestHist` period=5m | 05-22..06-10, 288/day/coin, full | ~30-day retention — fetched just in time |
| cascade proxy | built from existing `binance_1s` (forced-order history is NOT public) | 05-22..06-10 minutes | 1-min vol/range robust-z; registered q99.5 joint per coin |
| joint-cascade (amended) | same | same | ≥3 of 4 coins simultaneously vol_z≥q95 ∧ rng_z≥q90 (d+h thresholds) |
| macro calendar | static, from-knowledge schedule (approx=1 on every row) | 16 events 05-15..06-09, ±15min windows | **no CPI / FOMC decision lands in-window** (May-12 CPI before, Jun-10 CPI / Jun-16-17 FOMC after) — declared coverage gap |
| 5m co-terminal books | `data/live/*.csv.gz` 5m slugs with start%900==600 | 2.04M ticks, 6,829 windows, 05-23..06-09 | the 5m market sharing a 15m window's close |

### Cascade-proxy validation (pre-registered leg) — the registered proxy FAILED it

The registered per-coin q99.5 proxy (vol_z thresholds 23–27, rng_z 7.6–8.9; flags
0.13–0.24% of minutes) does **NOT** flag the known 3-coin live burst at 2026-06-10 17:45
(minute 1781113800: xrp vol_z 21.0/rng 4.6, eth 8.5/4.9, sol 7.3/6.0, btc 5.7/3.7). Even
q99 misses it. **Finding: the live-loss signature is the cross-coin JOINT move, not
per-coin extremity** — per coin the incident was only a ~q97–99 minute. The amended
joint-cascade regime (≥3/4 coins hot at q95/q90; 406 minutes = 1.41%) captures it exactly
(eth/sol/xrp hot — "the 3-coin burst"). Amendment was logged in the ledger BEFORE any EV
was computed; both regimes were then tested.

## TL;DR

| WS | test | verdict | headline |
|---|---|---|---|
| 1 | XI1 deriv-regime gates | ❌ no gate — direction REVERSED | stress regimes ENRICH det EV (dual×hi_doi future $1.32 vs $0.33); worst bursts are NOT Binance-extreme |
| 1 | XI2 cascade-conditional disagree edge | ❌ dead | cascade subsets 5–72 decisions, sub-EV ≤ unconditional |
| 2 | XI3 event-window gate | ❌ no gate | in-event d+h EV +$2.9 (lg) > out-event; knives don't concentrate; no CPI/FOMC in window (gap) |
| 3 | XI4 5m↔15m bound violation | ✅ **deploy-paper-candidate** | future lg +$1.36 [0.12,2.68] n=60, seeds $1.81; v2 +$1.84 [0.67,3.03]; maxJac 0.21; all 6 grid cells future-positive |
| 4 | XI5 E6 closeout | ❌ DEAD | future v2 −$0.575 [−1.04,−0.10] n=298 — the 06-01..04 'pass' was macro-lump luck |

## 2. XI1 — Binance-derivative regime gates: honest negative, with a reversed sign worth knowing

Decision rule (registered): avoid-gate iff (i) d+h in-regime lg EV < 0 with CI-upper < out-regime
EV, (ii) future agrees (seeds 0–4), (iii) share ≥ 5% and d+h fills ≥ 10. **0 of 24 config×regime
slices pass leg (i)** — not one in-regime d+h EV is even negative where n ≥ 3.

The interesting part is the reversal. In-regime EV is consistently HIGHER for the det family,
d+h AND future agreeing (live_guarded $5/fill):

| config × regime | share | d+h in | d+h out | future in (seeds μ) | future out |
|---|---|---|---|---|---|
| det_d12_dual × hi_doi | 25% | **+$1.77** [1.06,2.37] n=26 | +$1.04 | **+$1.32** | +$0.33 |
| det_d12_dual × joint-cascade | 2.8% | +$2.32 n=3 | +$1.18 | +$1.27 | +$0.65 |
| det_lwd × joint-cascade | 4.1% | **+$1.72** [1.23,2.28] n=6 | +$0.33 | +$1.19 | +$0.53 |
| det_lwd × funding_extreme | 28% | +$1.10 [0.59,1.52] n=35 | +$0.15 | +$0.65 | +$0.47 |
| fav045 × hi_doi | 29% | +$5.54 [0.12,12.6] n=22 | +$4.00 | +$2.86 | +$1.41 |

Funding-extreme shows no consistent direction (dual future in $0.24 < out $0.91 while lwd/early
go the other way) — noise. Reading: **when the derivative tape is stressed, books lag spot MORE
and the deployed edges earn more per fill** — the same one-mechanism story, seen through a new
input. An OI/cascade *boost* (sizing up in-regime) is a hypothesis for a future registered test,
NOT shipped here (XI1 was registered as an avoid-gate test only).

Descriptive killer for the cascade-protection hope: **all 20 worst live window-groups (4 configs
× 5 worst) sit OUTSIDE both cascade regimes** — e.g. fav045's −$15.98 (3 fills) and the
det_d12_dual −$15.32 group fired in minutes the Binance proxy calls calm. The multi-coin
PM-book bursts that hurt are strike-crossing events, not volume/range cascades; a Binance-side
filter cannot see them coming. (Corroborates BC1/BC2: the protection has to live at the
executor's per-window-ts cap, not in an external feed.)

## 3. XI2 — cascade-conditional disagreement edge: dead

Registered legs (uplift ≥ +$0.25/fill over unconditional, seed-0 CI > 0, ≥ 20 future cascade
fills): **all fail in all 4 slices.** fav_disagree has 5 (cascade) / 16 (joint) of 363 decisions
in-regime; early_disagree 9 / 72 of 603; future cascade fills 0–6. Where measurable, the
in-cascade EV is not richer: early×joint d+h +$1.39 [−0.57,+3.32] vs unconditional +$2.32
[1.53,3.11]. The fav×cascade d+h +$21.6/fill (n=4) is one 3-coin burst-jackpot — anecdote, not
conditioner. The disagreement edge fires off PM-book-vs-spot disagreement directly; the upstream
Binance stress state adds nothing tradeable at these n.

## 4. XI3 — macro-calendar windows: no avoid-gate (and the sign is again 'wrong')

Pooled over the 4 deployed configs, ±15 min around the 16 scheduled releases:

- in-event fills are 2.2–2.7% of all fills (33 v2 / 21 lg across all splits);
- **in-event d+h EV is POSITIVE and above out-of-event**: lg +$2.93 [0.98,4.52] n=18, v2 +$2.20
  [0.76,3.48] n=29, vs out-event +$1.67 / +$1.59;
- knives (pnl ≤ −80% of stake) do NOT concentrate in-event: knife share 1.9–2.3% ≈ fill share;
- future in-event n=3 (−$3.03) — too thin for anything;
- tier-1-only slice n=6–7, EV ≈ −$0..−1 — unresolvable.

Verdict: registered leg 1 fails (needs < −$0.50 with CI-upper < 0; got +$2.9). **No event gate.**
Declared limits: calendar dates/times are from-knowledge (approx=1), and the window contains NO
CPI print or FOMC decision — this clears claims/ISM/ADP/JOLTS/minutes/GDP/PCE/NFP, it says
nothing about CPI-day. The scheduled-volatility around tier-2 prints behaves like XI1's stress
regimes: more repricing → the edges earn, not bleed.

## 5. XI4 — 5m↔15m cross-book bound violation: deploy-paper-candidate

The co-terminal 5m market (opens at 15m_start+600s, same close) prices the same close against
its own strike K5. Strike monotonicity binds the two books: K5 ≥ K15 ⇒ P(close>K15) ≥
P(close>K5). The rule buys the implied 15m side when the 15m book violates the bound at
EXECUTABLE prices (15m ask + margin ≤ 5m bid of the implied side, ≥$1 displayed behind the 5m
reference quote, strike gap ≥ g, ceiling 0.90).

- Join health: 1.34M joined overlap ticks, 6,315 15m windows with a sane co-terminal 5m book.
- Registered d+h-only chooser → **m=0.03, g=2bps** (max d+h lg total $361; eligibility n≥40).
- **Future, revealed once: lg +$1.36 [+0.12,+2.68] n=60 fills (seed 0), seeds 0–4 mean $1.81±0.27;
  v2 +$1.84 [+0.67,+3.03] n=98.** All four LC4 legs pass (EV>0 both models, CI>0, ≥30 fills,
  max Jaccard 0.21 < 0.5).
- Grid robustness: every cell (m ∈ {3,5,10}c × g ∈ {2,5}bps) is future-positive under BOTH
  models; the strictest (m=0.05,g=5) does +$2.87 [1.06,4.73] lg on 34 fills.
- Profile: fires at/just after the 5m OPEN (median entry sec 600, p75 621), buys the cheap 15m
  side (median ask 0.39), 56% DOWN, |gap| median 5.3bps; 386 decisions/18d ≈ 21/day; all 4
  coins positive (btc +$0.50 … eth +$2.26 v2); future days 4/5 positive (06-09 is the partial
  day, n=5, all losses — within seed noise).
- Sensitivity (descriptive): requiring the 5m book ≥5s old keeps it (+$1.69 [0.56,3.00] v2
  future); ≥15s decays to +$0.95 [−0.10,+1.99]. The information event is the **5m open itself:
  a brand-new book seeded off CURRENT spot** — a free, exchange-published "spot anchor" the 15m
  book hasn't absorbed. Mechanism = the atlas's one mechanism, harvested through a new input,
  in a region (cheap side, tl≤300s) the deployed configs barely touch.
- Honesty: the raw mid-vs-mid conflict test shows the 5m book is NOT generally smarter (5m
  implied side right 53% vs 15m favourite 59% on 572 conflict windows) — the tradeable content
  is exactly and only the no-arb bound at executable quotes. K5 is the collector-captured
  start_price (recorded within the first seconds of the 5m window); a live twin should read
  spot at the open directly. The zero-fill-hazard optimism caveat applies as everywhere.

**Recommendation:** add `xb_5m15m_v1` (margin 0.03, gap 2bps, ceiling 0.90, tl 600–895s,
$1 5m-ref-notional floor) as a PAPER strategy twin; do not touch live configs.

## 6. XI5 — E6 cross-window persistence closeout: DEAD

The frozen rule (REVERSAL after prev-window |move| ≥ 50bps, consecutive windows, entry ≤ 60s)
on future = 06-05..09, one shot, no re-tuning:

| economics | dev | holdout | future 06-05..09 |
|---|---|---|---|
| original ($10, quoted ask, CB-clean) | +$1.64 [−0.87,+4.17] n=39 | +$1.04 [+0.04,+2.07] n=249 | **−$0.72 [−1.69,+0.27] n=298 (total −$216)** |
| registered gate: v2 $5 Chainlink | +$0.39 d+h pooled | — | **−$0.575 [−1.04,−0.10] n=298** |
| live_guarded $5 Chainlink | +$0.33 d+h pooled | — | −$0.27 [−0.88,+0.31] n=180 |

Future CI fully negative under the registered gate. The prior "letter-pass" on 06-01..04
(+$1.28) was exactly the 4-lumpy-macro-correlated-days artifact the original ⚠️ suspected.
Cross-window reversal is not a tradeable edge; **E6 is closed.**

## 7. What this changes

1. **Ship nothing to live.** One new PAPER twin recommended: `xb_5m15m_v1` (XI4).
2. **Stop looking for external avoid-gates for the det/disagree family.** Two independent
   stress vocabularies (Binance derivative stress, scheduled macro prints) both say the same
   thing: stress regimes are where these edges EARN. The protective work stays where BC2 put
   it (per-window-ts burst caps, bankroll caps).
3. The cascade proxy itself was a finding: per-coin extreme-tail z-spikes miss the real
   PM-burst events; the cross-coin JOINT regime (≥3/4 coins q95/q90) is the right vocabulary
   if anyone revisits — it flags the 06-10 incident exactly.
4. A registered follow-up candidate (NOT run here): in-regime size-UP (hi_doi / joint-cascade
   boost) for det_d12_dual / det_lwd — the XI1 reversal is consistent across d+h and future,
   but it was observed under an avoid-gate registration, so it must be re-registered as its
   own hypothesis on a fresh block.

## Files

- `research/dataset/binance_deriv_fetch.py` — funding/OI fetcher (resumable, polite)
- `research/analysis/external_inputs.py` — the whole study; subcommands build/proxy/xi1..xi5
- `tests/research/test_external_inputs.py` — pure-helper unit tests
- `data/research/events_calendar.csv` — static macro calendar
- `data/research/binance_deriv/` — funding_{sym}.parquet, oi_{sym}_{date}.parquet
- `data/research/external_inputs/` — cascade_minutes.parquet, cascade_thresholds.json,
  ticks5m_coterminal.parquet, proxy_validation.json, xi1_gates.jsonl, xi2_edge.jsonl,
  xi3_events.json, xi4_5m15m.json, xi5_e6.json
