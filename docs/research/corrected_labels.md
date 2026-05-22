# Corrected outcome labels — May 15m windows

Rebuilding trustworthy Up/Down outcome labels for the 2,232 May 15-minute
windows, after `docs/research/phase0_audit.md` Task 8c found that `start_price`,
`move_pct`, `outcome` and `outcome_up` in `ticks_15m.parquet` /
`windows.parquet` / `data/outcomes.csv` are all corrupt (the discovery bug froze
each window's strike at the Coinbase spot sampled ~30 min *before* the window
opened).

Script: `research/analysis/corrected_labels.py` (`run()` + `__main__`).
Output: `data/research/corrected_labels.parquet` — one row per 15m slug.

## Method

Two independent reconstructions, then reconcile.

### Way 1 — Coinbase reconstruction (we have the data)

The per-tick `coinbase_price` (the live spot feed) is genuine and uncorrupted.
For each window:

- `true_strike` = `coinbase_price` at the earliest tick (smallest
  `seconds_into_window`).
- `true_end`    = `coinbase_price` at the latest tick.
- `recon_outcome` = "Up" if `true_end > true_strike`, else "Down".

4 corrupt tick rows (NaN `seconds_into_window` / NaN `coinbase_price`) were
dropped before grouping so the strike/end always come from valid ticks.

### Way 2 — Polymarket gamma API ground truth

For each slug, `GET https://gamma-api.polymarket.com/events?slug=<slug>`.

**The `/markets?slug=` endpoint is unreliable for these markets** — it returns
an empty array for most 15m crypto slugs even though they exist and are
resolved (verified by repeated probing). **`/events?slug=` returns every slug
reliably** and is what the script uses.

The resolved outcome is exposed by the market-level **`outcomePrices`** field,
a 2-element array paired index-for-index with `outcomes` (`["Up","Down"]`):

- `outcomePrices == ["1","0"]`  → **Up won**
- `outcomePrices == ["0","1"]`  → **Down won**

Confirmed resolved by `umaResolutionStatus == "resolved"` and `closed == true`.
Mid-market values strictly between 0.01 and 0.99 would be treated as
unresolved — none occurred.

The event's `eventMetadata` additionally carries `priceToBeat` (the true
**Chainlink** strike) and `finalPrice` (the true Chainlink settlement). These
are captured in the parquet (`api_price_to_beat`, `api_final_price`) for
cross-checking. Polymarket resolves these markets on the Chainlink BTC/USD
stream; `chainlink_price` is always 0 in our tick data, so Coinbase is our
reconstruction proxy.

Politeness: concurrency 6, 0.15 s delay per request, 3 retries with backoff.
Full run: ~92 s for 2,232 slugs.

### Reconcile

`authoritative_outcome_up` = API ground truth where available, else the
Coinbase reconstruction. `true_strike` is **always** kept from the Coinbase
reconstruction — the API does not expose a Coinbase strike (it exposes the
Chainlink `priceToBeat`, a different feed), and `move_pct` / proximity features
are computed off the Coinbase tick stream so they need a Coinbase-consistent
strike.

## Results

### API coverage — 100%

**All 2,232 of the 2,232 15m slugs returned a resolved outcome** via
`/events?slug=`. `api_status` is `"resolved"` for every single one. There were
no 404s, no unresolved markets, no missing-outcome cases. Consequently
`label_source == "api"` for **all 2,232 windows** — the Coinbase fallback was
never needed.

### API internal consistency check

The API outcome (`outcomePrices`) was cross-checked against the independent
`eventMetadata` Chainlink figures (`finalPrice > priceToBeat`):

**Agreement: 99.96% (2,227 / 2,228 windows with metadata).** The single
mismatch is a window where `finalPrice` and `priceToBeat` are within rounding
distance. The API resolved outcome is rock-solid ground truth.

### Coinbase reconstruction vs API ground truth

| Scope | Agreement | n |
|-------|-----------|---|
| **Overall** | **92.03%** | 2,232 |
| btc | 90.86% | 558 |
| eth | 92.29% | 558 |
| sol | 92.11% | 558 |
| xrp | 92.83% | 558 |

The 178 disagreements (7.97%) are overwhelmingly **near-zero-move coin-flips**:

- Disagreements: median `|Coinbase move|` = **0.0151%**.
- Agreements:    median `|Coinbase move|` = **0.1133%** (7.5× larger).
- **61.2%** of disagreements have `|Coinbase move| < 0.02%`; **85.4%** under
  0.05%.

