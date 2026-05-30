---
name: mean-rev-status
description: Use when the user asks about the polymarket-mean-reversion bot status — is it running, did it trade, what's the PnL per strategy, what's the heartbeat age, runtime, daily profit rate. Triggered by phrases like "mean-rev status", "is the new bot running", "did the bot trade", "how is the live bot doing", "what's the status of the strategies", "/mean-rev-status".
---

# polymarket-mean-reversion bot status

Detailed health + trading snapshot with per-strategy notes, recent trades, runtime, and daily profit rate. Read-only when bot is healthy; will auto-diagnose and restart if down.

## When to invoke

- User asks "is the bot running / working / OK?"
- User asks "did the bot trade / make money?"
- User asks "what's the PnL / heartbeat / queue / status?"
- User asks "what's the runtime / how long has it been running?"
- `/mean-rev-status`
- After a long absence ("I'm back, what happened?")

Do NOT invoke proactively. Only when asked.

## Steps

Working directory: `/Users/itayozer/dev/polymarket-mean-reversion`

### 1. Read STATE.md first

```bash
head -40 /Users/itayozer/dev/polymarket-mean-reversion/STATE.md
```

Captures expected state, scheduled review date, what strategies are running.

### 2. Run the status command

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && uv run python -m mean_reversion_live.scripts.status
```

Reports process alive/dead, KILL sentinel, last heartbeat age, queue size, active markets, per-strategy PnL + trade count, portfolio files.

### 3. Get runtime duration

```bash
# Combined pid → process elapsed time. Fallback: pid file mtime.
PID=$(cat /Users/itayozer/dev/polymarket-mean-reversion/.combined.pid 2>/dev/null)
if [ -n "$PID" ] && ps -p "$PID" > /dev/null 2>&1; then
  ps -p "$PID" -o etime= | xargs   # e.g. "00:24:13" or "1-02:15:30"
  # Get start epoch for daily-rate math
  ps -p "$PID" -o lstart=
else
  echo "process not running"
fi
```

Convert `etime` to seconds for daily-profit math. Format `[DD-]HH:MM:SS` or `MM:SS`.
- `< 1h`: show as "~N min"
- `< 24h`: show as "~Xh Ym"
- `>= 24h`: show as "Xd Yh"

### 4. Get recent trades grouped by strategy (with hold time)

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && python3 <<'EOF'
import json, pathlib
# enabled = strategies the running engine actually loaded (heartbeat keys). No yaml
# dep (this runs under system python3), and reflects reality, not just config.
try:
    enabled = set(json.load(open("data/state/last_tick.json")).get("strategy_pnl", {}))
except Exception:
    enabled = set()
root = pathlib.Path("data/jsonl")
for sid_dir in sorted(root.iterdir()):
    if enabled and sid_dir.name not in enabled: continue   # skip disabled strategies
    f = sid_dir / "trades.jsonl"
    if not f.exists(): continue
    trades = [json.loads(L) for L in f.read_text().splitlines() if L.strip()]
    if not trades: continue
    last3 = trades[-3:]
    print(f"\n{sid_dir.name}:")
    for t in last3:
        sym = t["slug"].split("-")[0].upper()
        win = t["slug"].split("-")[2]   # slug=<sym>-updown-<tf>-<ts>; NOT `"5m" in slug` ("15m" contains "5m"!)
        held = t.get("seconds_held", 0)
        pnl = t["pnl"]
        # det/stale-quote trades hold to resolution: exit_price 1.0=win, 0.0=loss, reason "resolution"
        print(f"  {sym} {win} {t['side']} @{t['entry_price']:.3f} → {t['exit_price']:.3f} = {'+' if pnl>=0 else ''}${pnl:.2f} ({held}s {t['exit_reason']})")
EOF
```

### 5. Compute total / daily / monthly PnL — overall AND per-strategy ($ and %)

`etimes` is Linux-only — on macOS BSD `ps`, parse `etime` (`[DD-]HH:MM:SS` / `MM:SS`).
Capital base for `%`: prefer portfolio `starting_cash`; if absent, fall back to $100/strategy.

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && python3 <<'EOF'
import json, pathlib, subprocess

