# Oracle-print study (2026-06-10) — predict the print, not the market

**Question.** Polymarket settles each 15m window on the Chainlink print (CL_close vs CL at window
open); our signal feed is Coinbase. The deployed primary (`det_d12_dual_live`) gates entries with a
BINARY "AGREE" check (Chainlink sign must match Coinbase sign at entry). Can a CONTINUOUS,
calibrated settlement-probability model P(cl_up | decision tick) (a) beat the book's own price as a
probability, (b) replace the binary gate at equal-or-better EV with more volume, and (c) open a new
inverse edge (fade overpriced near-strike favourites)?

**Pre-registration.** `docs/research/test_ledger.md` → "Oracle-print study (2026-06-10)", OP1–OP4
with gates G1–G3, written before any future-block number was computed. Protocol: model fit on dev
(05-23→05-27), isotonic on holdout (05-28→05-31), App-1 θ\* on dev only; future (06-01→06-09)
revealed once at the end. Code: `research/analysis/oracle_print_model.py` (+ unit tests
`tests/research/test_oracle_print_model.py`, 11 passing). Battery: `rejudge_live_model.
simulate_config`, $5 stake, seed 0, fill models **v2** (idealized) and **live_guarded**
(live-calibrated, deployment-realistic). CIs: slug-clustered bootstrap (n=2000).

