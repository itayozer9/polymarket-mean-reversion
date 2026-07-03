# EDGE HUNT v2 — Theory-First Campaign Verdict (2026-07-02 → 07-03)

**Question:** after the honest-settlement reckoning (no edge survives official labels), is
there ANY robustly positive daily EV in Polymarket 5m/15m crypto Up/Down for a $5 taker?

**Answer: YES — exactly one family.** The cheap-**disagree**-side structure (buy the
spot-implied side when the book favourite contradicts it) survives every honest gate, was
confirmed by two independent methods, and is live again as `fav_disagree_live`. Every other
door tested closes. Full pre-registration: `test_ledger.md` § "HONEST EDGE HUNT v2".

Method spine (all verdicts): OFFICIAL on-chain labels (money-parity-pinned 288/288 + per-cent
re-settle test) · virgin block = entries ≥ 2026-06-19 (never seen by any pre-07-02 selection) ·
degraded epoch 06-05→06-12 excluded everywhere · live-2 fill model (580 clean real attempts,
holdout-validated) · window-clustered bootstrap · BH-FDR 10% per theory · one sealed reveal per
theory · $5 stakes.

---

## Scoreboard

| Theory | Mechanism | Verdict |
|---|---|---|
| **T2 disagree family** | book favourite contradicts spot; buy the cheap spot side | **SURVIVES ALL GATES → live** |
| T2 det/book-lag rest | book lags spot near-lock | det_d12/fav_lowvol fail consistency leg; sq/tadiv/oracle_fade/psettle/early_disagree_v1 killed | 
| T1 cross-book 5m↔15m | co-terminal no-arb violations | **CLOSED** (xb gate neutral; 0/145 virgin survivors; big-gap specs collapse to n≤7 forward) |
| T3 mispricing atlas | static miscalibration beyond costs | **no new family** — 4/4 virgin-confirmed cells ARE the disagree structure (earlier timing, CL-dist conditioned) |
| T4 flow (raw prints) | fade uninformed bursts | **CLOSED permanently** (0/11 virgin survivors; discovery positives were overfits) |

## The surviving edge — fav_disagree family

Virgin block (06-19→07-02, official labels, paper decisions): fav_disagree +$4.72/fill
[+2.90,+6.60] n=122 · fav_disagree_live +$3.46 n=79 · fav_disagree_d5 +$1.64 n=436.
BH-FDR p = 0.00025–0.0025 across 24 strategies. **Consistency leg** (full 06-12→07-02,
including its worst fortnight): +$1.05..+$2.70/fill, all CI-lo > 0. **Live-2 guarded fills**
on the recorded decisions: +$2.29 / +$2.57 / +$0.60 per fill at 56–66% fill rate, 5-seed
robust ⇒ ≈ **+$8–13/day per twin at $5 stakes**. Jaccard vs det_lwd_live: 0.07–0.11.
Spread across 9–10 of 14 virgin days and all four symbols — not a lottery.

Independent confirmation (T3 atlas, sealed reveal): all 4 positive candidate cells are
cheap-disagree at ask 0.30–0.50 / tl 300–900s (+16%..+35% net per $1, best n=191); the
funding side confirms too (cheap *consistent* longshots near expiry lose 40–56%/$1).

History note: this family was killed 2026-06-18 at −$66 real on an n=34 clean-era verdict —
with the full sample that kill was a small-sample mistake. **Re-armed 2026-07-03 ~10:40 IDT**
(user-approved): $5/trade, $50/UTC-day hard cap, isolated book. Pre-registered stop rule:
official-settled ≤ −$0.50/fill with CI-hi < 0 → recommend stop.

Honest caveats: (1) clean-era fortnight was negative — the process is regime-heterogeneous;
expect losing weeks; judge by the stop rule, not by any n<40 window. (2) Capacity is
~$10-depth books; this is a $10–30/day-scale edge at current sizing, not more. (3) All EVs
subtract a theoretical fee live doesn't pay (zero-fee verified) — numbers are conservative.

## Closed doors (do not re-test without new data + new pre-registration)

- **Cross-horizon (T1):** xb twin +$1.04 CI[−0.65,+2.87] n=130 — gate not passed. fam_xh:
  145 clean-discovery specs (dominated by the never-tested 5m-instrument legs, +$2–11/fill
  in discovery) → **0/145 survive the virgin reveal**. The g=2bps 5y specs remain individually
  suggestive (n=87, +$1.56, CI-lo +0.33, seed-ok) but fail BH — revisit ONLY with ≥3 more
  weeks of forward data and a fresh registration.
- **Flow (T4):** raw CLOB prints (73M) mined once, honestly — 11 discovery positives all
  collapse (0/11). Book- AND print-derived flow signals are dead.
- **T2 remainder:** det_d12 + fav_lowvol are virgin-fortnight riders (fail consistency);
  oracle_fade, tadiv twins, det_sqp v1/v2, fav_deepdown, early_disagree_v1 killed by rule.

## Follow-ups now running

- `early_disagree_cl_v1` **paper twin** (atlas refinement: tl 450–900s, ask 0.30–0.40,
  Chainlink agree+dist≥2bps gate instead of Coinbase dist≥10) — standard 14-virgin-day gate.
- **Nightly honest scoreboard** (launchd 06:15 IDT): official labels (15m+5m) → re-settle →
  `data/research/paper_official/scoreboard.md` + money-parity test. The paper book cannot
  silently lie again.

## New facts discovered

- **Zero taker fees live** (348,600/348,600 prints at 0bps; no fee in real fills) — the
  0.07·p·(1−p) in paper/research is theoretical-only; every EV here is conservative.
- **5m oracle basis ≈10× the 15m one** (post-fix: 0.65% of ≥20bps Coinbase moves settle
  opposite; 34.6% near-strike disagreement). Critical for anything 5m-settled.
- First-ever official 5m labels: 31,238 clean-era windows, 100% resolved, cache now 46,771.

## Infrastructure shipped (the durable asset)

`official_outcomes.py` (--timeframes/--since, `official_only_by_slug`) · `resettle_official.py`
(+ 4-test money parity) · fill model live-2 (holdout block) · `xbook.py` + `trade_prints.py`
(+ 9 look-ahead guard pins) · sealed sweeps `xh_sweep.py`/`flow_sweep.py` (discover/reveal) ·
`atlas_honest.py` (registered splits) · `build_joined_chunked.py` (kill-resumable frames) ·
`scripts/nightly_honest.sh` + launchd.