def parse_etime(s: str) -> int:
    # BSD: "MM:SS" | "HH:MM:SS" | "DD-HH:MM:SS"
    s = s.strip()
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [int(x) for x in s.split(":")]
    if len(parts) == 2:
        h, (m, sec) = 0, parts
    elif len(parts) == 3:
        h, m, sec = parts
    else:
        return 0
    return days * 86400 + h * 3600 + m * 60 + sec

PID = pathlib.Path(".combined.pid").read_text().strip() if pathlib.Path(".combined.pid").exists() else ""
elapsed_s = None
if PID:
    try:
        et = subprocess.check_output(["ps","-p",PID,"-o","etime="]).decode()
        elapsed_s = parse_etime(et)
    except Exception:
        pass

DEFAULT_BASE = 1000.0  # paper-trader per-strategy seed (starting_capital_usd)
# Only report ENABLED strategies. Disabled strategies' stale portfolio JSONs stay
# on disk (26 of them since the 2026-05-29 cutover) but MUST NOT clutter the live
# report. Enabled set = the running engine's heartbeat keys (no yaml dep under python3).
_enabled = set()
try:
    _enabled = set(json.load(open("data/state/last_tick.json")).get("strategy_pnl", {}))
except Exception:
    pass
total_pnl = 0.0
total_base = 0.0
rows = []
for f in sorted(pathlib.Path("data/portfolios").glob("*.json")):
    sid = f.stem
    if _enabled and sid not in _enabled:
        continue  # skip disabled/retired strategies
    p = json.loads(f.read_text())
    pnl = float(p.get("total_pnl", 0.0))
    base = float(p.get("starting_cash") or p.get("starting_capital") or DEFAULT_BASE)
    total_pnl += pnl
    total_base += base
    rows.append((sid, pnl, base, int(p.get("n_trades", 0) or len(p.get("trades", [])))))

def fmt_rate(pnl, base, elapsed, horizon_s):
    if not elapsed or elapsed <= 0:
        return None, None
    proj = pnl * horizon_s / elapsed
    pct = (proj / base * 100.0) if base > 0 else 0.0
    return proj, pct

print(f"TOTAL  pnl=${total_pnl:+.2f}  base=${total_base:.2f}  total_pct={total_pnl/total_base*100:+.2f}%")
if elapsed_s and elapsed_s > 0:
    d_usd, d_pct = fmt_rate(total_pnl, total_base, elapsed_s, 86400)
    m_usd, m_pct = fmt_rate(total_pnl, total_base, elapsed_s, 86400 * 30)
    print(f"TOTAL  elapsed_s={elapsed_s}  daily=${d_usd:+.2f} ({d_pct:+.2f}%)  monthly_forecast=${m_usd:+.2f} ({m_pct:+.2f}%)")

print()
print(f"{'strategy':32}  {'trades':>6}  {'total$':>10}  {'total%':>8}  {'daily$':>10}  {'daily%':>8}  {'month$':>10}  {'month%':>8}")
for sid, pnl, base, n in rows:
    tot_pct = (pnl / base * 100.0) if base > 0 else 0.0
    d_usd, d_pct = fmt_rate(pnl, base, elapsed_s, 86400) if elapsed_s else (None, None)
    m_usd, m_pct = fmt_rate(pnl, base, elapsed_s, 86400 * 30) if elapsed_s else (None, None)
    if d_usd is None:
        print(f"{sid:32}  {n:>6}  {pnl:>+10.2f}  {tot_pct:>+7.2f}%  {'-':>10}  {'-':>8}  {'-':>10}  {'-':>8}")
    else:
        print(f"{sid:32}  {n:>6}  {pnl:>+10.2f}  {tot_pct:>+7.2f}%  {d_usd:>+10.2f}  {d_pct:>+7.2f}%  {m_usd:>+10.2f}  {m_pct:>+7.2f}%")
