---
name: mean-rev-review
description: Use when the user asks for the weekly/periodic review of the polymarket-mean-reversion bot, OR after ~1 week of forward paper trading the determinism/stale-quote edges. Triggered by "weekly review", "review the mean-rev bot", "how did the strategies perform this week", "/mean-rev-review". Compares live-paper to backtest, mines trades_detailed for WR-lifting filters, decides on a small live test.
---

# Periodic review — polymarket-mean-reversion (determinism / stale-quote forward test)

**Context (read first):** the 2-week research hunt retired all mean-reversion
strategies — those markets are efficient-after-cost and the user's dip-buy thesis
was backwards. The live edge is **momentum/determinism (the book lags spot)**. As
of 2026-05-29 the bot runs ONLY 4 strategies (see `docs/research/edge_hunt_synthesis.md`):

| id | edge | rule | backtest |
|---|---|---|---|
| `det_lwd_v1` / `_capped` | Phase 1 determinism (primary, robust) | last 60s, |spot−strike|≥5bps, buy favourite ask≤0.90, hold to resolution | OOS +$1.68/tr, 91% WR |
| `det_sqp_v1` / `_capped` | Phase 2 stale-quote (secondary, high-variance) | mid-window, |model_p−mid|∈[8,30]¢ + spot jump≥8bps, hold | +$3.5/tr, WR ~50% |

`_capped` = `$50/day` max-loss breaker (live candidate); uncapped = true-edge measure.

