# TA Strategy Campaign — Design

> Comprehensive research campaign to find NEW paper strategies, using base-asset
> technical-analysis features, run through this project's existing anti-self-deception
> machinery. Survivors deploy as paper twins to monitor forward.

**Date:** 2026-06-16
**Status:** approved design → implementation plan next
**Approach:** A (extend the existing hypothesis pipeline with a TA feature layer)

---

## Why (context that shapes every decision)

This project's entire history is "edges that looked great were stale-data / look-ahead
artifacts." Almost every *directional* "predict the base asset → bet the matching side"
idea has already been tested and rejected (ORACLE_PRINT, BINANCE_COMPOSITE, EDGE_ATLAS,
external-inputs): the Polymarket book already reflects spot (**T−30s spot beats T−0**), the
settlement print is a lagging ~33s snapshot, and "model-beats-book" washed out across seeds.

The edges that survive are all **"rent on slow book repricing"** — mean-reversion /
disagreement harvested at different distances from settlement — and they are macro-correlated
(4 coins move together). So "more strategies of the same shape" does not diversify.

**Implication for this campaign:** TA must be tested in roles that don't just re-fight the
rejected directional bet. We test directional TA anyway (to get a real verdict, not an
assumption), but the bet is on the *filter*, *regime*, and *divergence* roles.

## Goal

Cast a wide net for new strategies (including base-asset TA), run every candidate through the
existing rigor stack, and deploy the survivors as **paper twins** the user can monitor forward.
A batch, not a single strategy. The user is open to all statistical profiles (uncorrelated /
higher-frequency / higher-EV) — judge candidates as they come.

## Non-goals

- No real-money deployment. Paper twins only; no `live:true` without explicit user go
  (`feedback_supervised_realmoney`).
- No new backtester. Reuse `hypothesis_sweep → select → verify → atlas` verbatim.
- No silent engine changes. TA features not already in the live tick dtype get FLAGGED for a
  separate engine-wiring plan, not auto-deployed.

---

## Architecture

```
cb_spot per-second tape (already in joined frame)
        │
        ▼
research/dataset/ta_features.py   ── causal TA indicators, as-of seconds_into_window
        │   (trend / momentum / volatility / mean-reversion / regime label)
        ▼
ta_features.parquet  ──1:1 join on (market_slug, seconds_into_window)──┐
                                                                       ▼
                                            slim base frame  +  TA columns
                                                                       │
                                                                       ▼
hypothesis_sweep.py   fam_ta_{directional,filter,regime,divergence}   (new family builders)
        │
        ▼
hypothesis_select.py  ── future-blind gates (n,dev_n,cpcv,full_ci_lo,latency,cap)
        ▼
hypothesis_verify.py --fill-model live  ── live-model future EV not negative
        ▼
edge_atlas.py  ── FDR control  +  Jaccard dedup vs every deployed edge
        ▼
SURVIVORS ──┬── expressible in existing engine mode → strategies.yaml live:false paper twin
            └── needs new engine code → FLAG in spec for a separate engine-wiring plan
```

### Component 1 — `research/dataset/ta_features.py`

Compute base-asset TA indicators on the per-second Coinbase spot tape (`cb_spot`), keyed to
each window's clock. **Every feature is causal**: computed only from spot at timestamps
≤ the tick's timestamp (as-of `seconds_into_window`). This project has been burned by
look-ahead twice; the feature module is the place to enforce causality once.

Indicator set:

- **Trend:** EMA(spot) slope, fast/slow MA-cross state, ADX-style directional strength.
- **Momentum:** RSI(spot), MACD histogram, n-second return.
- **Volatility:** ATR, realized vol (already present — reuse, don't recompute), Bollinger-band
  width, vol-of-vol.
- **Mean-reversion:** z-score of spot vs rolling VWAP/MA, distance from Bollinger band.
- **Regime label:** trend / range / high-vol, derived from the above.

Output: a parquet that joins 1:1 onto the slim base frame by `(market_slug,
seconds_into_window)`. One module. Fully unit-tested against hand-computed fixtures.

**Interface:** `build_ta_features(base_or_spot_df) -> DataFrame` indexed by
`(market_slug, seconds_into_window)`; a thin CLI to (re)build the parquet. Depends only on the
spot tape already in the joined frame — no new feeds.

### Component 2 — TA family builders in `hypothesis_sweep.py`

New `fam_ta_*` builders following the existing `(base_df, params) -> (cand_df, buy_yes_array)`
contract, each carrying an economic rationale string (pipeline convention). Registered into the
sweep grid alongside the existing families.

1. **`fam_ta_directional`** — TA trend/momentum says up → buy UP (and symmetric). The honest
   test the project expects to fail; run on clean data for a real verdict.
2. **`fam_ta_filter`** — layer a TA gate on the *proven* edges (e.g. only fade the odds-dip
   when spot is range-bound / RSI-neutral; skip when trending hard). Tests whether TA *lifts*
   an edge we already know is real.
3. **`fam_ta_regime`** — vol/regime window-selection (trade the disagree edge only in high-ATR
   regimes where overshoots are bigger).
4. **`fam_ta_divergence`** — TA detects a base-asset move the book hasn't repriced yet
   (TA-momentum vs stale odds). Most aligned with the "rent on slow book repricing" thesis;
   the likeliest genuine new edge.

Before authoring, check overlap with the existing `divergence_backtest.py`,
`cross_coin_leadlag.py`, `e5_late_momentum_continuation.py` so we extend rather than duplicate.

### Component 3 — Rigor pipeline (reused verbatim)

Each candidate flows through the existing gauntlet, unchanged:

- `hypothesis_select` future-blind gates: n≥40, dev_n≥12, CPCV≥80%, full_CI_lo>0,
  latency 5s & 10s EV>0, cap_10≥0.5.
- `hypothesis_verify --fill-model live`: live-model future EV not negative.
- `edge_atlas` FDR control.
- **Clean data only**: entry UTC ≥ 2026-06-12 11:00 (post stale-book fix).
- **Chainlink settlement**, not Coinbase (Coinbase EV is ~20–30% optimistic).
- **Future block revealed once** (pre-register; don't re-peek).
- **Jaccard dedup** against every deployed edge — a TA family that's just `early_disagree`
  relabeled gets rejected.

### Component 4 — Deployment (paper twins only)

- Survivors expressible in an **existing engine mode** (`consistent` / `disagree` / `psettle` /
  `xb` + param knobs) → add to `strategies.yaml` as `live:false` paper twins; validate
  `registry.load_strategies` parses before any restart; safe-window `run_combined` restart only
  (executor + existing books untouched).
- Survivors needing **new engine code** (most TA features aren't in the live tick dtype yet) →
  FLAG in the deliverable doc as a separate engine-wiring plan. Not deployed this round.

### Component 5 — Testing & deliverable

- `tests/` unit coverage for `ta_features` (hand-computed fixtures, causality assertions) and
  each new family builder (research-arithmetic parity).
- Deliverable doc `docs/research/TA_STRATEGIES_2026-06-16.md`: every family's verdict, including
  the negatives (the rejections are as valuable as the survivors).
- Full test suite green before any `strategies.yaml` change.

---

## Honest expectations

Directional TA will very likely fail (the book prices spot). The filter and divergence roles are
where a survivor is plausible. Most of the net comes back empty — that is the machinery working,
not a failure of the campaign. Success = a small number of *deduped, clean-data, Chainlink-settled,
live-fill-model-positive* paper twins running forward, plus a documented set of honest negatives.

## Risks

- **Look-ahead in TA features** — mitigated by computing every indicator as-of and unit-testing
  causality.
- **Re-discovering a deployed edge** — mitigated by Jaccard dedup.
- **Over-fitting the wide net** — mitigated by future-blind select gates + FDR + future-block
  reveal-once.
- **Feature needs engine work** — handled by the flag-don't-deploy rule.
```