EOF
```

Notes:
- Monthly forecast = `daily_rate × 30`. It's a linear extrapolation, not seasonal — caveat below.
- `%` denominator is the portfolio's `starting_cash` (typical paper-trader seed = $100/strategy). The TOTAL row uses the sum of bases as the denominator so totals reconcile.
- If `elapsed_s` < 1800 (≈30 min) suppress the daily/monthly columns in the response — extrapolation from <30 min is noise. The script still prints them; you decide whether to surface.
- If `elapsed_s` < 86400 (24h) include a one-line caveat: "monthly forecast assumes today's rate persists for 30d — confidence builds after 24h+ runtime".

### 5b. Daily-loss-cap status + condition slice (the 4 edge strategies)

Reads `data/jsonl/<sid>/trades_detailed.jsonl` (rich per-trade context). Surfaces:
today's PnL per strategy + whether the $50/day cap fired, all-time WR, and a
"worst hours by WR" teaser feeding the weekly filter analysis (skip-bad-times).

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && python3 <<'EOF'
import json, pathlib, datetime as dt, collections
today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
allr = []
for sid in ("det_lwd_v1","det_lwd_v1_capped","det_sqp_v1","det_sqp_v1_capped"):
    f = pathlib.Path("data/jsonl")/sid/"trades_detailed.jsonl"
    rows = [json.loads(L) for L in f.read_text().splitlines() if L.strip()] if f.exists() else []
    allr += rows
    today_rows = [r for r in rows if dt.datetime.utcfromtimestamp(r["entry_ts_ms"]/1000).strftime("%Y-%m-%d")==today]
    pnl_today = sum(r["pnl"] for r in today_rows)
    wr = sum(r["won"] for r in rows)/len(rows)*100 if rows else 0.0
    cap = "$50/day" if "capped" in sid else "uncapped"
    flag = "  ⚠ DAILY CAP HIT" if ("capped" in sid and pnl_today <= -50) else ""
    print(f"{sid:22} today: {len(today_rows):>2} tr ${pnl_today:+7.2f} ({cap}){flag} | all-time: {len(rows):>3} tr WR {wr:.0f}%")
if allr:
    byh = collections.defaultdict(lambda:[0,0])
    for r in allr: byh[r["utc_hour"]][0]+=r["won"]; byh[r["utc_hour"]][1]+=1
    worst = sorted((w/n, h, n) for h,(w,n) in byh.items() if n>=5)[:3]
    print("filter teaser — worst UTC hours by WR (n>=5):",
          [(f"h{h:02d}", f"{wr*100:.0f}%", f"n={n}") for wr,h,n in worst] or "n/a")
EOF
```

Notes:
- The `_capped` variant should match its uncapped twin until a day's loss reaches −$50, after which the capped one stops entering (fewer trades, floored loss). Calling out a CAP HIT is a useful signal.
- `det_*` = the post-2026-05-29 edge strategies. If `trades_detailed.jsonl` is absent, no trades have settled yet — normal early in a window/regime (the edge is selective).

### 6. If process is dead OR heartbeat > 30s OR queue blown up: AUTO-DIAGNOSE & RESTART

**This skill MUST take action when the bot is down.** Don't just report — fix it.

#### a. Surface the symptom to the user up front

```markdown
🔴 **Bot is down** — process [missing | dead pid | heartbeat 12m stale]. Diagnosing root cause now.
```

#### b. Pull recent log evidence

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && tail -120 logs/combined.log
```

Look for (in order of likelihood):
- `ws_disconnect` / `ws_no_subscriptions_in_120s` — WS dropped, reconnect loop failed
- `gamma_error` / `connection refused` — Polymarket API unreachable
- `Traceback` / `*_error` — Python exception, likely fatal
- `aggregator_status` with `rows_written=0` for many cycles — feed alive but no data
- `KILL` sentinel detected — operator stopped it

Report the root cause in 1-2 sentences with the log line that proves it.

#### c. Run preflight (from mean-rev-restart skill)

Reuse the checks from the `mean-rev-restart` skill:
- `data/KILL` absent (rm if present, confirming with user first)
- `.combined.pid` either gone or pointing to dead PID (rm if stale)
- `/Users/itayozer/dev/polymarket-arb/scripts/mean_reversion/signals.py` exists
- `readlink /Users/itayozer/dev/polymarket-arb/data_v2` is intact
- `curl -s -o /dev/null -w "%{http_code}" https://gamma-api.polymarket.com/events?limit=1` returns 200
- `strategies.yaml` parses

