# Binance(+Coinbase) composite vs the Chainlink print (2026-06-10)

**Question.** Windows settle on the Chainlink print; our live signal is Coinbase WS.
Chainlink aggregates volume-weighted across venues and Binance dominates crypto volume —
does a Binance(+Coinbase) composite predict the Chainlink print better than Coinbase
alone? If yes it should (a) cut the near-strike flip problem (Coinbase disagrees with the
settled outcome in ~37% of windows finishing within 2bps of strike) and (b) improve the
live AGREE gate / distance features of `det_d12_dual_live`.

**Pre-registration.** `docs/research/test_ledger.md` B1/B2/B3, registered 2026-06-10
**before** any future-block reveal. Dev-only fitting (venue offsets, regression weight);
composite variant for the gate chosen on dev+holdout; future revealed once at the end.

**Code.** `research/dataset/binance_fetch.py` (fetch), `research/analysis/binance_composite.py`
(`--fetch-check` / `--measure` / `--gate`), pure-helper tests in
`tests/research/test_binance_composite.py` (12, all green).

---

## 1. Data

- **Binance**: 1-second klines (`GET /api/v3/klines?interval=1s`, public REST, no key,
  host api.binance.com), symbols BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT, 2026-05-22..2026-06-09
  UTC (19 full days; the window universe ends 2026-06-09 04:45 UTC, so 06-10 contains no
  study windows). 150ms inter-request sleep, limit=1000 (~87 req/symbol-day), resumable
  per symbol-date parquet → `data/research/binance_1s/<sym>_<date>.parquet`. The
  `interval=1s` path worked for the whole range — the aggTrades fallback was implemented
  but never triggered.
- **Coinbase**: the live collector's WS feed via `research/dataset/feeds.py::load_spot`
  (event-time ~1Hz).
- **Chainlink**: `data/live_chainlink/` on-chain Aggregator via
  `dual_oracle_features.load_chainlink_aged`, asof tolerance 120s (the pipeline's
  convention). CL_strike = asof(window_start), CL_close = asof(window_start+900s).
- **Universe & truth**: `edge_lab.load_base()` (6,593 windows 05-23→06-09; splits dev
  05-23..27 / holdout 05-28..31 / future 06-01..09) and `edge_lab.cl_outcomes()`
  (slug→cl_up). Our asof reconstruction reproduces `cl_up` with **0 mismatches** on
  6,573 CL-matched windows.

### Alignment (causality)

A Binance kline's close is only fully known 1s after its openTime, so the Binance leg is
asof'd on `openTime+1000` — at a boundary query t the value used is the close of the last
FULLY-CLOSED second (strictly ≤ t, ~0-1s stale). Coinbase event-time asof is the same ~0-1s
stale. Fair race, no in-second lookahead.

### Coverage (gate B3)

**100.00% on all four symbols** — 1,641,600 / 1,641,600 seconds each over the 19-day
window (6.57M klines total, no empty seconds on these pairs, no aggTrades fallback, no
host rotation needed; ~45 min wall time at the polite rate). **B3 PASS** — the B1/B2
negatives below are not data-limited.

## 2. The two structural facts that frame everything

**(a) Binance trades in USDT; the print is USD.** The raw Binance leg sits a ~flat
**−12.2..−12.5bps below the CL print** (dev, btc/sol; a USDT premium ≈ $1.0012). It is a
*level* basis, near-constant across a 15-minute window, but it **drifts across days**
(btc dev-fit constant left a 1.3→4.4bps T=0 median error on holdout). Consequences:
- any CL-*level*-referenced use of Binance needs a fitted, drifting offset — fragile;
- any *own-strike* use (proxy close vs proxy at window open — exactly what the engine's
  `dist_strike_bps`/`consistent` and the gate substitution compute) cancels the basis
  to first order — robust. The study therefore reports both framings and pins the
  deployable claims to the own-strike one.

**(b) Our "Chainlink print" is the slow on-chain Aggregator, ~25s behind spot.** Real
settlement is the near-real-time Data Streams feed (`phase0a_settlement_feed.md`); what we
collect (and what the entire research program settles against, including the deployed dual
gate's validation) is the on-chain Aggregator. A dev-only lag probe (median |cb(t_end−T) −
CL_close| over T): btc 1.03bps at T=0 → **0.53bps at T=25s**; sol 1.58 → 0.87 at T=25-30s.
Independently confirmed the same day by the oracle-print study (OP1, test_ledger): all four
feeds are ≈**33s-heartbeat** oracles, so the answer asof t_end reflects spot from roughly
half a heartbeat back. So |proxy − CL_close| tables partly measure "who looks like spot
25s ago", mechanically favouring laggier feeds at T=0. The decision-relevant metric is the
**near-strike sign agreement with the settled cl_up**, which is the same truth the deployed
edge's PnL is already measured in.