**Data.** Base frame `edge_lab.load_base()` (5.09M ticks, 05-23→06-09, 6,593 windows; splits dev
1.49M / holdout 1.20M / future 2.41M ticks). Chainlink rounds `data/live_chainlink/` 05-22→06-10
(~48–49k observed rounds per coin). Labels = `cl_outcomes()` (poll-asof Chainlink settle — the
repo's ledger truth).

---

## OP1 — the print process (characterization; descriptive, all days)

`--characterize` output, 4 coins:

**Round physics.** All four feeds behave as ~33s-heartbeat oracles on this sample:
- inter-update dt (consecutive observed rounds): p50 **33s**, p90 35–37s, p99 46–47s, max 72–86s.
- Updates triggered early by price deviation exist but are a minority; |Δprice| at update:
  p5 0.10–0.19 bps, p50 1.9–3.1 bps, p90 8.0–11.6 bps. There is no visible hard deviation
  threshold above ~0.1–0.2 bps — i.e. in this regime the cadence is heartbeat-dominated, and each
  ~33s print simply picks up whatever the aggregate moved.
- Missed rounds (round_id gaps vs the ~15s poll): only 1.2–2.0% — the poll sees nearly every round,
  so inter-update stats are barely right-biased.
- Convention check: rounds-asof (updated_at ≤ close) settlement sign agrees with the repo's
  poll-asof `cl_outcomes` label on **98.14%** of 6,573 windows — the residual ~1.9% is the
  poll-lag convention ambiguity, concentrated in near-strike closes (an irreducible label-noise
  floor for ANY predictor, including the book).

**Predictability of the final print T seconds out** (per window: CL_now & oracle age at close−T,
concurrent Coinbase CB_now at the same tick, CL_close at close; gap = CB-vs-CL distance demeaned
per coin; flip = final print on the other side of the CL strike than CL_now):

| T (s) | n | sd(CL_close−CL_now) bps | p90 abs | P(>2bps) | P(>5bps) | P(>10bps) | corr w/ CB gap | beta | R² | resid sd | flip% | flip% at CL-dist<5bps |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 30 | 2,940 | 3.99 | 6.01 | 39% | 14% | 3.2% | 0.75 | 0.88 | 0.57 | 2.62 | 8.2% | 17.2% |
| 60 | 3,807 | 6.30 | 8.66 | 55% | 25% | 7.4% | 0.52 | 0.84 | 0.27 | 5.40 | 10.0% | 23.4% |
| 120 | 4,713 | 10.02 | 14.48 | 70% | 41% | 19.0% | 0.36 | 0.89 | 0.13 | 9.34 | 13.0% | 29.4% |
| 180 | 5,498 | 13.90 | 19.24 | 76% | 52% | 27.2% | 0.28 | 0.96 | 0.08 | 13.33 | 14.0% | 32.7% |
| 300 | 6,260 | 18.22 | 25.82 | 82% | 61% | 38.1% | 0.19 | 0.80 | 0.03 | 17.91 | 17.1% | 35.6% |

(n grows with T because late-window tick coverage, needed for CB_now, thins near the close.)

Headlines:
- **30s out the final print is predictable to ±2.6 bps** (residual sd after the CB-gap regression);
  the concurrent Coinbase-vs-Chainlink gap maps ~0.85–0.95 : 1 (beta) into the final print at every
  horizon — Chainlink converges to spot, mechanically. At T=30 the gap alone explains 57% of the
  variance; by T=300 the gap is mostly noise (R² 0.03) and the diffusion term dominates.
- Sign-flips vs the CL strike are exactly where the AGREE gate lives: 8% of windows flip side in
  the last 30s overall, but **17–36% flip when CL sits within 5 bps of its strike** — near-strike
  late entries are coin-flips no matter what the book says.
- Oracle age at decision adds a second-order dispersion penalty (T=60: sd 6.1 bps at age<5s vs
  8.0 bps at age>30s) — stale answer ⇒ more pending catch-up.

**Implication for the model:** the print T seconds out ≈ CL_now + ~0.9·(CB−CL gap) + diffusion
noise ∝ σ√T. That is exactly the pre-registered feature set (z_cl, basis, z_cb, age interaction).

---

## OP2 — settlement-probability model

`--fit`: logistic on dev (n=992,930 ticks; 2,194 dropped for NaN features, fail-closed), features
standardized; isotonic fit on holdout (n=784,579) was **NOT adopted** (it worsened the isotonic's
own out-of-sample, dev: Brier 0.13160 → 0.13483), so the raw logistic is the model.

Coefficients (standardized):

| feature | coef | reading |
|---|---|---|
| z_cl (CL dist / σ√T) | **+0.744** | diffusion term on Chainlink's own distance |
| d_cl (raw CL dist) | **+2.113** | deep CL locks are near-deterministic |
| basis (CL−CB gap) | **−0.589** | the print converges to Coinbase: CL above CB ⇒ P(up) falls |
| z_cb / d_cb | −0.020 / +0.027 | redundant given z_cl + basis (cb ≈ cl − basis) |
| age_gap (staleness × (z_cb−z_cl)) | +0.054 | stale answer ⇒ weight spot slightly more |
| coin eth/sol/xrp | −0.07/−0.02/−0.09 | small per-coin intercepts |
| intercept | −0.214 | dev-period down-drift (regime term — a known risk, see caveats) |

Dev Brier 0.13160, holdout Brier 0.13239 (logloss 0.4336) — stable across the seal.

### G1 reveal — FUTURE calibration (vs both baselines)

Future eval population: 1,547,424 ticks / 3,134 windows (book_healthy, 1≤tl≤600s, features+label
present). AGREE 2-leaf probabilities fit on dev: P(CB side wins | agree)=0.840, |disagree)=0.407.

| predictor | Brier (future) | logloss (future) |
|---|---|---|
| **model** | **0.13456** | 0.46753 |
| book yes_mid | 0.13750 | **0.42281** |
| AGREE 2-leaf | 0.14858 | 0.47092 |

Paired per-tick Brier differences (slug-clustered CI):
- book − model: **+0.00294 [+0.00010, +0.00588]** — the model IS better than the book's own price,
  and the CI clears zero, but the improvement is **3.4× short of the pre-registered ≥0.01 margin**
  → **G1 FAIL**.
- AGREE − model: **+0.01402 [+0.01201, +0.01615]** — the continuous model clearly dominates the
  binary gate as a probability (secondary: PASS).

Reliability on future (the diagnosis): the model is **sharper than the book mid-range but
overconfident in the tails** — pred 0.023 → realized 0.090; pred 0.976 → realized 0.912 — while
the book's tails are nearly perfectly calibrated (pred 0.954 → realized 0.966). That tail
overconfidence is also why the book wins on logloss while losing on Brier. Mechanism: the logistic
saturates on d_cl/z_cl fit to the calm dev regime; the future block is choppier, so "near-locked"
ticks flip more often than dev taught the model. (The holdout-fit isotonic would not have fixed
this — it failed its own OOS check on dev and was correctly not adopted.)