If any preflight fails: STOP, surface the failure, ask the user.

#### d. Restart

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && ./scripts/start_all.sh
```

#### e. Verify after 30s

```bash
sleep 30 && cd /Users/itayozer/dev/polymarket-mean-reversion && uv run python -m mean_reversion_live.scripts.status
```

Confirm: process alive, heartbeat <10s, active_markets > 0.

#### f. Report

```markdown
🟢 **Bot restored**
- Root cause: [one line]
- Action: [what was fixed]
- Now: PID X, heartbeat Ys ago, N markets active
```

If restart fails twice: STOP, dump the last 200 lines of log, ask the user.

### 7. Format the healthy-path response

Target: under 450 words (extra ~100 to absorb the wider per-strategy table). Match this structure (the user prefers detail over brevity):

```markdown
## mean-rev bot status — <UTC time>

🟢 **ALL GREEN** — running for <runtime>, healthy heartbeat

| | |
|---|---|
| Process | ✓ PID X (heartbeat Ys ago) |
| KILL sentinel | absent |
| Active markets | N |
| Queue | 0 (engine keeping up) |
| Strategies | 4 enabled |
| Runtime | <e.g. ~24 min / 3h 12m / 2d 5h> |
| Total PnL | $±X.XX (±X.XX% of $base capital) |
| Avg daily | $±X.XX/day (±X.XX%/day) |
| Monthly forecast | $±X.XX (±X.XX%) — 30d linear extrapolation; <confidence note> |

**Per-strategy PnL (the 4 active edge strategies — post-2026-05-29 cutover):**

| strategy_id | trades | total $ | WR | daily $ | today's PnL (cap) | Notes |
|---|---|---|---|---|---|---|
| `det_lwd_v1` | N | $±X | X% | $±X | $±X | Determinism (Phase 1) — last 60s, spot ≥5bps from strike, buy favourite ≤0.90, hold to resolution. Backtest OOS +$1.68/tr, 91% WR. **uncapped** (true-edge measure). |
| `det_lwd_v1_capped` | N | $±X | X% | $±X | $±X (≤−50? CAP) | Same rule + **$50/day max-loss breaker** (live candidate). |
| `det_sqp_v1` | N | $±X | X% | $±X | $±X | Stale-quote (Phase 2) — mid-window, model-vs-market in [8,30]¢ + spot jump ≥8bps, hold. Backtest +$3.5/tr, **WR ~50% / high-variance**. uncapped. |
| `det_sqp_v1_capped` | N | $±X | X% | $±X | $±X (≤−50? CAP) | Same + **$50/day breaker**. |

Column rules:
- These are **fixed-$10/trade, hold-to-resolution** strategies. The `%`-of-capital columns are meaningless (only ~$10×concurrent is deployed) — report **$/trade, WR, $/day, today's PnL** instead.
- `daily $` = `total × 86400 / elapsed_s` (caveat <24h runtime). Skip if runtime <30 min.
- For idle strategies (n=0): show `$0.00`, and note WHY (selective edge — only ~14% of 15m windows qualify for det; sq needs a spot jump). 0 trades for a while is normal, NOT a fault.
- Group as **det (robust) vs sq (higher-variance)** and **capped vs uncapped** — call out if a capped twin diverged from its uncapped twin (daily cap fired).

**Recent trades (last 3 per strategy):** (exit_price 1.0 = won, 0.0 = lost; reason `resolution`)

det_lwd_v1:
- BTC 15m UP @0.83 → 1.0 = **+$2.05** (48s, resolution)
- ETH 15m DOWN @0.78 → 0.0 = **-$10.13** (55s, resolution)
- ...