## 3. Composite construction

C(t) = w·k_bn·BN(t) + (1−w)·k_cb·CB(t), per-coin multiplicative offsets k = median(CL/venue)
fit on **dev windows only**; w ∈ {1.0, 0.7, 0.5} plus a dev-fitted pooled no-intercept
regression weight (CL−CB on BN−CB, bps space).

Dev-only fits (full data; (k−1)·1e4):

| coin | Binance offset | Coinbase offset |
|---|---|---|
| btc | −12.49 bps | +0.26 bps |
| eth | −12.51 bps | −0.12 bps |
| sol | −12.35 bps | −0.11 bps |
| xrp | −12.09 bps | −0.30 bps |

Binance (USDT) sits a uniform ~12.1–12.5bps from the CL USD print on dev; Coinbase is on
it to within ±0.3bps. Pooled dev regression weight **w_fit = 0.514** — in the LEVEL
regression the print looks like a roughly 50/50 blend, but see §4: after the offset is
removed, the Coinbase leg alone still tracks the print better at every horizon.
Aligned universe: 6,573 windows with CL + all proxies at all horizons (0 dropped);
`cl_up` truth reconstruction: 0 mismatches.

The frame, with all proxy columns, is cached at
`data/research/binance_composite_windows.parquet`.

## 4. Print prediction — |proxy − CL_close| at T ∈ {0,30,60}s (bps)

Median / p90, pooled across coins (per-coin in `/tmp/final_run.log` + recomputable from
the cached frame). **Coinbase wins every horizon, every split, every coin.**

**FUTURE (n=3,138):**

| proxy | T=0s | T=30s | T=60s |
|---|---|---|---|
| coinbase | **2.88 / 9.15** | **1.55 / 5.40** | **3.33 / 10.95** |
| bn (w=1.0) | 5.10 / 12.29 | 4.35 / 9.75 | 5.50 / 13.75 |
| comp (w=0.7) | 4.10 / 11.00 | 3.35 / 7.82 | 4.54 / 12.42 |
| comp (w=0.5) | 3.53 / 10.18 | 2.69 / 6.79 | 3.90 / 11.64 |
| comp (w_fit=0.51) | 3.59 / 10.25 | 2.72 / 6.84 | 3.98 / 11.73 |

**ALL splits (n=6,573):** coinbase 1.87/0.98/2.04 (medians at T=0/30/60) vs bn
3.00/2.42/3.01, comp(w=0.5) 2.20/1.37/2.21. Dev (n=1,916): cb 1.25/0.64/1.33 vs bn
1.95/1.52/1.97. Holdout (n=1,519): cb 1.34/0.67/1.34 vs bn 2.35/2.03/2.18 — note the bn
degradation dev→holdout (1.95→2.35 at T=0) = the USDT basis drifting away from its
dev-fit constant; Coinbase needs no such correction and doesn't drift.

Two structural reads:
- **T=30 < T=0 everywhere** (cb future 1.55 vs 2.88): the print is an ~33s-heartbeat
  snapshot, best matched by spot ~25s earlier (§2b). Errors at T=0 are dominated by the
  oracle's own lag, not by venue choice.
- The future block's errors are ~2× dev/holdout across ALL proxies (cb 1.25→2.88) —
  June's higher volatility widens the per-heartbeat gap for everyone; it also shrinks
  the ≤2bps near-strike population (249 dev / 203 holdout / 153 future).

## 5. Near-strike sign agreement with the settled outcome (KEY SLICE)

