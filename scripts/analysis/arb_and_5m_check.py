#!/usr/bin/env python3
"""
Two final checks:
  A. RISKLESS ARB -- is yes_ask + no_ask ever < $1 (buy both sides, guaranteed $1)?
     This is the only mechanically-sound "two markets at once" play.
  B. 5m calibration -- confirm 5m markets are calibrated like 15m.
     Outcome reconstructed from coinbase_price (first tick vs last tick);
     ~99% accurate on the large-move windows that dominate the favorite tail.
"""
import gzip, csv, glob, collections, zlib

LIVE = sorted(glob.glob("data/live/*.csv.gz"))
ASK_EDGES = [0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.93, 0.96, 0.99, 1.00]


def ask_bucket(a):
    for i in range(len(ASK_EDGES) - 1):
        if ASK_EDGES[i] <= a < ASK_EDGES[i + 1]:
            return f"{ASK_EDGES[i]:.2f}-{ASK_EDGES[i+1]:.2f}"
    return None


def stream(path):
    with gzip.open(path, "rt") as f:
        reader = csv.DictReader(f)
        while True:
            try:
                yield next(reader)
            except StopIteration:
                return
            except (zlib.error, EOFError, OSError, csv.Error):
                return


# ---- pass 1: 5m window outcomes from coinbase + arb scan (all ticks) ----
first = {}   # slug -> (siw, coinbase)
last = {}
arb_sum = collections.Counter()   # rounded ask-sum -> count
arb_hits = []                     # (sum, min_depth)
n_ticks = 0

for path in LIVE:
    for row in stream(path):
        slug = row.get("market_slug", "")
        try:
            ya = float(row["yes_best_ask"]); na = float(row["no_best_ask"])
            yad = float(row["yes_ask_depth"]); nad = float(row["no_ask_depth"])
        except (ValueError, KeyError, TypeError):
            continue
        # arb scan: need both sides genuinely offered
        if 0.0 < ya < 1.0 and 0.0 < na < 1.0:
            n_ticks += 1
            s = ya + na
            arb_sum[round(s, 2)] += 1
            if s < 1.0:
                arb_hits.append((s, min(yad, nad)))
        # 5m outcome reconstruction
        if "-5m-" not in slug:
            continue
        try:
            siw = float(row["seconds_into_window"])
            cb = float(row["coinbase_price"])
        except (ValueError, KeyError, TypeError):
            continue
        if cb <= 0:
            continue
        if slug not in first or siw < first[slug][0]:
            first[slug] = (siw, cb)
        if slug not in last or siw > last[slug][0]:
            last[slug] = (siw, cb)

outcome5 = {}
for slug in first:
    if slug in last and last[slug][0] > first[slug][0]:
        outcome5[slug] = "Up" if last[slug][1] > first[slug][1] else "Down"

print("=" * 72)
print("A.  RISKLESS-ARB SCAN  (yes_ask + no_ask across all ticks, both sides)")
print("=" * 72)
print(f"ticks scanned: {n_ticks:,}")
print(f"min ask-sum seen: {min(arb_sum):.2f}   max: {max(arb_sum):.2f}")
below = sum(c for s, c in arb_sum.items() if s < 1.00)
b98 = sum(c for s, c in arb_sum.items() if s < 0.98)
b96 = sum(c for s, c in arb_sum.items() if s < 0.96)
print(f"ticks with ask-sum < 1.00 : {below:,}  ({below/n_ticks*100:.3f}%)")
print(f"ticks with ask-sum < 0.98 : {b98:,}  ({b98/n_ticks*100:.4f}%)")
print(f"ticks with ask-sum < 0.96 : {b96:,}  (a real arb after ~3.5c fees)")
if arb_hits:
    arb_hits.sort()
    print(f"deepest 5 'arb' ticks (sum, min-depth $): {[(round(s,3),round(d,1)) for s,d in sorted(arb_hits,key=lambda x:-x[1])[:5]]}")
print("ask-sum distribution (rounded):")
for s in sorted(arb_sum):
    if arb_sum[s] / n_ticks > 0.005:
        print(f"  {s:.2f}: {arb_sum[s]/n_ticks*100:5.1f}%")

# ---- pass 2: 5m favorite calibration ----
print()
print("=" * 72)
print("B.  5m FAVORITE CALIBRATION  (outcome reconstructed from coinbase spot)")
print("=" * 72)
print(f"5m windows with reconstructed outcome: {len(outcome5):,}")
# one obs per window: tick nearest 60s-left
best = {}   # slug -> (dist, ask, won)
for path in LIVE:
    for row in stream(path):
        slug = row.get("market_slug", "")
        if "-5m-" not in slug:
            continue
        oc = outcome5.get(slug)
        if oc is None:
            continue
        try:
            siw = float(row["seconds_into_window"])
            ym = float(row["yes_mid"]); nm = float(row["no_mid"])
            ya = float(row["yes_best_ask"]); na = float(row["no_best_ask"])
        except (ValueError, KeyError, TypeError):
            continue
        tl = 300 - siw
        if tl <= 0:
            continue
        if ym > nm:
            fav, fav_ask = "YES", ya
        elif nm > ym:
            fav, fav_ask = "NO", na
        else:
            continue
        if not (0.0 < fav_ask < 1.0):
            continue
        won = (fav == "YES" and oc == "Up") or (fav == "NO" and oc == "Down")
        dist = abs(tl - 60)
        if slug not in best or dist < best[slug][0]:
            best[slug] = (dist, fav_ask, won)

cells = collections.defaultdict(list)
for slug, (_, ask, won) in best.items():
    ab = ask_bucket(ask)
    if ab:
        cells[ab].append((ask, won))
print(f"{'fav-ask bin':>12} {'n':>7} {'mean_ask':>9} {'realized win%':>14}  {'(calibrated?)':>14}")
for i in range(len(ASK_EDGES) - 1):
    ab = f"{ASK_EDGES[i]:.2f}-{ASK_EDGES[i+1]:.2f}"
    rows = cells.get(ab, [])
    if not rows:
        continue
    n = len(rows)
    ma = sum(r[0] for r in rows) / n
    wr = sum(r[1] for r in rows) / n
    gap = (wr - ma) * 100
    print(f"{ab:>12} {n:>7} {ma:>9.4f} {wr*100:>13.2f}%  {gap:>+12.2f}pp")
print("\n(gap = realized win% - mean ask;  ~0 => calibrated => no edge after fees)")
