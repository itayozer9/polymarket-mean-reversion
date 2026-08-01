---
name: mean-rev-restart
description: Use when the user wants to start or restart the polymarket-mean-reversion bot. Triggered by "start the mean-rev bot", "restart mean-rev", "boot the bot", "/mean-rev-restart". Performs preflight checks then runs scripts/start_all.sh.
---

# Restart polymarket-mean-reversion bot

Clean startup with preflight checks before launching.

## When to invoke

- User says "start / restart / boot / bring it back"
- User says "/mean-rev-restart"
- After a `mean-rev-stop` if user explicitly asks to restart

## Steps

### 1. Preflight (READ-ONLY checks BEFORE starting)

Run each check; surface any failure to the user BEFORE running start_all.sh.

#### a. KILL sentinel absent
```bash
ls /Users/itayozer/dev/polymarket-mean-reversion/data/KILL 2>&1
```
If it exists: ask the user "KILL sentinel exists — should I remove it?" Wait for confirmation. Then `rm data/KILL`.

#### b. No stale process
```bash
ls /Users/itayozer/dev/polymarket-mean-reversion/.combined.pid 2>&1
ps aux | grep "mean_reversion_live.scripts.run_combined" | grep -v grep | head -3
```
If pid file exists and process is alive: tell user "already running". Abort.
If pid file exists but process is dead: warn, then `rm .combined.pid` and continue.

#### c. polymarket-arb path reachable
```bash
ls /Users/itayozer/dev/polymarket-arb/scripts/mean_reversion/signals.py
```
If missing: abort. The bot imports from there.

#### d. data_v2 symlink intact
```bash
readlink /Users/itayozer/dev/polymarket-arb/data_v2 2>&1
```
Must point to `/Users/itayozer/dev/polymarket-mean-reversion/data/historical`. If broken: surface and stop.

#### e. Polymarket Gamma reachable
```bash
curl -s -o /dev/null -w "%{http_code}" https://gamma-api.polymarket.com/events?limit=1
```
Expect "200". If not: surface network issue.

#### f. strategies.yaml parses
```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && uv run python -c "
from pathlib import Path
from mean_reversion_live.engine.registry import load_strategies
ss = load_strategies(Path('strategies.yaml'), Path('data'))
print(f'{len(ss)} strategies loaded')
"
```

### 2. Run the start script

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && ./scripts/start_all.sh
```

This:
- Refuses to start if pid file exists and process is alive
- Clears `data/KILL` (sentinel)
- Launches `nohup uv run python -m mean_reversion_live.scripts.run_combined`
- Writes `.combined.pid`
- Sleeps 3s and verifies still alive

### 3. Wait 30s, then verify trading flow

```bash
sleep 30 && cd /Users/itayozer/dev/polymarket-mean-reversion && uv run python -m mean_reversion_live.scripts.status
```

Look for: process alive, last_tick heartbeat <10s, active_markets > 0.

### 4. Format response

```markdown
## mean-rev bot started — <UTC time>

✓ Process running (pid=X)
✓ Heartbeat Xs ago, N active markets
✓ N strategies enabled  (count is dynamic — all `enabled: true` entries in strategies.yaml; e.g. det_d12_wide_v1 + fav_* + det_* families)

**Preflight:**
- KILL sentinel: ✓ absent
- polymarket-arb path: ✓ reachable
- data_v2 symlink: ✓ valid
- Gamma API: ✓ 200 OK
- strategies.yaml: ✓ N strategies parsed (dynamic; report the actual count from load_strategies)

**Now collecting:** tick data → data/live/, paper trades → data/jsonl/<sid>/

Tail logs: `./scripts/tail_logs.sh`
Status anytime: `/mean-rev-status`
Stop: `/mean-rev-stop`
```

## What this skill must NOT do

- Don't start if a preflight check fails — fail fast and surface.
- Don't ignore a stale pid file — clean it up only after explicit confirmation if `ps` says no process.
- Don't enable live trading. The `.env` has `POLYMARKET_PRIVATE_KEY` preserved for future use, but the current code path is paper-only.
- Don't touch `strategies.yaml` — only the user edits it.

## Edge cases

- If `start_all.sh` exits non-zero: read `logs/combined.log` tail and surface the error.
- If 30s after start there's still no heartbeat: investigate logs. Don't assume success.
- If process dies within 3s of start: the start_all.sh script will tail logs automatically. Surface the error.