**Analysis:**
- <2-3 bullets: Is live-paper tracking the backtest (det ~90% WR +$1.45/tr; sq ~50% WR)? Did either capped twin hit its $50/day breaker? Any time-of-day / regime pattern emerging from the 5b slice (toward the weekly filter to lift WR)? Note det fires in the last 60s of any 15m window (all hours, NOT ASIA-only).>

Next: <e.g. "let it run, next review 2026-05-22" / "monitor relaxed_v1 if it hits 20 trades">
```

### 8. Append a diary entry (ALWAYS — on every successful run)

After reporting to the user, append a short note to `data/diary.md` summarizing this check.
This builds a chronological hourly log so future sessions can see how the bot evolved.

```bash
TZ=Asia/Jerusalem date "+%Y-%m-%d %H:%M %Z"   # for the header
```

Write the entry as a markdown block, appended with `>>`:

```bash
cat >> /Users/itayozer/dev/polymarket-mean-reversion/data/diary.md <<'EOF'

## <YYYY-MM-DD HH:MM IDT/IST> — runtime <Xh Ym>

- **Total PnL:** $±X.XX (Δ last hour: $±X.XX)
- **Observations:** <1-3 short bullets — best/worst strategy this hour, regime signal, any FR cluster, WR shifts>
- **Notable trades:** <0-2 single-line trade callouts ONLY if remarkable (big win, correlated cluster, structural failure)>
- **Next:** <e.g. "ASIA window opens 01:00 IDT", "watch max_pnl_v2 if WR > 70% holds">

EOF
```

Rules:
- **Keep each entry under ~10 lines.** This is a diary, not a report.
- **Always include the IDT/IST suffix** in the header (Israel time per user preference).
- **Never overwrite** — only append. If `data/diary.md` doesn't exist, the heredoc creates it.
- **Skip the diary append if the bot was DOWN** (you went through the AUTO-DIAGNOSE path) — that's a separate incident-style entry, write `## <ts> — bot restarted` with the root cause instead.

**What to write in the Observations bullets (THE HARD PART):**

The diary's value is **analytical patterns across hours**, not single-hour numbers. The status report already showed the user this hour's PnL table — don't transcribe it. Re-reading the diary later, the user wants to see what *trends and structural insights* you identified that they couldn't reconstruct from the raw status alone.

Include 1–2 of these when they apply (read the most recent diary entries first to spot patterns):

1. **Multi-hour bleed/gain trends** — string together at least 3 prior hourly Δs. *"Bleed pattern: -$254 → -$146 → -$51 → -$197 — pure variance, not a stable edge."*
2. **Recurring structural failures** — match against earlier diary entries. *"3rd correlated FR cluster of the session — same trade hit max_pnl trio simultaneously."*
3. **Cumulative WR drift** — strategies whose WR moved meaningfully across the session. *"max_pnl_v2 WR climbed 63% → 71% over 8h — small-sample reversion."*
4. **Dormancy patterns** — strategies idle N consecutive hours. *"Validated #1 dormant 4th hour — deep-dip filter rarely fires in current regime."*
5. **EV math callouts** when WR crosses a threshold. *"At 71% WR with +$1.5/−$10.5 payoffs, EV/trade ≈ −$1.5 — still negative."*
6. **Cross-strategy correlation events** — single market move hitting multiple strategies. *"XRP DOWN @0.23 → $0 hit v1/v2/v3 = −$31.62 in one event."*

AVOID in the diary:
- Restating the current PnL table the status report already showed
- Hour-by-hour transcription of which strategy made which dollar amount
- Generic "bot healthy, all running" — that's implicit if an entry exists
- Trade-by-trade callouts unless the trade is a *pattern instance*

Before writing, glance at `tail -50 data/diary.md` to see the most recent entries — patterns and recurrence are the point.

#### How to fill the "Notes" column