---

## OP3 — Application 1: continuous gate on det_d12 (vs binary AGREE)

Decision universe: `det_d12_dual_live` config WITHOUT the agree gate (mode consistent, t 1–180s,
dist≥12bps, ask 0.50–0.78 + adaptive 0.85 at |cl_dist|≥20, adverse_vel≤2) → **579 signals** full
sample (0 dropped for missing CL features); the deployed binary AGREE gate keeps **423** (73%).
Gate: trade iff P_settle(buy side wins) ≥ θ\*.

**Battery sanity** (must reproduce the documented baseline before anything else): AGREE baseline
full-sample at $5, seed 0 — v2 **$+1.126 [+0.889,+1.351]** n_sig 423 / fills 372; live_guarded
**$+0.867 [+0.507,+1.204]** fills 192 — exactly matches `rejudge_live_model.jsonl` (2026-06-10).

**Dev sweep** (live_guarded, seeds 0–4 mean, $5; AGREE on dev: EV/fill $+0.860, total $+23.2,
n_sig 53). Selection rule (pre-registered): max dev total s.t. dev EV/fill ≥ AGREE's.

| θ | n_sig | fills | EV/fill | total | v2 EV |
|---|---|---|---|---|---|
| 0.500 | 65 | 33.6 | 1.454 | 49.1 | 1.550 |
| 0.550 | 62 | 31.2 | 1.524 | 47.4 | 1.646 |
| **0.575** | **59** | **30.2** | **1.709** | **51.6** | **1.705** |
| 0.650 | 56 | 28.6 | 1.752 | 49.4 | 1.741 |
| 0.725 | 46 | 22.2 | 2.314 | 51.3 | 2.199 |
| 0.800 | 42 | 21.4 | 2.189 | 47.0 | 2.227 |
| 0.875 | 36 | 18.6 | 2.233 | 41.6 | 2.237 |
| 0.950 | 13 | 7.2 | 2.294 | 16.6 | 2.316 |

**θ\* = 0.575** (constraint satisfiable; persisted to `data/research/oracle_print_model.json`).
The dev surface is well-behaved: EV/fill rises monotonically-ish in θ, total is flat-topped
0.575–0.725 — no knife-edge.

### G2 reveal — FUTURE block ($5, seed 0)

| arm | n_sig | fills | EV/fill | 90% CI | WR | total |
|---|---|---|---|---|---|---|
| model-gate θ\*=0.575, v2 | 402 | 350 | **$+1.095** | [+0.841,+1.337] | 84.6% | **$+383.3** |
| model-gate, live_guarded | 402 | 174 | $+0.880 | [+0.513,+1.231] | 82.2% | $+153.0 |
| model-gate, live_guarded seeds 0–4 | 402 | — | $+1.034 (sd 0.214) | — | — | — |
| ungated, v2 | 446 | 392 | $+0.793 | [+0.544,+1.039] | 80.1% | $+310.9 |
| ungated, live_guarded | 446 | 197 | $+0.795 | [+0.416,+1.141] | 80.7% | $+156.5 |
| AGREE (deployed), v2 | 321 | 278 | $+1.021 | [+0.753,+1.289] | 84.5% | $+283.7 |
| AGREE, live_guarded | 321 | 135 | $+1.015 | [+0.652,+1.382] | 85.9% | $+137.1 |
| AGREE, live_guarded seeds 0–4 | 321 | — | $+0.867 (sd 0.169) | — | — | — |

**G2 by the pre-registered letter: PASS** — live_guarded seed-0 EV/fill $+0.880 ≥ $0.63 (the
registered baseline figure), CI lower +0.513 > 0, n_sig 402 ≥ 321 (**+25% volume**).

