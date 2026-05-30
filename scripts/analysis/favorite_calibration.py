#!/usr/bin/env python3
"""
Late-window FAVORITE calibration + buy-and-hold-to-resolution EV.  [CORRECTED LABELS]

Run with:  uv run python3 scripts/analysis/favorite_calibration.py

Genuine gap left by the prior edge-research effort:
  - calibration only binned the CHEAP side (0.00-0.53)
  - calibration cross-section sampled only out to 720s (180s before a 15m close)
  - "buy favorite, HOLD to resolution" (exit fee ~= 0) was never isolated

Labels: data/research/corrected_labels.parquet -> authoritative_outcome_up
        (Polymarket API ground truth, 100% coverage, 15m windows only).
        data/outcomes.csv is corrupt (~31% wrong) and is NOT used.

Token prices: live tick order books (May 15-22), not affected by the strike bug.
Train = May 15-19, Test = May 20-22.  An edge must hold post-fee in BOTH.
"""
import gzip, csv, glob, random, collections, zlib
import pandas as pd

LIVE = sorted(glob.glob("data/live/*.csv.gz"))
FEE = 0.07
STAKE = 10.0
TRAIN_DATES = {"2026-05-15", "2026-05-16", "2026-05-17", "2026-05-18", "2026-05-19"}
TEST_DATES = {"2026-05-20", "2026-05-21", "2026-05-22"}

TL_BUCKETS = [(0, 30, "0-30s"), (30, 60, "30-60s"), (60, 120, "60-120s"),
              (120, 300, "120-300s"), (300, 600, "300-600s")]
TL_CENTER = {"0-30s": 15, "30-60s": 45, "60-120s": 90, "120-300s": 210, "300-600s": 450}
ASK_EDGES = [0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.93, 0.96, 0.99, 1.00]


def tl_bucket(tl):
    for lo, hi, name in TL_BUCKETS:
        if lo < tl <= hi:
            return name
    return None


def ask_bucket(a):
    for i in range(len(ASK_EDGES) - 1):
        if ASK_EDGES[i] <= a < ASK_EDGES[i + 1]:
            return f"{ASK_EDGES[i]:.2f}-{ASK_EDGES[i+1]:.2f}"
    return None


def realized_pnl(ask, won):
    """PnL of buying the favorite at `ask` with $10, holding to resolution."""
    shares = STAKE / ask
    entry_fee = FEE * shares * ask * (1.0 - ask)        # = 0.7*(1-ask)
    payout = shares * (1.0 if won else 0.0)             # exit fee ~= 0 at p in {0,1}
    return payout - STAKE - entry_fee


# ---- 1. corrected labels (15m only) ----
cl = pd.read_parquet("data/research/corrected_labels.parquet")
outcome = {r["slug"]: ("Up" if r["authoritative_outcome_up"] == 1.0 else "Down")
           for _, r in cl.iterrows()}
print(f"corrected labels: {len(outcome)} 15m windows  (API ground truth, P(Up)={cl['authoritative_outcome_up'].mean():.4f})")

# ---- 2. stream ticks (15m only); one favorite obs per (window, tl_bucket) ----
obs = {}
windows_seen = set()
truncated = 0
for path in LIVE:
    date = path.split("/")[-1].split("_")[1][:10]
    try:
        with gzip.open(path, "rt") as f:
            reader = csv.DictReader(f)
            while True:
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except (zlib.error, EOFError, OSError, csv.Error):
                    truncated += 1
                    break
                slug = row.get("market_slug")
                if not slug or "-15m-" not in slug:
                    continue
                oc = outcome.get(slug)
                if oc is None:
                    continue
                try:
                    siw = float(row["seconds_into_window"])
                    ym = float(row["yes_mid"]); nm = float(row["no_mid"])
                    ya = float(row["yes_best_ask"]); na = float(row["no_best_ask"])
                    yad = float(row["yes_ask_depth"]); nad = float(row["no_ask_depth"])
                except (ValueError, KeyError, TypeError):
                    continue
                tl = 900 - siw
                if tl <= 0:
                    continue
                tlb = tl_bucket(tl)
                if tlb is None:
                    continue
                if ym > nm:
                    fav, fav_ask, fav_depth = "YES", ya, yad
                elif nm > ym:
                    fav, fav_ask, fav_depth = "NO", na, nad
                else:
                    continue
                if not (0.0 < fav_ask < 1.0):
                    continue
                won = (fav == "YES" and oc == "Up") or (fav == "NO" and oc == "Down")
                key = (slug, tlb)
                dist = abs(tl - TL_CENTER[tlb])
                if key not in obs or dist < obs[key][0]:
                    obs[key] = (dist, (date, tlb, fav_ask, fav_depth, won))
                windows_seen.add(slug)
    except (OSError, EOFError, zlib.error):
        truncated += 1
