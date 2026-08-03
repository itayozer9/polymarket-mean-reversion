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

### 2b. Daemon health + LIVE trading probe (covers LIVE + paper together)

Three INDEPENDENT processes run the system (all nohup'd — they survive Claude sessions,
so the bots keep trading/collecting when no Claude is attached; the user checks them via
this skill):
- **data bot + paper engine** (`run_combined`) — self-heals via `respawn_loop.sh`
- **live executor** — the $5 real-money probe (`live_executor.py --live`)
- **macro collector** — IV/funding/OI research feed (`macro_collector`)

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion
echo "=== daemon health (independent of Claude) ==="
for pat in "run_combined" "live_executor.py --live" "macro_collector"; do
  pids=$(pgrep -f "$pat" | tr '\n' ' ')
  echo "  ${pat}: ${pids:-DOWN}"
done
echo "=== LIVE real-money — PER-STRATEGY table (each started \$100; bal = \$100 + total P&L; wins recycle) ==="
uv run python - <<'PYEOF'
import json, pathlib, datetime as dt
try:
    from zoneinfo import ZoneInfo
    IL = ZoneInfo("Asia/Jerusalem")
except Exception:
    IL = dt.timezone(dt.timedelta(hours=3))  # IDT fallback (summer)
st = pathlib.Path("data/live/executor_state.json")
fl = pathlib.Path("data/live/fills.jsonl")
se = pathlib.Path("data/live/settlements.jsonl")
s = json.loads(st.read_text()) if st.exists() else {}
# Normalize to per-strategy books. v2 = {"version":2,"strategies":{...}}; pre-migration flat
# state (no "strategies") is all det_lwd_live's.
if isinstance(s, dict) and "strategies" in s:
    books = s["strategies"]
elif s:
    books = {"det_lwd_live": {k: s.get(k) for k in
             ("done_slugs","deployed","realized_total","realized_by_day","pending")}}
else:
    books = {}
fills = [json.loads(l) for l in fl.read_text().splitlines() if l.strip()] if fl.exists() else []
setts = [json.loads(l) for l in se.read_text().splitlines() if l.strip()] if se.exists() else []
def _sid(x): return x.get("strategy_id") or "det_lwd_live"   # legacy lines = det_lwd_live
utc_today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
il_mid = dt.datetime.now(IL).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
# today's P&L (Israel-local day) from the forward-only settlements ledger
today_il = {}
for x in setts:
    if float(x.get("ts",0)) >= il_mid:
        today_il[_sid(x)] = today_il.get(_sid(x),0.0) + float(x.get("pnl",0.0))
now_ts = dt.datetime.now(dt.timezone.utc).timestamp()   # for span-based daily$ (restart-immune)
print(f"  {'strategy':20}{'balance':>9}{'total$':>9}{'daily$':>9}{'todayIL$':>10}{'trTdy':>6}{'trTot':>6}{'open':>5}{'capLeftUTC':>11}{'bankLeft':>9}")
for sid, b in sorted(books.items()):
    b = b or {}
    base = float(b.get("bankroll_usd", 100.0))            # starting balance == bankroll == $100
    rt   = float(b.get("realized_total", 0.0))
    cap  = float(b.get("max_daily_loss_usd", 25.0))
    day_utc = float((b.get("realized_by_day") or {}).get(utc_today, 0.0))
    okf  = [f for f in fills if f.get("ok") and _sid(f)==sid]
    trtdy = sum(1 for f in okf if float(f.get("ts",0)) >= il_mid)
    open_n = len(b.get("pending") or [])
    cap_left  = cap  + min(0.0, day_utc)                  # remaining daily LOSS budget (UTC)
    bank_left = base + min(0.0, rt)                       # remaining lifetime LOSS budget
    # daily$ = realized_total annualized over the strategy's LIVE SPAN (first ok-fill -> now).
    # Span-based so it's restart-immune (same convention as the paper §5 daily$). Needs >1h span.
    fts = [float(f.get("ts",0)) for f in okf if float(f.get("ts",0)) > 0]
    span_s = (now_ts - min(fts)) if fts else 0.0
    daily = (rt * 86400.0 / span_s) if span_s > 3600 else float("nan")
    daily_str = f"{daily:>+9.2f}" if daily == daily else f"{'n/a':>9}"   # nan => just-deployed
    print(f"  {sid[:20]:20}{base+rt:>9.2f}{rt:>+9.2f}{daily_str}{today_il.get(sid,0.0):>+10.2f}"
          f"{trtdy:>6}{len(okf):>6}{open_n:>5}{cap_left:>11.2f}{bank_left:>9.2f}")
filled = [f for f in fills if f.get("ok")]
print(f"  deployed_total ${s.get('deployed_total', s.get('deployed',0)):.2f} | "
      f"{len(fills)} order attempts ({len(filled)} filled, {len(fills)-len(filled)} no-fill)")
print("  NOTE: this table is the executor's OWN book (per-strategy detail + the live LOSS caps)."
      " The AUTHORITATIVE realized total is the Polymarket data-api GROUND TRUTH printed in the next"
      " block — always read that as the headline P&L, and heed its divergence flag (the book can"
      " under-count if the executor missed settlements during downtime). daily$ = total$ annualized"
      " over the live span (first fill->now), restart-immune, 'n/a' until >1h. 'todayIL$' is the"
      " Israel-day P&L from settlements.jsonl; the daily LOSS cap is a UTC mechanism (capLeftUTC).")