**Honesty note (stricter same-run reading).** The registered $0.63 was the AGREE baseline's future
EV from the rejudge jsonl (full-frame RNG path). Recomputing AGREE on the isolated future frame in
this run gives $1.015 at seed 0 and $0.867 across seeds 0–4 — i.e. a single live_guarded seed
carries ~±$0.2–0.4 of Monte-Carlo noise, and against the same-run seed-0 recompute the model gate
loses ($0.880 < $1.015) while across seeds it wins ($1.034 > $0.867). The deterministic v2 model is
the noise-free comparison: **model-gate $1.095 vs AGREE $1.021 per fill, +81 signals, total +$100
(+35%)**. Conclusion: per-fill EV is statistically indistinguishable from AGREE (every comparison
flips within noise), while the volume and total-PnL gains are consistent across all readings. The
model gate also clearly dominates UNGATED (v2 +$0.30/fill at −44 signals — it cuts genuinely bad
trades, not random ones).

---

## OP4 — Application 2: near-strike fade (inverse-det)

Rule (fully pre-registered, no dev fitting): book prices the favourite ≥ 0.75 BUT the model says
P(fav wins) ≤ 0.60, time_left 60–360s, book_healthy, cheap-side ask ≤ 0.35 → buy the CHEAP side at
its YES-equivalent ask (fill ceiling 0.40), first qualifying tick per window.

**Decision frame:** 1,321 decisions (dev 421 / holdout 370 / future 530), balanced across coins
(btc 373 / eth 335 / xrp 312 / sol 301), ~70/day. Entry-ask distribution (capacity): p10 0.12,
p50 **0.23**, p90 0.27, max 0.35. Median time_left 202s. Sides: 390 UP-longshots / 931 DOWN.
**Mechanism confirmed at the decisions:** median |cl_dist| = **4.8 bps** (p90 13.5) — the book is
quoting a 0.79–0.84 favourite while Chainlink sits ~5 bps from its strike, where OP1 measured
17–36% sign-flip rates. Model p_fav at decision: median 0.565.

**Overlap with fav_disagree** (`decisions_for("fav_disagree")`, 363 decisions): Jaccard =
**0.101** → this is NEW volume, not the E4/fav_disagree edge re-labelled (that edge requires a
book-vs-spot SIGN disagreement; the fade mostly fires when signs agree but the magnitude is far
too small to justify 0.80).

Splits (battery, $5; dev/holdout were previewed pre-reveal, future revealed once):

| split | n_sig | v2 fills | v2 EV/fill [CI] | v2 WR | lg fills | live_guarded EV/fill [CI] | lg total |
|---|---|---|---|---|---|---|---|
| dev | 421 | 411 | $+5.84 [+4.20,+7.51] | 38.4% | 248 | $+4.10 [+2.66,+5.67] | $+1,018 |
| holdout | 370 | 362 | $+3.34 [+2.19,+4.60] | 33.4% | 206 | $+2.70 [+1.28,+4.37] | $+557 |
| **future** | **530** | 508 | **$+6.19 [+4.95,+7.54]** | 44.9% | **271** | **$+5.87 [+4.50,+7.30]** | **$+1,589** |

future live_guarded seeds 0–4: $+5.92 (sd 0.56). **G3: PASS** (CI lower +4.50 > 0; n_fills 271 ≥
30; Jaccard 0.101 < 0.5).

**Post-hoc scrutiny of the revealed result** (diagnostics, no selection):
- Day-lumpiness: **9/9 future days positive** ($115–$686/day); top day = 21.8% of total. Dev 5/5
  positive days; holdout 3/4 (worst day −$39). 17 of 18 days positive overall.
- Per-coin (future, v2): btc $+7.10, eth $+7.05, sol $+5.09, xrp $+5.31 per fill — all four.
- By entry ask (future, v2): every bucket positive. The sub-0.10 tail (26 fills) is the jackpot
  zone — WR 73%, $+48/fill, ~40% of total — but stripping it still leaves $+3.0–4.9/fill across
  all other buckets (consistent with the repo's sq-deep-tail finding: do NOT floor out the cheap
  tail).
- Sides: UP-longshots WR 51.1% (178), DOWN-longshots WR 41.5% (330) — both far above the ~25%
  breakeven at 0.23 asks.
- Win-rate by regime: holdout 33% < dev 38% < future 45% — the future block was chop-friendly.
  Even the worst split (holdout) sits comfortably above breakeven, but a sustained-trend regime is
  the known bleed mode (same physics as sq).