This review is NOT the old mean-reversion sweep. It does: (1) live-paper vs
backtest drift, (2) **filter discovery from `trades_detailed.jsonl`** to lift WR +
profit (the user's explicit goal: skip bad times/conditions), (3) daily-cap
behaviour, (4) small-live-test decision.

## When to invoke

- "weekly review" / "review the bot" / "how did the strategies do this week"
- ~7+ days after the forward run started (STATE.md: "1-WEEK FORWARD RUN started")
- Before deciding on a small live test
- `/mean-rev-review`

## Steps — Working dir: `/Users/itayozer/dev/polymarket-mean-reversion`

### 1. Data sufficiency + trade counts

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion
echo "clean instrumented days (>=2026-05-23):"; ls data/live_l2/btc_*.csv.gz | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort -u | awk '$0>="2026-05-23"'
for sid in det_lwd_v1 det_lwd_v1_capped det_sqp_v1 det_sqp_v1_capped; do
  f="data/jsonl/$sid/trades_detailed.jsonl"; n=$( [ -f "$f" ] && wc -l < "$f" || echo 0 )
  echo "  $sid: $n settled trades"
done
```
If <50 det trades total, note the sample is thin (CIs wide) but proceed.

### 2. Rebuild the backtest dataset with the week's new data, refresh the OOS track

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion
uv run python -m research.build_joined            # rebuild joined_15m with new clean days
uv run python -m research.forward_validate        # per-day OOS track for the determinism rule
```
`forward_validate` prints the per-day det edge on fresh-spot data + the OOS summary;
append its output to `docs/research/forward_validation_log.md`.

### 3. LIVE-PAPER vs BACKTEST drift (the go/no-go gate: <30%)

Compares what the LIVE bot actually did (`trades_detailed.jsonl`, settled on the
true outcome) to the backtest expectation. Material drift = investigate fills/feed.

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && python3 <<'EOF'
import json, pathlib
EXP = {"determinism": (0.90, 1.45), "stale_quote": (0.50, 3.0)}  # (WR, $/trade) backtest
for sid in ("det_lwd_v1","det_sqp_v1"):  # uncapped = the unbiased edge measure
    f = pathlib.Path("data/jsonl")/sid/"trades_detailed.jsonl"
    rows = [json.loads(L) for L in f.read_text().splitlines() if L.strip()] if f.exists() else []
    if not rows: print(f"{sid}: no trades"); continue
    kind = rows[0]["strategy_kind"]; n=len(rows)
    wr = sum(r["won"] for r in rows)/n; pt = sum(r["pnl"] for r in rows)/n
    ew, ep = EXP[kind]
    dr = (pt-ep)/abs(ep)*100 if ep else 0
    print(f"{sid:14} n={n:>3} liveWR={wr*100:4.0f}% (exp {ew*100:.0f}%)  live$/tr=${pt:+.2f} (exp ${ep:+.2f})  drift={dr:+.0f}%  {'OK' if abs(dr)<30 else 'DRIFT>30% — investigate'}")
EOF
```

### 4. FILTER DISCOVERY — slice trades_detailed to lift WR + profit (the main goal)

Find conditions (hour, symbol, distance, side) where the edge is weak or negative;
those become candidate EXCLUSIONS. Run per edge (det vs sq separately).

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && python3 <<'EOF'
import json, pathlib, collections
def load(sid):
    f=pathlib.Path("data/jsonl")/sid/"trades_detailed.jsonl"
    return [json.loads(L) for L in f.read_text().splitlines() if L.strip()] if f.exists() else []
def bucket_dist(d): return "<7" if d<7 else ("7-12" if d<12 else ("12-25" if d<25 else "25+"))
def report(rows, label):
    if not rows: print(f"\n{label}: no trades"); return
    n=len(rows); wr=sum(r['won'] for r in rows)/n; pt=sum(r['pnl'] for r in rows)/n
    print(f"\n=== {label}: n={n} overall WR={wr*100:.0f}% ${pt:+.2f}/tr ===")
    for name, keyfn in [("utc_hour", lambda r:r['utc_hour']),
                         ("symbol", lambda r:r['symbol']),
                         ("dist_bucket", lambda r:bucket_dist(r.get('dist_bps',0))),
                         ("fav_side", lambda r:r['fav_side'])]:
        agg=collections.defaultdict(lambda:[0,0.0])
        for r in rows: agg[keyfn(r)][0]+=1; agg[keyfn(r)][1]+=r['pnl']
        cells=sorted(((k,c,s/c) for k,(c,s) in agg.items() if c>=5), key=lambda x:x[2])
        bad=[f"{k}(n{c},${m:+.1f})" for k,c,m in cells if m<0]
        print(f"  by {name}: worst→ {[(k,c,round(m,1)) for k,c,m in cells[:3]]} | EXCLUDE-cands(mean<0,n>=5): {bad or 'none'}")
report(load("det_lwd_v1"), "determinism (det_lwd_v1)")
report(load("det_sqp_v1"), "stale_quote (det_sqp_v1)")
print("\nNOTE: only propose a filter if the bad cell is (a) n>=~15, (b) negative-mean,")
print("(c) plausibly causal (a specific UTC hour / low-depth symbol). Avoid overfitting on n<10.")
EOF
```

### 5. Daily-cap behaviour (capped vs uncapped)

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && python3 <<'EOF'
import json, pathlib
def stat(sid):
    f=pathlib.Path("data/jsonl")/sid/"trades_detailed.jsonl"
    r=[json.loads(L) for L in f.read_text().splitlines() if L.strip()] if f.exists() else []
    return len(r), sum(x["pnl"] for x in r)
for base in ("det_lwd_v1","det_sqp_v1"):
    nu,pu=stat(base); nc,pc=stat(base+"_capped")
    print(f"{base}: uncapped {nu}tr ${pu:+.2f} | capped {nc}tr ${pc:+.2f} -> cap {'FIRED (fewer trades / floored loss)' if nc<nu else 'never triggered'}")
EOF
```

### 6. Update STATE.md + recommend

Append a dated section to `STATE.md`: clean days, per-strategy live WR/$/tr vs
backtest + drift, the daily-cap summary, the **proposed filters** (with the cell
stats that justify each), and a small-live-test recommendation.

Decision guide:
- **Drift <30% AND det WR ~88-92% over ≥~50 trades** → propose a small live test ($50–100, $10/trade, the `_capped` config). Needs a separate go-live plan (WS-spot wiring already done; real fill-rate is the remaining unknown).
- **Filters found** → propose adding them as a v2 strategy (e.g. `det_lwd_v2` excluding hour X / low-depth symbols). DON'T auto-edit strategies.yaml — surface for the user.
- **Drift >30% or WR collapsed** → do NOT go live; investigate (fill realism, feed staleness, regime shift) first.

### 7. Format the response

```markdown
## mean-rev — forward-test review (<date>, N clean days)

**TL;DR:** <does live track backtest? any filters to add? go-live yes/wait?>

### Live-paper vs backtest
| strategy | live n | live WR | live $/tr | backtest | drift |
|---|---|---|---|---|---|
| det_lwd_v1 | … | …% | $… | 90% / +$1.45 | …% |
| det_sqp_v1 | … | …% | $… | 50% / +$3.0 | …% |

### Proposed filters (to lift WR + profit)
- <e.g. "exclude UTC hour 03 — det WR 71% over n=18, mean −$0.40"> (with the stat)

### Daily-cap
- <fired N times / never; capped vs uncapped PnL>

### Recommendation
- <small live test? add filters as det_lwd_v2? keep collecting?>
```

## What this skill must NOT do

- **Don't auto-modify `strategies.yaml`** — surface filter/config proposals; the user enables them.
- **Don't go live with real money** on this review alone — that's a separate plan (small cap, daily-loss limit, the real-fill unknown).
- **Don't overfit filters** on tiny samples (n<10) — a few unlucky trades in an hour ≠ a real time-of-day effect.
- **Don't run the old polymarket-arb mean-reversion sweep** — it's retired; the edges are evaluated via `research/` (build_joined, forward_validate, gauntlet, trades_detailed).
- Never delete data.

## Edge cases

- <50 det trades total: thin sample, CIs wide — caveat heavily, lean "keep collecting".
- A capped twin with far fewer trades than its uncapped twin: the $50/day breaker is firing often → the edge is having bad days; investigate before live.
- det WR fine but $/tr low: check entry prices (are fills landing near the backtest's ~0.78 avg, or worse?) — a fill-quality / latency signal.