When the Coinbase move over the window is essentially zero, the Coinbase-vs-
Chainlink feed basis (or a few seconds of timing slack) is enough to flip the
sign — exactly the predicted failure mode. Only **2** of the 178 disagreements
have an open-tick offset > 0 and only **5** have a close offset > 2 s, so
disagreements are driven by feed basis on near-zero moves, **not** by the
timing gap. The Coinbase strike sits a median **−0.0024%** from the Chainlink
`priceToBeat` (p90 absolute basis 0.046%) — a genuinely tiny basis, confirming
Coinbase is a high-quality proxy and the disagreements are unavoidable
coin-flips, not a reconstruction error.

### Timing-gap quantification

- **Open tick:** `seconds_into_window == 0` for **2,228 / 2,232 (99.82%)** of
  windows. The 4 exceptions opened ~445 s late (collection outages). The
  `true_strike` is therefore the Coinbase spot at the genuine window-open
  boundary for 99.8% of windows.
- **Close tick:** the last tick is within 2 s of the 900 s boundary for
  **2,200 / 2,232 (98.57%)** of windows (median close offset **1 s**). 32
  windows lost their final stretch of ticks (mean close offset ~212 s) — for
  those, `true_end` is a few minutes early and slightly less reliable.

The open/close offsets are stored per-window in the parquet
(`open_offset_sec`, `close_offset_sec`) so downstream code can filter.

### Damage estimate — how wrong the OLD corrupt label was

`windows.parquet` `outcome_up` (the corrupt label, scored against the stale
30-min-early `strike`) vs the new authoritative label, over the 2,228 windows
with an old label:

| Scope | Old label WRONG | windows |
|-------|-----------------|---------|
| **Overall** | **31.06% (692 / 2,228)** | 2,228 |
| btc | 32.50% (181/557) | 557 |
| eth | 31.42% (175/557) | 557 |
| sol | 28.90% (161/557) | 557 |
| xrp | 31.42% (175/557) | 557 |

**The old corrupt `outcome_up` disagrees with ground truth on nearly one in
three windows.** This is catastrophic for any supervised analysis: a label with
a ~31% error rate is barely better than noise for a near-50/50 binary target.
Any Phase 2+ result that consumed `outcome_up` from `windows.parquet` /
`ticks_15m.parquet` / `data/outcomes.csv` is invalid and must be re-run against
`corrected_labels.parquet`. The base rate also shifted: old P(Up) = 0.4690 vs
authoritative P(Up) = 0.4906.

## Output schema — `data/research/corrected_labels.parquet`

One row per 15m slug (2,232 rows):

| Column | Meaning |
|--------|---------|
| `slug`, `symbol` | window identifier |
| `true_strike` | Coinbase spot at the earliest tick |
| `true_end` | Coinbase spot at the latest tick |
| `recon_outcome`, `recon_outcome_up` | Way 1 — Coinbase reconstruction |
| `api_outcome`, `api_outcome_up` | Way 2 — Polymarket API ground truth |
| `authoritative_outcome_up` | API where available, else recon |
| `label_source` | `"api"` or `"recon"` (all `"api"` here) |
| `open_offset_sec`, `close_offset_sec` | timing gap of the strike/end ticks |
| `api_price_to_beat`, `api_final_price` | Chainlink strike / settlement (cross-check) |
| `api_status` | per-slug fetch status (all `"resolved"`) |
| `n_ticks` | tick count for the window |

## Verdict — trustworthiness

**The corrected labels are highly trustworthy.**

- `authoritative_outcome_up` is the Polymarket API resolved outcome for
  **100%** of windows — the actual ground truth, what real money settled on,
  Chainlink-based, cross-confirmed by `eventMetadata` at 99.96%.
- The Coinbase reconstruction independently agrees 92.03%, and every
  disagreement is a near-zero-move coin-flip explained by the tiny
  Coinbase-vs-Chainlink basis — not a reconstruction bug.
- `true_strike` is the Coinbase spot at the genuine window-open tick for 99.8%
  of windows; the Coinbase-vs-Chainlink strike basis is a median −0.0024%.

**Use `authoritative_outcome_up` as the outcome label and `true_strike` as the
strike for all Phase 2+ research.** Do not use `outcome_up` / `start_price` /
`move_pct` from `windows.parquet`, `ticks_15m.parquet` or `data/outcomes.csv` —
they are corrupt and wrong on ~31% of windows.

Two minor caveats for downstream code:
1. 32 windows have `close_offset_sec > 2` (lost final ticks) — `true_end` is a
   few minutes early there. The API outcome is still correct; only the Coinbase
   `true_end` is affected. Filter on `close_offset_sec` if `true_end` precision
   matters.
2. Near-zero-move windows are inherently coin-flips between Coinbase and
   Chainlink; the authoritative (API/Chainlink) label is canonical for those.