print(f"15m windows with >=1 usable tick: {len(windows_seen)}  ({truncated} partial files)")

# ---- 2b. sanity check: overall favorite calibration (all corrected obs) ----
print("\nSANITY  -- overall favorite calibration (one obs/window/bucket pooled, all dates)")
print(f"{'fav-ask bin':>12} {'n':>7} {'mean_ask':>9} {'realized win%':>14}")
allcells = collections.defaultdict(list)
for (slug, tlb), (_, rec) in obs.items():
    date, tlb2, ask, depth, won = rec
    ab = ask_bucket(ask)
    if ab:
        allcells[ab].append((ask, won))
for i in range(len(ASK_EDGES) - 1):
    ab = f"{ASK_EDGES[i]:.2f}-{ASK_EDGES[i+1]:.2f}"
    rows = allcells.get(ab, [])
    if not rows:
        continue
    n = len(rows)
    print(f"{ab:>12} {n:>7} {sum(r[0] for r in rows)/n:>9.4f} {sum(r[1] for r in rows)/n*100:>13.2f}%")

# ---- 3. train/test EV table ----
def build(dates):
    cells = collections.defaultdict(list)
    for (slug, tlb), (_, rec) in obs.items():
        date, tlb2, ask, depth, won = rec
        if date not in dates:
            continue
        ab = ask_bucket(ask)
        if ab:
            cells[(tlb, ab)].append((ask, won, depth, slug))
    return cells


def summarize(cells, label):
    print(f"\n{'='*104}\n{label}\n{'='*104}")
    print(f"{'time-left':>10} {'fav-ask':>11} {'n':>5} {'mean_ask':>9} "
          f"{'win%':>7} {'breakeven%':>11} {'edge_pp':>8} {'PnL/$10':>9} {'CI90':>18} {'dep>=10':>8}")
    positive = []
    for _, _, tlb in TL_BUCKETS:
        for i in range(len(ASK_EDGES) - 1):
            ab = f"{ASK_EDGES[i]:.2f}-{ASK_EDGES[i+1]:.2f}"
            rows = cells.get((tlb, ab))
            if not rows or len(rows) < 20:
                continue
            n = len(rows)
            mean_ask = sum(r[0] for r in rows) / n
            winrate = sum(r[1] for r in rows) / n
            pnls = [realized_pnl(r[0], r[1]) for r in rows]
            ev = sum(pnls) / n
            breakeven = mean_ask + FEE * mean_ask * (1 - mean_ask)
            edge_pp = (winrate - breakeven) * 100
            depfrac = sum(1 for r in rows if r[2] >= 10.0) / n
            byslug = collections.defaultdict(list)
            for (ask, won, depth, slug), p in zip(rows, pnls):
                byslug[slug].append(p)
            slugs = list(byslug.values())
            rng = random.Random(42)
            boot = []
            for _ in range(400):
                s = c = 0
                for _ in range(len(slugs)):
                    grp = slugs[rng.randrange(len(slugs))]
                    s += sum(grp); c += len(grp)
                boot.append(s / c)
            boot.sort()
            lo, hi = boot[20], boot[379]
            flag = "  <==POS" if lo > 0 else ("  ~" if ev > 0 else "")
            if lo > 0:
                positive.append((tlb, ab))
            print(f"{tlb:>10} {ab:>11} {n:>5} {mean_ask:>9.4f} "
                  f"{winrate*100:>6.2f}% {breakeven*100:>10.2f}% {edge_pp:>+8.2f} "
                  f"{ev:>+9.3f} [{lo:>+6.3f},{hi:>+6.3f}]{flag} {depfrac*100:>6.1f}%")
    return positive


train = build(TRAIN_DATES)
test = build(TEST_DATES)
pos_train = summarize(train, "TRAIN  (May 15-19)  -- buy FAVORITE at ask, $10, hold to resolution")
pos_test = summarize(test, "TEST   (May 20-22)  -- same bins, out-of-sample")

print(f"\n{'='*104}\nVERDICT\n{'='*104}")
print(f"bins post-fee positive (CI lo>0) in TRAIN: {pos_train or 'NONE'}")
print(f"bins post-fee positive (CI lo>0) in TEST : {pos_test or 'NONE'}")
both = sorted(set(pos_train) & set(pos_test))
print(f"bins positive in BOTH (a real edge would live here): {both or 'NONE'}")