PYEOF
echo "=== LIVE wallet-wide cross-check — REAL P&L by market (Polymarket data-api ground truth) ==="
uv run python - <<'PYEOF'
import asyncio, aiohttp, json, re, pathlib
st = pathlib.Path("data/live/executor_state.json"); fl = pathlib.Path("data/live/fills.jsonl")
s = json.loads(st.read_text()) if st.exists() else {}
fills = [json.loads(l) for l in fl.read_text().splitlines() if l.strip()] if fl.exists() else []
filled = [f for f in fills if f.get("ok")]
try:
    from mean_reversion_live.clients.data_api import fetch_activity, fetch_positions
    proxy = ""; env_rpc = None
    for ln in open(".env"):
        mm = re.match(r'\s*POLYMARKET_PROXY_(?:ADDRESS|WALLET)\s*=\s*["\x27]?([0-9a-fA-Fx]+)', ln)
        if mm: proxy = mm.group(1)
        mr = re.match(r'\s*POLYGON_RPC\s*=\s*["\x27]?(\S+?)["\x27]?\s*$', ln)
        if mr: env_rpc = mr.group(1)
    # RPC list for the on-chain cash read — prefer the bot's .env RPC; AVOID polygon-rpc.com (its
    # public key is disabled -> returns an error and a false $0 balance).
    RPCS = [r for r in [env_rpc, "https://polygon-bor-rpc.publicnode.com",
                        "https://polygon.llamarpc.com"] if r]
    PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # "Available to trade" collateral
    async def _cash():   # own session (the cross-check's session is already closed by here)
        cd = "0x70a08231" + proxy.lower().replace("0x", "").rjust(64, "0")  # balanceOf(proxy)
        async with aiohttp.ClientSession() as cs:
            for rpc in RPCS:
                try:
                    async with cs.post(rpc, json={"jsonrpc": "2.0", "method": "eth_call",
                            "params": [{"to": PUSD, "data": cd}, "latest"], "id": 1}) as r:
                        j = await r.json()
                    if j.get("result"): return int(j["result"], 16) / 1e6
                except Exception:
                    continue
        return None
    since = min((f.get("ts", 0) for f in fills), default=0) - 60   # isolate THIS probe's markets
    async def _p():
        async with aiohttp.ClientSession() as sess:
            # NO max_records — see fetch_activity's docstring. A loser emits one record
            # (the BUY), a winner two (BUY + REDEEM), so any cap strands redeems whose buy
            # fell outside the window and the pairing below reads them as pure profit.
            # That bug reported +$272 on a -$49 book until 2026-07-24.
            acts = await fetch_activity(sess, proxy)                     # multi-day shared wallet
            pos = await fetch_positions(sess, proxy)
        posb = {p.get("title", ""): p for p in pos if "up or down" in str(p.get("title", "")).lower()}
        by = {}
        for a in acts:
            if "up or down" not in a.title.lower() or a.timestamp < since:
                continue
            m = by.setdefault(a.title, {"buy": 0., "redeem": 0., "side": ""})
            if a.type == "TRADE" and a.side == "BUY": m["buy"] += a.usdc_size; m["side"] = a.outcome
            elif a.type in ("REDEEM", "MERGE"): m["redeem"] += a.usdc_size
        print(f"  {'market':40}{'side':5}{'cost':>7}{'P&L':>8}  status")
        real = openexp = 0.; w = lo = 0; orphan = 0; orphan_usd = 0.
        for title, m in sorted(by.items()):
            if m["buy"] <= 0:
                # Redeem with no BUY in window => the buy predates `since` (or the history
                # got truncated). Counting it would be free money. Drop + tally.
                orphan += 1; orphan_usd += m["redeem"]; continue
            p = posb.get(title); cp = float(p.get("curPrice") or 0) if p else None
            if m["redeem"] > 0: status, pnl = "WON", m["redeem"] - m["buy"]; real += pnl; w += 1
            elif cp is not None and cp <= 0.01: status, pnl = "LOST", -m["buy"]; real += pnl; lo += 1
            elif cp is not None and cp >= 0.99: status, pnl = "WON*", float(p.get("size") or 0) - m["buy"]; real += pnl; w += 1
            elif cp is not None: status, pnl = "OPEN", float(p.get("size") or 0) * cp - m["buy"]; openexp += m["buy"]
            # No position record at all = resolved worthless and aged out of /positions.
            # It is a LOSS, not an unknown; scoring it 0 hid real losses.
            else: status, pnl = "LOST*", -m["buy"]; real += pnl; lo += 1
            print(f"  {title[:40]:40}{str(m['side'])[:4]:5}{m['buy']:>7.2f}{pnl:>+8.2f}  {status}")
        if orphan:
            print(f"  ({orphan} redeem-only markets dropped, ${orphan_usd:.2f} — buys predate the probe window)")
        # ===== HEADLINE: Polymarket data-api is the AUTHORITATIVE realized P&L. Reconcile the
        # executor's OWN book against it and flag divergence (missed settlements / downtime). =====
        book = sum(float(b.get("realized_total", 0.0)) for b in (s.get("strategies") or {}).values())
        diff = book - real
        under = real - book   # >0 => book UNDER-counts reality = the failure mode that matters
        print(f"  {'-'*70}")
        # Actual Polymarket account balance (matches the website's "Available to trade" / "Portfolio").
        cash = await _cash()
        posval = sum(float(p.get("size") or 0) * float(p.get("curPrice") or 0) for p in pos)
        if cash is not None:
            print(f"  💰 POLYMARKET ACCOUNT: Available to trade ${cash:,.2f}  |  Portfolio ${cash + posval:,.2f}"
                  f"  (open-position value ${posval:.2f})")
        else:
            print(f"  💰 POLYMARKET ACCOUNT: (cash read failed — RPC unavailable)")
        print(f"  🎯 GROUND TRUTH (Polymarket data-api): ${real:+.2f} realized  ({w}W/{lo}L, open exposure ${openexp:.2f})")
        print(f"     executor-book total: ${book:+.2f}    Δ(book−truth) = ${diff:+.2f}")
        # The book books wins at $1.00/share at settlement; the data-api measures actual redeemed
        # cash. So a SMALL POSITIVE Δ (~$0.09/win) is NORMAL convention, not an error. The real
        # alarm is the book UNDER-counting reality (missed settlements during executor downtime).
        # Judge the over-count PER WIN, not as a % of `real`: the convention gap scales with the
        # number of wins, while `real` can sit near zero — a %-of-real threshold turns a perfectly
        # normal $0.11/win into a false "possible double-book" alarm (it did, 2026-07-24).
        per_win = diff / w if w else 0.0
        if under > 3.0:
            print(f"  ⚠️  BOOK UNDER-COUNTS reality by ${under:.2f} — missed settlements (likely executor")
            print(f"      downtime). FIX: stop executor → `uv run python scripts/backfill_settlements.py")
            print(f"      --execute` → relaunch. (The headline ground-truth number above is still correct.)")
        elif per_win > 0.20 and diff > 8.0:
            print(f"  ⚠️  BOOK OVER-COUNTS reality by ${diff:.2f} (${per_win:.3f}/win vs the ~$0.09 $1/share")
            print(f"      convention) — investigate (possible double-book).")
        else:
            print(f"  ✓ book ${book:+.2f} ≈ reality ${real:+.2f} (Δ ${diff:+.2f} = ${per_win:.3f}/win; a small + Δ is the executor's")
            print(f"    $1/share settlement convention, ~$0.09/win above actual redeemed cash — normal).")
    asyncio.run(_p())