Slices: windows finishing within 2/5bps of strike by the benchmark's measure (engine
Coinbase close vs engine strike — the frame in which the ~37% number was published).
Two framings per proxy: **vs CL_strike** (the task's primary definition; basis-sensitive)
and **vs own strike** (deployment frame; basis-immune). Cells are
`vs-CL-strike% / own-strike%` disagreement with the settled cl_up.

| split, band | n | coinbase | bn(1.0) | comp(0.7) | comp(0.5) | comp(fit) |
|---|---|---|---|---|---|---|
| dev ≤2bps | 249 | 20.9 / 32.5 | 24.9 / 30.5 | 22.5 / 31.7 | 21.3 / 32.5 | 21.3 / 32.5 |
| dev ≤5bps | 540 | 14.6 / 21.7 | 18.3 / 20.6 | 16.3 / 20.7 | 15.0 / 21.3 | 15.0 / 21.1 |
| holdout ≤2bps | 203 | 17.7 / 32.5 | 26.6 / 32.5 | 22.7 / 33.5 | 22.2 / 33.0 | 21.7 / 32.5 |
| holdout ≤5bps | 438 | 11.6 / 21.5 | 19.9 / 21.5 | 16.7 / 22.1 | 15.3 / 21.9 | 15.1 / 21.7 |
| **future ≤2bps** | **153** | **22.9 / 35.9** | **29.4 / 31.4** | 26.8 / 32.7 | 24.2 / 34.0 | 24.8 / 34.0 |
| **future ≤5bps** | **362** | **17.7 / 26.8** | **25.7 / 24.9** | 23.2 / 26.2 | 19.6 / 26.2 | 20.2 / 26.2 |

(The CL-close-dist-sliced version tells the same story; see `/tmp/final_run.log`.)

Reading it:
- **The ~37% benchmark reproduces**: Coinbase own-strike ≤2bps disagreement is 35.9% on
  future (32.5% dev/holdout). It was never a Coinbase-feed artifact.
- **vs CL_strike (primary, the task's definition): Coinbase is the BEST proxy** in every
  split/band; every Binance admixture makes it worse (future ≤2bps 22.9→29.4%). The
  drifting USDT offset directly poisons any CL-level-referenced sign.
- **Own-strike: Binance is slightly better near the strike** (future ≤2bps 35.9→31.4%,
  −13% relative; ≤5bps 26.8→24.9%, −7%) — consistent in direction across all splits but
  always far below the registered 25% cut, and tiny in absolute terms (~7 windows of 153).
  Returns-wise Binance does lead the aggregate a little; level-wise it can't be trusted.
- **Chooser** (pre-registered, dev+holdout own-strike ≤5bps): bn(w=1.0) 21.0% vs
  comp(0.7) 21.4 / comp(0.5) 21.6 / comp(fit) 21.4 — a 0.4pp margin, i.e. a tie-zone;
  bn(w=1.0) selected for the gate test.

## 6. Gate application — det_d12_dual with the composite signal

Substitution (exactly what could ship): per-tick `dist_strike_bps`/`abs_dist_bps` and
`consistent` recomputed from the composite vs its own window-open value; `adverse_vel_10s`
keeps the Coinbase velocity source but projects on the composite's side sign; the AGREE
gate compares the Chainlink side to the **composite** side at entry. Book features, fill
model, laddered ceiling (0.78/0.85 @|cl_dist|≥20) untouched. Scored by
`rejudge_live_model.simulate_config` (v2 + live_guarded) at $5, seed 0 primary,
seeds 0-4 MC robustness. Baseline = deployed `det_d12_dual_live` pipeline, reproduced
exactly before the study (v2 future +$1.02 [0.75,1.29]; live_guarded future +$0.63
[0.20,1.06], 423 signals).

Composite per-tick NaN share 0.25% (full Binance coverage). FUTURE block, $5, seed 0:

| | baseline (Coinbase) | composite (bn w=1.0) |
|---|---|---|
| gated signals (future) | 321 | 296 (−8%) |
| v2: fills, EV/fill | 278, **+$1.021** [+0.753,+1.289] | 240, +$0.955 [+0.695,+1.218] |
| v2: WR, total | 84.5%, **$284** | 86.2%, $229 |
| live_guarded: fills, EV/fill | 131, +$0.634 [+0.199,+1.062] | 116, **+$0.843** [+0.437,+1.249] |
| live_guarded: WR / flip | 79.4% / 20.6% | 84.5% / 15.5% |
| live_guarded seeds 0–4 mean EV | **+$0.87** | +$0.84 |

- Dev/holdout (composite): lg +$2.16 (13 fills, WR 100%) / +$1.95 (29 fills, WR 96.6%) —
  flattering but tiny-n; baseline +$1.18/+$1.61.
- **Decision-set overlap**: 271 common windows, 152 baseline-only, 101 composite-only —
  the bn distance doesn't merely veto near-strike entries, it RE-TIMES a third of them
  (bn crosses the 12bps threshold at different ticks).
- **Paired delta on the 41 common future fills: +$0.310/fill [−0.037,+0.699]** — CI spans
  zero.

**B2 by the registered letter (seed 0): PASS** — EV +$0.843 ≥ $0.63 ✓, CI lower
+0.437 > 0 ✓, flip 15.5% < 20.6% ✓. **By the registered robustness report: a wash.**
The seeds-0–4 means INVERT the seed-0 ranking (+$0.84 vs +$0.87); under the
deterministic v2 fill model the baseline is ahead on EV/fill (+$1.021 vs +$0.955) and
clearly ahead on volume and total (+$284 vs +$229, +38 fills); the paired delta is not
significant. The seed-0 PASS is one favourable draw of the random zero-fill hazard, not
a stable improvement — the identical disposition as OP3's letter-pass the same day.

## 7. Verdicts

| gate | registered rule | result |
|---|---|---|
| **B3** coverage | ≥90% of seconds per symbol | ✅ **PASS** — 100.00% × 4 |
| **B1** print prediction | future ≤2bps disagreement cut ≥25% vs Coinbase | ❌ **FAIL** — primary framing WORSE (22.9→29.4%, −29% "cut"); own-strike −13% (35.9→31.4%), under the gate |
| **B2** dual gate | future lg EV ≥ $0.63 ∧ CI>0 ∧ flip lower (seed 0) | ⚠️ **letter-PASS, robustness-WASH → not shipped** (seeds-mean 0.84 vs 0.87; v2 favors baseline; paired CI spans 0; B1 mechanism failed) |

**Bottom line / recommendation:**
1. **Do not change the live signal feed.** Coinbase remains the better proxy for the
   settlement print as we measure it — and decisively so in any level-referenced use.
   The hypothesis "Binance dominates the Chainlink aggregate" is rejected for levels
   (the print is USD-venue-anchored; Binance brings a −12bps drifting USDT basis) and
   only homeopathically true for returns (own-strike sign −7..−13% relative, never
   clearing the gate).
2. The near-strike flip problem is **oracle noise, not venue mismatch**: with a
   33s-heartbeat print, a window finishing within 2bps is decided by which ~33s snapshot
   boundary lands where — no spot feed mix predicts that. The flip-rate floor is
   structural; the existing mitigations (AGREE gate + dist≥12 + max_ask) remain the
   right tools.
3. If anyone wants to keep a hand in: a `bn`-distance **paper twin** of det_d12_dual is
   defensible (it letter-passed B2 with better WR at −12% volume), but it is NOT a
   validated improvement and must not displace the deployed config.
4. The reusable asset from this study is the data + frame: 19 days × 4 symbols of 1s
   Binance klines (`data/research/binance_1s/`) and the per-window proxy frame
   (`binance_composite_windows.parquet`) — useful for any future cross-venue question
   (e.g., basis-as-regime-signal, which was NOT tested here and would need its own
   pre-registration).

## 8. Honest caveats

- The truth (cl_up) and the CL_close target come from the slow on-chain Aggregator, not
  the Data Streams settlement feed (§2b). All sign/EV results are exactly as decision-
  relevant as the rest of the program (same truth as the deployed gate's validation), but
  the |err| race at T=0 mechanically flatters laggier proxies, and a near-real-time
  settlement print could re-order the |err| table.
- The USDT/USD basis is fitted on dev and drifts; only own-strike (basis-cancelling)
  uses of Binance are deployment-grade. A live composite would need either a USDT/USD
  feed or the own-strike formulation (the gate substitution uses the latter).
- live_guarded applies the zero-fill hazard randomly; live misses are adversely selected
  (known optimism, identical for baseline and variant — comparisons are like-for-like).
- Decision-set changes between baseline and composite alter the sample; the paired
  common-window delta is reported to control mix effects.
- Process note: during the run, a garbled background-notification stream delivered a
  full set of plausible-looking but **non-reproducible** results (n=308 near-strike
  windows, comp(w=0.7) chosen, B2 FAIL at +$0.610) whose baseline rows did NOT match the
  pre-study `rejudge_live_model` reproduction. All numbers in this doc were verified by
  a clean foreground rerun against the repo data (3 consistent executions total:
  `/tmp/final_run.log` ×2 + `/tmp/verify_measure.log`/`verify_gate.log`); the phantom
  set was discarded and the ledger rows corrected. Reproduce anytime with:
  `uv run python -m research.analysis.binance_composite --measure --final --rebuild`
  then `--gate --final --seeds 0,1,2,3,4`.