The ONLY enabled strategies since the 2026-05-29 cutover are the 4 below (the
edge hunt's survivors). All prior mean-reversion strategies (`cfg_*`, `sv2_*`,
`v2_gold_*`, `relaxed_v1`) are **disabled** — the research proved mean-reversion
is the wrong direction here; the live edge is momentum/determinism (book lags
spot). If a disabled id ever appears in the report, the enabled-filter broke —
fix it; don't report stale strategies.

| strategy_id | Notes template |
|---|---|
| `det_lwd_v1` | "Determinism / late-window pickoff (Phase 1, **robust primary**). Last 60s of a 15m window, spot ≥5bps from strike (favourite agrees), buy favourite at ask ≤0.90, **hold to resolution**. Backtest OOS +$1.68/tr, 91% WR, gauntlet-clean. **uncapped** (true-edge measure). Fires in last 60s of ANY 15m window — all hours, NOT ASIA-only." |
| `det_lwd_v1_capped` | "Same rule + **$50/day max-loss breaker** (the live-deployment candidate). Should mirror det_lwd_v1 until a day hits −$50, then stops." |
| `det_sqp_v1` | "Stale-quote pickoff (Phase 2, **secondary, higher-variance**). Mid-window, |empirical P(Up\|z) − mid| in [8,30]¢ AND a recent spot jump ≥8bps, buy the model side, hold. Backtest +$3.5/tr but **WR ~50%** (bigger payoffs, outlier-sensitive). uncapped." |
| `det_sqp_v1_capped` | "Same + **$50/day breaker**." |

**No ASIA-window check needed** — unlike the old sv2 strategies, the determinism
edge trades in the last 60s of *every* 15m window across all 24h, and stale-quote
fires mid-window on jumps. 0 trades for a stretch is normal: det qualifies in only
~14% of windows; sq needs a spot jump. Idle ≠ broken.

**Group the report as:** det (robust, ~90% WR, low-variance) vs sq (higher-
variance, ~50% WR); and within each, capped vs uncapped. The headline question
all week: **does live-paper track the backtest** (det ~+$1.45/tr 90% WR; sq ~+$3/tr
50% WR)? Call out any capped twin that diverged from its uncapped twin (daily cap
fired) and any time-of-day / regime pattern from the §5b slice.

#### Analysis bullets — what to call out

- **Live-vs-backtest drift**: det WR should sit ~88–91% and ~+$1.0–1.7/tr; sq ~50% WR with positive mean. Flag material drift (the <30% bar gates real money).
- **Daily cap**: did `det_lwd_v1_capped` / `det_sqp_v1_capped` hit −$50 today (stopped early)? If a capped twin's trade count < its uncapped twin's, the breaker fired.
- **Selectivity is expected**: det fires ~14% of windows; long idle stretches in a quiet (spot-near-strike) regime are normal, not a fault.
- **Filter teaser** (§5b): if a UTC hour / regime shows consistently low WR over the week, that's a candidate exclusion for the weekly review (lifts WR + profit).
- Daily rate: unreliable <24h runtime — caveat it. Don't quote %-of-capital (fixed-$10 strategies; the % is meaningless).
- Fat tail: det losers are −$10 (full stake), ~9–11% of trades; sq is even more variable. A red day within variance ≠ a broken edge — check WR, not just $.

## Edge cases

- If `.combined.pid` doesn't exist AND no recent log activity: bot was never started this session. Offer to start it via the restart flow above.
- If `data/KILL` exists: surface as the very first line ("⚠ KILL switch is active — bot is halting"). Ask whether to clear and restart.
- `n_trades=0` for the det/sq edge strategies is **normal** (selective: det ~14% of windows, sq needs a jump) — do NOT warn on it. Only warn if 0 trades for >24h AND the §5b "fired" signals are also absent (would suggest the strategy isn't evaluating) OR the feed is dead (heartbeat stale).
- If queue size > 100: engine falling behind aggregator. Surface as warning.
- If runtime < 30 min: don't show daily-rate extrapolation, it's noise.

## What this skill must NOT do

- Never write to portfolio JSONs, jsonl files, or state files.
- Never place real orders.
- Never restart without first identifying the root cause from logs.
- Never silently kill a running process. Restart only when verified-down.