except Exception as e:
    print(f"  (live P&L from data-api unavailable: {e})  <- can't ground-truth; trust the per-strategy book with caution")
PYEOF
```

**LIVE caveats to surface (real money):**
- **TWO live strategies run, each isolated** (own $100 bankroll + $25/day UTC cap; the
  executor keeps a separate book per strategy, so neither can interrupt the other):
  - `det_lwd_live` — a ~break-even edge on Chainlink, so it's an EXECUTION/clean-data probe,
    NOT a profit play. Report it as ~flat.
  - `det_d12_dual_live` — **the live PRIMARY since 2026-06-09** (replaced `det_d12_wide_live`).
    det_d12 + DUAL-ORACLE gate (Chainlink must AGREE with Coinbase at entry) + max_ask 0.78 +
    adverse_vel2. Chainlink backtest future +$1.97/tr [+1.31,+2.59], WR 87% (≈2× the old det_d12
    +$1.20). Call out its balance, today P&L, trade count, and whether it's tracking +$1.97/tr.
    See [[dual-oracle-gate-det-d12]] / docs/research/DUAL_ORACLE_2026-06-09.md.
  - `det_d12_wide_live` — **BACKUP (live:false since 2026-06-09)**, +$21.93 history preserved.
    It won't trade unless re-armed (flip live:true). If you see no det_d12_wide_live intents
    post-cutover, that's correct — det_d12_dual_live is the active one.
  - Surface the per-strategy table (balance, total/today P&L, trades, cap-left, bankroll-left) for
    the live strategies. Worst-case combined exposure is two independent $100 bankrolls (det_lwd_live
    + det_d12_dual_live) on one shared wallet.
  - **Dual-oracle line** (det_d12_dual_*): from the signal log, count `skipped_oracle_disagree`
    (flip-trades the gate avoided) + `skipped_oracle_missing` (Chainlink unavailable → fail-closed
    skip; a SUSTAINED high count means the live cl_dist wiring is broken — investigate ws_collector).
  - **Fill line** (`fills.jsonl`): the laddered fill records `attempts`/`rounds`/`avg_price`; flag
    any `avg_price` above the strategy's `max_ask` (should never happen — the cap prevents overpay).
- **Headline P&L = the data-api GROUND TRUTH block, not the per-strategy table.** The per-strategy
  table is the executor's own book (good for per-strategy detail + the live caps), but it can
  under-count if the executor missed settlements during downtime — that's exactly what happened
  pre-2026-06-08 (45 resolved windows were never booked; backfilled via
  `scripts/backfill_settlements.py`). The status now prints `🎯 GROUND TRUTH` + `Δ(book−truth)`;
  **report the ground-truth number, and if the ⚠️ divergence flag fires, surface it and run the
  backfill.** (Data-api caveat: it reconstructs from recent activity/positions, so it's reliable
  for the live probe's horizon but isn't a permanent ledger — the executor book is the durable one,
  which is why we keep them reconciled.)
- **Settlement caveat:** the paper twin can log a near-strike window as a WIN while Polymarket
  settled it a LOSS (our Chainlink asof ≠ Polymarket's exact boundary reading). So trust the
  data-api block for real outcomes; do NOT trust the paper twin's win/loss on near-strike fills.
- If a live strategy's intent isn't in `data/live/intents.jsonl` yet, it hasn't fired since the
  bot was last restarted with the new strategies.yaml — note it (restart via /mean-rev-restart).
- If the live executor is DOWN and neither `data/KILL` nor `data/live/EXEC_KILL` exists, surface
  it as a real outage — the user restarts real money manually (don't auto-restart it).

**The forward-test edges (paper, the week's focus):** `fav_lowvol`, `fav_deepdown`, `fav_momentum`,
`fav_disagree` — the 4 VERIFIED favourite-value edges (book underprices near-locked favourites;
latency-proof, hold-to-resolution); PLUS the paper `det_d12_wide_v1` (still running for OOS data
alongside its live twin `det_d12_wide_live`). The §5/§5b tables auto-include them (heartbeat keys).
Call out `det_d12_wide_v1`'s paper PnL + WR (compare it to the live twin) and the favourite-value
combined PnL + WR vs the legacy det/sqp.

### 2c. Redeem + deposit-confirm — now GASLESS + AUTOMATED by the claim daemon (just REPORT here)

As of 2026-06-08 redemption + deposit-confirm runs UNATTENDED in `claim_loop.sh` (launched by
`start_all.sh`, self-healed by `respawn_generic.sh`, at wall-clock minutes `CLAIM_AT_MINUTES`=:05
and :35), and as of the
later 2026-06-08 update it is **GASLESS** — it submits via **Polymarket's meta-transaction relayer**
(`relayer-v2.polymarket.com`, the same path the UI's "Redeem" button uses), so the relayer pays gas
and the owner **EOA needs NO MATIC**. SHARED-WALLET SAFE + idempotent: only `-updown-15m-` markets,
winners only, gated on the on-chain redeemability check before each submit. Each cycle does three
steps — **approve** USDC.e→Onramp (one-time), **redeem** wins → USDC.e, **wrap** ALL USDC.e → pUSD
("Confirm pending deposit", every cycle so nothing strands). Relayer creds are auto-derived from the
private key (fallback: `BUILDER_API_KEY/BUILDER_SECRET/BUILDER_PASS_PHRASE` in `.env`). `CLAIM_VIA_RELAYER=0`
switches back to the old gas-paying EOA path (only useful if the relayer is down AND the EOA has MATIC).
The status check broadcasts nothing — it just verifies the daemon is healthy and reports state.

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion
echo "claim daemon: $(pgrep -f claim_loop | tr '\n' ' ' || echo DOWN)"
[ -f data/live/CLAIM_KILL ] && echo "CLAIM_KILL: PRESENT (claiming paused)" || echo "CLAIM_KILL: absent"
echo "--- recent claim ledger (via=relayer expected; watch for relay_auth_failed / relay_preflight_failed) ---"
tail -6 data/live/claims.jsonl 2>/dev/null || echo "  (no claims yet)"
echo "--- on-chain balances + allowance (read-only) ---"
uv run --python 3.11 --no-project --with web3 --with eth-account --with eth-abi \
  --with requests --with python-dotenv python - <<'PY' 2>&1 | grep -vE "^\[|info |warning "
import os,sys; from pathlib import Path
sys.path.insert(0,str(Path('src/mean_reversion_live/live')))
from dotenv import load_dotenv; load_dotenv('.env')
import claimer
proxy=os.environ["POLYMARKET_PROXY_ADDRESS"]; rpc=os.environ.get("POLYGON_RPC") or claimer.DEFAULT_RPC
pk=os.environ["POLYMARKET_PRIVATE_KEY"]
pusd=claimer._get_erc20_balance_raw(rpc,claimer.PUSD,proxy)/1e6
usdce=claimer._get_erc20_balance_raw(rpc,claimer.USDC_E,proxy)/1e6
al=claimer._get_erc20_allowance_raw(rpc,claimer.USDC_E,proxy,claimer.COLLATERAL_ONRAMP)
eoa=claimer.eoa_address(pk); matic=claimer.get_matic_balance(eoa)
print(f"  pUSD (tradeable): ${pusd:.2f} | USDC.e (pending deposit): ${usdce:.2f} | Onramp-approved: {al>=claimer.APPROVE_MIN_RAW}")
print(f"  EOA gas: {matic:.4f} MATIC (informational — gasless relayer does NOT need it)")
PY
```