**Why this can be real:** it is the favourite-longshot bias ([[favourite-value-edge-found]] family,
same family as the verified E4/fav_disagree) read through the SETTLEMENT oracle instead of through
Coinbase sign-disagreement. The book routinely prices "looks locked on the screen" at 0.80+ while
the print process says the window is still a ~55/45 coin-flip; the cheap side is then worth ~2× its
ask. The model's G1 tail-overconfidence is irrelevant here — the fade uses its MID-range (0.40–0.60)
where future reliability was good (pred 0.55 → realized 0.56).

**Biggest caveat (stated, not hidden):** the live fill model applies the zero-fill hazard RANDOMLY,
but live misses are adversely selected — for a fade, the cheap shares nobody sells you are exactly
the ones most worth having, so live EV/fill will land below the $5.87 backtest figure. The
dev→holdout→future stability, 4-coin breadth, and 96% v2 fill-rate bound this risk but only a
forward paper run prices it.

---

## Verdicts (per the pre-registered gates)

| application | gate result | verdict |
|---|---|---|
| OP1 characterization | descriptive | **DONE** — print is a ~33s-heartbeat process; T=30s residual sd 2.6 bps after the CB-gap regression; flips concentrate at \|cl_dist\|<5 bps |
| OP2 settlement model (G1) | **FAIL** (Brier edge +0.0029 < required +0.01; book wins logloss via better tails; model does beat AGREE-2-leaf +0.0140 CI>0) | **honest negative as a standalone calibration product** — keep as a feature/gate engine, do not advertise it as "better than the book" |
| OP3 continuous gate (G2) | **PASS by pre-registered letter** ($0.880 ≥ $0.63, CI +0.513 > 0, vol 402 ≥ 321); per-fill parity vs AGREE within MC noise on the stricter same-run reading; volume +25% and total +29–35% robust | **deploy-candidate as a PAPER twin** (`det_d12_print_v1`) A/B'd against `det_d12_dual_live`'s paper twin — NOT a live-gate swap on this evidence |
| OP4 near-strike fade (G3) | **PASS** (CI lower +4.50 > 0, n 271, Jaccard 0.101) + clean post-hoc scrutiny | **deploy-candidate — strongest result of the study; start as PAPER strategy** (fade_print_v1: fav_ask≥0.75, p_fav≤0.60, tl 60–360, cheap ask≤0.35, ceiling 0.40, $5) to price the adverse-selection haircut before any live money |

**One-line summary:** predicting the print beats predicting nothing (AGREE) but not the book's own
tails (G1 fail); as a TRADE FILTER it matches the deployed AGREE gate with +25% volume (G2
letter-pass); and as an INVERSE signal it surfaces a large, broad, split-stable fade edge (G3 pass,
future +$5.87/fill live-modelled) that is genuinely new volume vs fav_disagree.

---

## Reproduce

```
uv run pytest tests/research/test_oracle_print_model.py -q          # 11 pure-helper tests
uv run python -m research.analysis.oracle_print_model --characterize
uv run python -m research.analysis.oracle_print_model --fit          # dev fit, holdout isotonic
uv run python -m research.analysis.oracle_print_model --gate         # dev θ sweep (no future)
uv run python -m research.analysis.oracle_print_model --eval-future  # G1 reveal
uv run python -m research.analysis.oracle_print_model --gate --reveal   # G2 reveal
uv run python -m research.analysis.oracle_print_model --fade --reveal   # G3 reveal
```

Model artifact (coefs, scaler, θ\*): `data/research/oracle_print_model.json`. Importable API:
`from research.analysis.oracle_print_model import p_settle, p_settle_side` (frame needs
cl_dist_bps, cb_dist_bps, cl_cb_basis_bps, cl_oracle_age_s, time_left_sec, realized_vol, symbol;
returns NaN where features are missing — fail closed).

Known limitations stated: (1) per-tick `cl_dist_bps` uses the 15s-poll feed (the live engine sees
the same, so no lookahead, but a websocket round feed would tighten oracle_age); (2) the label
itself carries ~1.9% poll-vs-updated_at convention ambiguity (OP1 convention check); (3) live fill
model's random zero-fill hazard is an optimistic bound for the fade (adverse selection); (4) the
dev-fit intercept encodes the dev down-drift — refit cadence should follow the weekly review.

