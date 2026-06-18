# Honest-Settlement Fix + Full Re-Validation — Design

**Date:** 2026-06-18
**Status:** approved (user delegated execution incl. full re-validation)
**Origin:** the 2026-06-18 diagnosis found the project's research labels are optimistically biased.

## Context / why

Every backtest, sweep, and re-score in this project settles via `edge_lab.cl_outcomes()` →
`resettle_chainlink.chainlink_outcome_by_slug()`, which **reconstructs** the Chainlink outcome as
`cl_end >= cl_start` from as-of-captured prices. Verified against Polymarket's **official on-chain
resolution** (`settlements.jsonl`, what real money paid): the reconstruction disagrees on **17.4% of
clean fills, ~4:1 optimistically biased** (27 recon-WIN→official-LOSS vs 7 the other way),
concentrated near-strike — exactly where the determinism edge lives. On official settlement the
deployed strategies are breakeven-to-negative (det_lwd +$0.03/fill, not +$0.48; det_d12_dual −$0.78;
fav_disagree −$2.89). Cross-check: paper-settled-on-official ≈ live-realized (fill drag ≈ $0), so the
official outcome is ground truth and the loss is the *signal*, not execution.

This mislabel is the root cause of deploying −$170 of phantom edges. It poisons every past number and
every future edge-hunt. **Fixing it is the prerequisite for trusting any result.**

## Goal

1. Make the research stack settle on the **official on-chain outcome**, pinned to real-money truth.
2. Re-validate the deployed strategies on honest labels → decide `det_lwd_live` posture.
3. **Full re-run** of the hypothesis pipeline on honest labels → answer "does ANY edge survive
   truly-settled clean data?"

## Non-goals

- No new strategy/coin/market work this round (that comes *after* labels are trustworthy).
- No real-money changes except the `det_lwd_live` posture decision (present-first).

## Architecture

```
Gamma /markets?slug=X&closed=true  ──(batched, all 10,249 window slugs)──┐
                                                                        ▼
research/dataset/official_outcomes.py  ── parse outcomePrices -> UP/DOWN/None
                                                                        ▼
                              data/research/official_outcomes.parquet (cache)
                                                                        ▼
official_outcome_by_slug()  ──prefer official, fall back to reconstructed (logged)──┐
                                                                                    ▼
edge_lab.cl_outcomes()  ── now returns OFFICIAL-settled slug->outcome ── SINGLE CHANGE POINT
                                                                                    ▼
        every downstream tool settles honestly: rejudge_clean, hypothesis_sweep/select/verify, atlas
```

### Component 1 — `research/dataset/official_outcomes.py` (the backfiller)

- Batched async fetch of `/markets?slug=<slug>&closed=true` for every distinct window slug in
  `joined_15m` (10,249), with bounded concurrency + retry/backoff (mirror `clients/gamma.py` batching).
- Parse with the executor's proven logic (`live_executor.py:235-253`): `outcomePrices` → first index
  with price ≥ 0.99 → map `Up/Yes`→UP, `Down/No`→DOWN; unresolved/missing → None.
- Cache `{slug, official_up (1/0/NaN), source}` to `data/research/official_outcomes.parquet`;
  incremental (skip slugs already cached). CLI: `uv run python -m research.dataset.official_outcomes`.
- Coverage report: % of slugs with an official outcome (expect high; the rest fall back).

### Component 2 — settlement source switch

- Add `official_outcome_by_slug() -> DataFrame[slug, cl_up]` (reads the cache; for missing slugs,
  fall back to `chainlink_outcome_by_slug()` and count them).
- `edge_lab.cl_outcomes()` returns this instead of the raw reconstruction. lru_cache preserved.
- Keep `chainlink_outcome_by_slug()` intact (used for the bias/coverage comparison; not the default).

### Component 3 — parity test `tests/research/test_official_outcomes.py`

- For every slug in `settlements.jsonl` (non-backfill), `official_outcome_by_slug` must equal the
  booked `outcome`. n≈400, the real-money ground truth. Zero mismatches allowed.
- Plus: cache-parse unit test (outcomePrices `["1","0"]`→UP, `["0","1"]`→DOWN, unresolved→None) and a
  fallback test (missing slug → reconstructed value, flagged source).

### Component 4 — re-validation (run after the fix lands)

1. **Deployed strategies:** `rejudge_clean --stake 5` on official labels → honest clean CIs for
   det_lwd_live, early_disagree_live, det_d12_*, fav_*, tadiv. Present `det_lwd_live`'s corrected CI →
   user decides keep-live / demote.
2. **Full sweep on honest labels:** rebuild any cached frames that embed outcomes, then
   `hypothesis_sweep --future-start 2026-06-12` → `hypothesis_select` → `hypothesis_verify
   --fill-model live` → `edge_atlas`. The question: any spec with clean-future CI-lo > 0 on OFFICIAL
   settlement + non-duplicate? Document survivors (likely few/none) in
   `docs/research/HONEST_SETTLEMENT_2026-06-18.md`.
3. **Bias quantification:** report reconstructed-vs-official EV delta per family (how optimistic the
   whole project's history was).

## Data flow / caching

- One-time backfill ~15 min (10k slugs). Cache makes re-runs instant. Forward windows: re-run the
  backfiller (incremental) before each future re-validation.
- `cl_outcomes()` is `@lru_cache` — the switch is transparent to callers.

## Testing

- Parity test (Component 3) is load-bearing: it pins the new label to real money.
- Existing research tests that assumed reconstructed outcomes may shift; update expected values to
  official where they assert specific win/loss, or mark the comparison explicitly.
- Full suite green before any `strategies.yaml` change.

## Risks

- **Gamma coverage gaps** (a slug not resolved / not found) → fall back to reconstruction, logged; if
  coverage is low on a date range, flag it (don't silently mix).
- **lru_cache staleness** across a backfill refresh within one process — clear the cache after refresh
  (mirror the `set_future_override` cache-clear pattern).
- **Rate limiting** on 10k Gamma calls → bounded concurrency + backoff; the cache means we pay it once.
- **Honest verdict may be bleak** — expect the full sweep to show few/no survivors on official labels.
  That is the point: a true negative is worth more than a phantom positive.