**Report:** daemon up/down, last few ledger actions (expect `via:"relayer"` + a `tx_hash`), and the
balances. Healthy = USDC.e ≈ $0 (nothing stuck), Onramp-approved = True; **EOA gas is now
informational** (the gasless relayer pays gas, so a near-zero EOA is FINE). **Flag** any of:
`relay_auth_failed` or `relay_preflight_failed` in `claims.jsonl` (🔴 relayer creds rejected — tell
the user to create a Relayer API key at polymarket.com/settings?tab=api-keys and set `BUILDER_*` in
`.env`; meanwhile wins won't clear); daemon DOWN while CLAIM_KILL absent (real outage — relaunch via
`start_all.sh`); USDC.e > a few $ sitting for >1h (deposit-confirm wedged — check
`logs/claim_loop.log`); Onramp-approved = False (approval missing). **Manual one-shot fallback** (if
the daemon is down): `scripts/live_claim.py --execute` (dry-run first without `--execute`). The first
real-money relay in a session may prompt for Bash permission — approve it.

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

### 5. Compute total / daily / monthly PnL — per-strategy, on OFFICIAL on-chain labels

**Source of truth: `data/research/paper_official/daily_scores.parquet`, NOT the portfolio
JSONs / `trades.jsonl`.** The paper engine settles on its own reconstructed-Chainlink read,
which disagrees with real on-chain settlement on ~17.6% of identical markets and errs in our
favour 2.4:1 — the engine tape runs **~3x hot** (2026-08-03 audit: 7d engine +$1,568 vs
official −$242 on the SAME trades; `det_lwd_v1_capped` is a 201x inflation ratio). Never lead
with engine dollars. The parquet carries `pnl_official` AND `pnl_engine` per sid per day, so
both are shown and the gap stays visible instead of hidden.

