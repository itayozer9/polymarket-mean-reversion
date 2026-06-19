# Honest-Settlement Re-Validation — Results (2026-06-18/19)

Spec: `docs/superpowers/specs/2026-06-18-honest-settlement-revalidation-design.md`
Plan: `docs/superpowers/plans/2026-06-18-honest-settlement-revalidation.md`

## TL;DR

The project's research labels were **optimistically biased**: every backtest/sweep/re-score settled
on a *reconstructed* Chainlink outcome (`cl_end >= cl_start` from as-of prices), which disagrees with
Polymarket's **official on-chain resolution** on **6.5% of all windows / ~17% of traded (near-strike)
windows, ~4:1 optimistic**. We fixed the settlement to use the official outcome (parity-pinned to
real money, 288/288), backfilled it for all 10,237 resolved windows, and re-ran everything.

**On honest settlement, NO strategy and NO swept hypothesis has a positive edge on clean data.**
- Deployed/paper strategies: every one is breakeven-to-negative (table below).
- Full sweep (2,681 hypotheses): **zero** survivors under the honest gate (future n≥30, future
  CI-lo > 0, seed-robust, non-duplicate).

The "edges" this project chased were the sum of three artifacts — stale-book frequency inflation,
Coinbase-vs-Chainlink settlement gap, and near-strike reconstruction mislabeling. With all three
removed, there is currently no harvestable edge on Polymarket 15m crypto Up/Down.

## The fix (validated)

- `research/dataset/official_outcomes.py` — fetches the official outcome (`/markets?slug=X&closed=true`
  → `outcomePrices`, the executor's proven parse) for every window slug; caches to
  `data/research/official_outcomes.parquet`; retry+backoff (a first run at 16 workers hit Gamma
  rate-limits → 27% coverage; lower concurrency + retry → **100% coverage, 10,265 slugs**).
- `edge_lab.cl_outcomes()` — the single function every backtest/sweep/re-score settles through — now
  returns the official outcome (reconstructed-Chainlink fallback for any gap; none in practice).
- **Parity test** `tests/research/test_official_outcomes.py`: official fetch == real-money booked
  outcome for all traded slugs — **288/288, 0 mismatches** (21 unresolved skipped). The honest label
  reproduces what real money was actually paid.
- Project-wide bias: **6.49% of all windows** disagree recon-vs-official (664/10,237); the bias
  concentrates near-strike where the determinism/disagree edges trade (~17% there), and on the
  *traded* slugs it is ~4:1 optimistic.

## Deployed/paper strategies — honest clean-data verdict (`rejudge_clean`, official, live_guarded)

| strategy | recon EV/fill (biased) | **OFFICIAL EV/fill** | CI95 | verdict |
|---|---|---|---|---|
| det_lwd_live | +$0.48 | **−$0.10** | [−0.78, +0.57] | breakeven-neg |
| fav_momentum | +$0.55 | **−$0.58** | [−1.22, +0.00] | phantom (was the "only passer") |
| fav_lowvol | +$0.39 | −$0.03 | [−0.42, +0.39] | breakeven-neg |
| fav_deepdown | +$0.00 | −$0.03 | [−0.24, +0.16] | neg |
| det_d12_wide | +$0.20 | −$0.23 | [−0.75, +0.25] | neg |
| det_d12_dual_live | −$0.64 | −$0.37 | [−1.29, +0.50] | killed (−$103 real) |
| tadiv_approx_v1 / ret3 | +$0.06 / −$0.13 | −$0.37 / −$0.22 | — | neg |
| fav_disagree / early_disagree | — | −$0.53 / −$1.32 | (thin) | killed/demoted |

**Not one has clean CI-lower > 0.** `fav_momentum` — the single config that "passed" on reconstructed
labels (+$0.55, CI-lo +0.17) — is −$0.58 on official labels: it was pure mislabeling.

## Full sweep on honest labels — 2,681 hypotheses

`hypothesis_sweep --future-start 2026-06-12` → `select` → `verify --fill-model live --extended-known`,
all settled on official outcomes. Honest survivor gate = future n≥30 AND future CI-lo > 0 AND
seed-robust (mean−2sd > 0) AND max_jaccard < 0.5.

**Survivors: NONE.** The shortlist's best by seed-future EV:
- `det_0066` (det) future +$2.18 [2.02, 2.34] but **n=7 future fills** — unvalidatable; determinism
  family (breakeven-neg live on honest data).
- `det_0028` / `det_0024` (det) seed-future +$1.9 / +$1.7 but **0 future fills** under the live model.
- Specs with enough future fills — `psettle_2314` (n=39), `psettle_2306` (n=167), `ta_divergence_2544`
  (n=44) — all have future CI-lo < 0 (negative).

Every positive point-estimate is either thin-future (n<10) or determinism-family; every spec with a
robust future sample is negative. Zero deploy-paper-candidates.

## Implications

1. **Past EV was optimistic project-wide.** Treat every pre-2026-06-18 backtest number as inflated;
   the honest pipeline is now the default (`cl_outcomes` → official).
2. **No current edge.** Under honest settlement the book-lag / determinism / disagree / favourite-value
   / settlement-print / TA-divergence families are all breakeven-to-negative on clean data. The
   program-success bar (≥1 edge with clean CI-lo > 0 + positive clean live book) is met by nothing.
3. **det_lwd_live** (last live strategy) is breakeven-negative (−$0.10 re-score / +$0.003 realized) —
   recommend demote to paper; no validated edge justifies real-money exposure.
4. The honest apparatus is the real asset now: any *future* edge-hunt (more coins, new families,
   different markets) is finally trustworthy because the labels match real money.