Rows are the **virgin era (entry >= 2026-06-19)** — never revealed to any sweep, and it
excludes the degraded 06-05..06-12 window (`research/analysis/resettle_official.py:35-42`).
Pending-label trades are excluded upstream, never imputed (`resettle_official.py:122`).

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && uv run python <<'EOF'
import json, pathlib, subprocess, datetime as dt
import pandas as pd

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
proc_elapsed_s = None   # process runtime — for the RUNTIME line only, NEVER for daily$
if PID:
    try:
        proc_elapsed_s = parse_etime(subprocess.check_output(["ps","-p",PID,"-o","etime="]).decode())
    except Exception:
        pass

VIRGIN = "2026-06-19"                       # official-label era boundary
P = pathlib.Path("data/research/paper_official/daily_scores.parquet")
age_h = (dt.datetime.now().timestamp() - P.stat().st_mtime) / 3600
df = pd.read_parquet(P)
df = df[df["utc_date"] >= VIRGIN]
# Only ENABLED strategies (heartbeat keys = what the running engine actually loaded).
try:
    enabled = set(json.load(open("data/state/last_tick.json")).get("strategy_pnl", {}))
    if enabled:
        df = df[df["strategy_id"].isin(enabled)]
except Exception:
    pass

now = pd.Timestamp.now(tz="UTC")
def span_days(first):    # span-based, restart-immune (same convention as the live table)
    return max((now - pd.Timestamp(first, tz="UTC")).total_seconds() / 86400.0, 1e-9)

g = (df.groupby("strategy_id")
       .agg(n=("n","sum"), off=("pnl_official","sum"), eng=("pnl_engine","sum"),
            wins=("wins","sum"), first=("utc_date","min"))
       .sort_values("off", ascending=False))

print(f"labels refreshed {age_h:.1f}h ago"
      f"{'  ⚠️ STALE — run ./scripts/nightly_honest.sh' if age_h > 26 else ''}")
print(f"OFFICIAL-settled, virgin era (>= {VIRGIN}). engine$ shown only to expose the inflation.")
print()
print(f"{'strategy':26}{'tr':>5}{'total$':>10}{'daily$':>9}{'month$':>10}{'WR':>6}"
      f"{'engine$':>10}{'infl':>7}{'span_d':>8}")
for sid, r in g.iterrows():
    sd = span_days(r["first"])
    infl = (r["eng"] / r["off"]) if abs(r["off"]) > 1e-9 else float("nan")
    print(f"{sid[:26]:26}{int(r['n']):>5}{r['off']:>+10.2f}{r['off']/sd:>+9.2f}"
          f"{r['off']*30/sd:>+10.2f}{r['wins']/r['n']*100:>5.0f}%{r['eng']:>+10.2f}"
          f"{infl:>7.1f}{sd:>8.2f}")
tn, toff, teng = int(g["n"].sum()), g["off"].sum(), g["eng"].sum()
tsd = span_days(df["utc_date"].min())
print(f"{'>>> TOTAL':26}{tn:>5}{toff:>+10.2f}{toff/tsd:>+9.2f}{toff*30/tsd:>+10.2f}"
      f"{'':>6}{teng:>+10.2f}{(teng/toff if abs(toff)>1e-9 else float('nan')):>7.1f}{tsd:>8.2f}")
print(f"proc_runtime={proc_elapsed_s or 'n/a'}s  (health line only — NOT the daily$ basis)")
EOF
```

Notes:
- **Report the `total$` / `daily$` / `month$` columns (official) as THE paper numbers.** Mention
  `engine$` / `infl` only as a one-line caveat ("engine tape reads $X, ~Nx inflated") — never as
  the headline. If a strategy's `infl` is wild (>5x), say so: it means its engine P&L is nearly
  all mislabeling (`det_lwd_v1` is ∞ — engine +$1,446, official −$13).
- **`daily$` is SPAN-based** = `official_total × 86400 / (now − first virgin-era trade day)`.
  Restart-immune: a fresh `run_combined` resets process etime to ~0, but the parquet is durable.
- Monthly forecast = `daily × 30`. Linear extrapolation, not seasonal — caveat it.
- **Freshness matters.** Labels come from the nightly (`scripts/nightly_honest.sh`, launchd
  ~03:15Z). Today's trades have NO official label until it next runs, so today is under-counted
  in this table by design — use §5b for today. On a gate morning, run the nightly by hand first.
- `%`-of-capital columns are gone (meaningless for fixed-$/trade strategies; only ~$10×concurrent
  is ever deployed). Report **total$, daily$, WR, today$** instead.

### 5b. TODAY'S CHANGE (per-strategy + total) + daily-cap status + condition slice

**This is the section the user specifically wants surfaced** — today's PnL/trade
change per strategy and overall. `today` = the **Israel-local calendar day**
(Asia/Jerusalem, per the user's timezone preference), measured by each trade's
**settlement** (`exit_ts_ms`, when the PnL is realized). The daily-loss cap is a
separate, **UTC-day** mechanism (`DailyLossGuard`), so its flag is computed on UTC
and labelled as such — don't conflate the two "today"s.

Dynamic over the **enabled** strategies (heartbeat keys), so it auto-covers the v2
strategies and any future ones. Reads `trades_detailed.jsonl` (fallback `trades.jsonl`).

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && python3 <<'EOF'
import json, pathlib, datetime as dt, collections
try:
    from zoneinfo import ZoneInfo
    IL = ZoneInfo("Asia/Jerusalem")
except Exception:
    IL = dt.timezone(dt.timedelta(hours=3))            # IDT fallback (summer)
now_il = dt.datetime.now(IL)
il_midnight = now_il.replace(hour=0, minute=0, second=0, microsecond=0)
il_start_ms = il_midnight.timestamp() * 1000
utc_today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

# enabled = the running engine's heartbeat keys (dynamic; covers det_*_v1 AND v2)
try:
    enabled = list(json.load(open("data/state/last_tick.json")).get("strategy_pnl", {}))
except Exception:
    enabled = []

def won_of(r): return r.get("won", 1 if r.get("pnl", 0) > 0 else 0)
def ts_of(r):  return r.get("exit_ts_ms") or r.get("entry_ts_ms", 0)

allr, tot_pnl, tot_n = [], 0.0, 0
print(f"TODAY'S CHANGE (since {il_midnight:%Y-%m-%d 00:00} IDT/IST)"
      f"  [ENGINE-settled — today has no official labels yet; runs ~3x hot]:")
for sid in sorted(enabled):
    f = pathlib.Path("data/jsonl")/sid/"trades_detailed.jsonl"
    if not f.exists(): f = pathlib.Path("data/jsonl")/sid/"trades.jsonl"
    rows = [json.loads(L) for L in f.read_text().splitlines() if L.strip()] if f.exists() else []
    allr += [r for r in rows if "utc_hour" in r]
    td = [r for r in rows if ts_of(r) >= il_start_ms]
    pnl_t = sum(r["pnl"] for r in td)
    wr_t = sum(won_of(r) for r in td)/len(td)*100 if td else 0.0
    wr_all = sum(won_of(r) for r in rows)/len(rows)*100 if rows else 0.0
    tot_pnl += pnl_t; tot_n += len(td)
    # cap fires on the UTC day:
    utc_pnl = sum(r["pnl"] for r in rows
                  if dt.datetime.utcfromtimestamp(ts_of(r)/1000).strftime("%Y-%m-%d") == utc_today)
    cap = "  ⚠CAP HIT(UTC)" if ("capped" in sid and utc_pnl <= -50) else ""
    print(f"  {sid:22} today: {len(td):>2}tr ${pnl_t:+8.2f} WR {wr_t:3.0f}% | all-time {len(rows):>3}tr WR {wr_all:.0f}%{cap}")
print(f"  {'>>> TOTAL':22} today: {tot_n:>2}tr ${tot_pnl:+8.2f}")

if allr:   # filter teaser — worst UTC hours by WR (feeds the weekly review)
    byh = collections.defaultdict(lambda: [0, 0])
    for r in allr: byh[r["utc_hour"]][0] += won_of(r); byh[r["utc_hour"]][1] += 1
    worst = sorted((w/n, h, n) for h, (w, n) in byh.items() if n >= 5)[:3]
    print("filter teaser — worst UTC hours by WR (n>=5):",
          [(f"h{h:02d}", f"{wr*100:.0f}%", f"n={n}") for wr, h, n in worst] or "n/a")
EOF
```

Notes:
- **This table is ENGINE-settled and there is no honest alternative intraday** — official
  on-chain labels for today's windows only land when the nightly runs (`scripts/nightly_honest.sh`,
  ~03:15Z). The engine tape reads ~3x hot in aggregate (per §5), so **always label table ① as
  "engine-settled (~3x hot)"** and never quote today's dollars as a realized result. If the user
  wants an honest today figure, run the nightly by hand first, then re-read §5.
- **Surface the TOTAL today's-change line and per-strategy today columns prominently** in the response (header row + a `today $` column in the per-strategy table).
- "Today" is Israel-local; a trade counts toward today when it **settled** today. Early after IDT midnight the numbers reset — that's expected, not a fault.
- The `_capped` variant matches its uncapped twin until a **UTC** day's loss reaches −$50, then stops entering. The cap flag is UTC-based by design.
- v2 vs v1: `det_lwd_v2`/`det_sqp_v2` are the filtered variants (deployed 2026-05-30). Compare today's change v1 vs v2 — that A/B is the whole point of running both.
- `det_*` = the post-2026-05-29 edge strategies. Absent `trades_detailed.jsonl` = nothing settled yet — normal (selective edge).

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

**MANDATORY — the report MUST contain THREE full table blocks, in this order (user instruction
2026-06-08: "keep the live trading tables, but also show the previous tables (total, daily) of
paper trading"). NEVER collapse the paper tables into inline bullet lists to save space:**
  - **🔴 LIVE (real money)** — lead with the **💰 POLYMARKET ACCOUNT** line (actual on-chain
    "Available to trade" + "Portfolio" — what the website shows) and the **🎯 GROUND TRUTH**
    realized P&L (data-api), incl. the Δ(book−truth) divergence verdict. THEN the per-strategy
    table from §2b: balance, **total$**, **daily$** (span-based), today$, trades, cap-left,
    bank-left — for every live strategy (`det_lwd_live`, `det_d12_wide_live`).
  - **① Paper — Today's change (engine-settled, ~3x hot)** — the per-strategy IDT table
    (today tr, today$, WR) + TOTAL row. MUST carry the engine-settled label (§5b).
  - **② Paper — All-time (OFFICIAL on-chain labels)** — the per-strategy table (trades,
    **total$**, WR, **daily$** [span-based, §5]) + TOTAL row. These are the honest dollars;
    add ONE caveat line giving the engine total + inflation ratio, never the reverse.

Every table carries BOTH a `total$` and a `daily$` view (daily$ = span-based, restart-immune) so
live and paper are reported symmetrically. The TOTAL row is mandatory on each.

The paper tables are NOT optional and are NOT replaced by the live table — paper is the 13-strategy
forward test; live is the 2-strategy real-money probe. Show BOTH. Word budget may exceed 450 to fit
all three — completeness wins over brevity here.

Target structure (the user prefers detail over brevity):

```markdown
## mean-rev bot status — <UTC time>

🟢 **ALL GREEN** — running for <runtime>, healthy heartbeat

| | |
|---|---|
| Process | ✓ PID X (heartbeat Ys ago) |
| KILL sentinel | absent |
| Active markets | N |
| Queue | 0 (engine keeping up) |
| Strategies | 6 enabled (det_lwd_v1/v2, det_sqp_v1/v2, + 2 capped) |
| Runtime | <e.g. ~24 min / 3h 12m / 2d 5h> |
| Total PnL | $±X.XX |
| **Today's change** | **$±X.XX (N trades) — since 00:00 IDT** |
| Avg daily | $±X.XX/day |
| Monthly forecast | $±X.XX — 30d linear extrapolation; <confidence note> |

**🔴 LIVE — real money** (per §2b; each strategy started $100, balance = $100 + total$, wins recycle):

> 💰 **Polymarket account:** Available to trade **$X.XX** · Portfolio **$X.XX** (on-chain, = website)
> 🎯 **Ground truth (data-api):** **$±X.XX** realized (NW/NL) · executor book $±X.XX · Δ $±X.XX — `<✓ matches / ⚠️ under-counts → backfill>`

| strategy_id | balance | total $ | daily $ | today $ | tr today | tr total | cap left | bank left |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| `det_lwd_live` | $X | $±X | $±X | $±X | n | N | $X | $X |
| `det_d12_wide_live` | $X | $±X | n/a¹ | $±X | n | N | $X | $X |
| **TOTAL** | **$X** | **$±X** | **$±X** | **$±X** | **n** | **N** | — | — |

¹ `daily$` shows `n/a` until a strategy has >1h of live span (a just-deployed probe can't be annualized yet).

**PAPER (forward test) — per-strategy, split into TWO tables. Both carry a TOTAL row. Strategy
descriptions go in the short list below, NOT as a table column.**

**① Today's change** (Israel-local day, by settlement — the headline the user asked for).
⚠️ **Engine-settled, ~3x hot** — official labels for today arrive with the next nightly:

| strategy_id | today tr | today $ | today WR | cap |
|---|--:|--:|--:|---|
| `det_lwd_v1` | n | $±X | X% | — |
| `det_lwd_v2` | n | $±X | X% | — |
| `det_lwd_v1_capped` | n | $±X | X% | ⚠ if UTC −50 hit |
| `det_sqp_v1` | n | $±X | X% | — |
| `det_sqp_v2` | n | $±X | X% | — |
| `det_sqp_v1_capped` | n | $±X | X% | ⚠ if UTC −50 hit |
| **TOTAL** | **N** | **$±X** | — | |

**② All-time — OFFICIAL on-chain labels**, virgin era (entry >= 2026-06-19), from §5.
These are the honest dollars; the engine tape is ~3x higher and belongs in the caveat only:

| strategy_id | trades | total $ | WR | daily $ |
|---|--:|--:|--:|--:|
| `det_lwd_v1` | N | $±X | X% | $±X |
| `det_lwd_v2` | N | $±X | X% | $±X |
| `det_lwd_v1_capped` | N | $±X | X% | $±X |
| `det_sqp_v1` | N | $±X | X% | $±X |
| `det_sqp_v2` | N | $±X | X% | $±X |
| `det_sqp_v1_capped` | N | $±X | X% | $±X |
| **TOTAL** | **N** | **$±X** | — | **$±X** |

**What each strategy is** (one line each, only if the user needs the reminder — skip if they know):
- `det_lwd_v1` — Determinism (Phase 1): last 60s, spot ≥5bps from strike, buy favourite ≤0.90, hold. **Unfiltered control.** Backtest +$1.68/tr, 91% WR.
- `det_lwd_v2` — v1 + loss filters (max_ask 0.88, skip adverse_vel>2bps, require strike_crossings≥1). Backfilled 05-30.
- `det_sqp_v1` — Stale-quote (Phase 2): mid-window, model-vs-market [8,30]¢ + spot jump ≥8bps, hold. **Unfiltered control**, WR ~50%/high-variance.
- `det_sqp_v2` — v1 + loss filters (margin 0.12, skip dist>19bps). Backtest lifts EV ~2×. Backfilled 05-30.
- `*_capped` — same rule as its twin + **$50/day UTC max-loss breaker**.

Table rules:
- **Both tables MUST show the TOTAL row.** Today's TOTAL also appears in the header table's "Today's change" row — keep them consistent.
- Numbers come straight from §5b (today) and §5 (all-time). Compare **v1 vs v2** in both tables — that A/B is the point of running both.
- If the user wants it even tighter, collapse to just table ① (today) + the header Total PnL — but default to both.
- These are **fixed-$10/trade, hold-to-resolution** strategies. The `%`-of-capital columns are meaningless (only ~$10×concurrent is deployed) — report **$/trade, WR, $/day, today's PnL** instead.
- `daily $` is **span-based** = `total$ × 86400 / (now − first_settled_trade)` (per §5), NOT process
  runtime — so it survives restarts. Same convention for the live table (span from first live fill).
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
- **Quoting an engine-settled paper dollar as a result.** Every paper P&L claim in the diary
  must cite the OFFICIAL number (§5 / `score_gates` / `daily_scores.parquet`). The engine tape
  may appear only when explicitly labelled ("engine +$1,568, official −$242 — 3x inflation").
  A diary full of engine dollars is how a −$242 week gets remembered as a +$1,568 one.
- **Un-caveated hype numbers.** hype has no Chainlink feed, so the paper engine settles it via
  `coinbase_fallback` at ~2x inflation; it was 44% of paper P&L since 07-17. On a hype-heavy
  day, say so.

Before writing, glance at `tail -50 data/diary.md` to see the most recent entries — patterns and recurrence are the point.

#### How to fill the "Notes" column

The ONLY enabled strategies since the 2026-05-29 cutover are the 6 below (the
edge hunt's survivors + their 2026-05-30 loss-filtered v2 variants). All prior
mean-reversion strategies (`cfg_*`, `sv2_*`, `v2_gold_*`, `relaxed_v1`) are
**disabled** — the research proved mean-reversion is the wrong direction here; the
live edge is momentum/determinism (book lags spot). If a disabled id ever appears
in the report, the enabled-filter broke — fix it; don't report stale strategies.

| strategy_id | Notes template |
|---|---|
| `det_lwd_v1` | "Determinism / late-window pickoff (Phase 1, **robust primary**). Last 60s of a 15m window, spot ≥5bps from strike (favourite agrees), buy favourite at ask ≤0.90, **hold to resolution**. Backtest OOS +$1.68/tr, 91% WR. **Unfiltered control** for the v2 A/B. Fires in last 60s of ANY 15m window — all hours, NOT ASIA-only." |
| `det_lwd_v2` | "v1 + validated loss filters: max_ask **0.88**, skip when spot reverts toward strike (**adverse_vel>2bps**), require **≥1 strike crossing** (drop already-decided blowout windows). Backfilled from v1 live-start 05-30. Backtest: EV/tr +1.5→+2.2, WR→91%." |
| `det_lwd_v1_capped` | "v1 rule + **$50/day UTC max-loss breaker** (live-deployment candidate). Mirrors det_lwd_v1 until a UTC day hits −$50, then stops." |
| `det_sqp_v1` | "Stale-quote pickoff (Phase 2, **secondary, higher-variance**). Mid-window, |empirical P(Up\|z) − mid| in [8,30]¢ AND a recent spot jump ≥8bps, buy the model side, hold. Backtest +$3.5/tr but **WR ~50%** (bigger payoffs, outlier-sensitive). Unfiltered control." |
| `det_sqp_v2` | "v1 + validated loss filters: margin **0.12** (drop tiny-mispricing noise), skip **dist>19bps** (far-from-strike near-certainties). Backfilled 05-30. Backtest: EV/tr ~2×, WR 55→60%, total profit up." |
| `det_sqp_v1_capped` | "sq v1 rule + **$50/day UTC breaker**." |

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
