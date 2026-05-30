---
name: mean-rev-stop
description: Use when the user wants to stop the polymarket-mean-reversion bot. Triggered by "stop the mean-rev bot", "shut down the bot", "halt mean-rev", "/mean-rev-stop". Performs a GRACEFUL shutdown — final portfolio dump + jsonl flush + clean WS close.
---

# Stop polymarket-mean-reversion bot

Graceful SIGTERM-based shutdown with 30s grace period, falls back to SIGKILL.

## When to invoke

- User says "stop the (mean-rev) bot"
- User says "shut it down" / "kill it"
- User says "halt trading"
- `/mean-rev-stop`

## Steps

### 1. Confirm first (destructive)

Tell the user what you're about to do:
> "I'll send SIGTERM to the bot. It will: finish processing the current tick, persist all portfolios, flush jsonl, close WS cleanly. Then exit. If it doesn't stop within 30s I'll SIGKILL. Confirm to proceed."

Wait for explicit confirmation ("yes", "go", "stop it"). If unsure, ASK.

### 2. Read STATE before stopping (so we know what we shut down)

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && uv run python -m mean_reversion_live.scripts.status 2>&1 | head -20
```

### 3. Run the stop script

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && ./scripts/stop_all.sh
```

This:
- Sends SIGTERM to pid in `.combined.pid`
- Waits up to 30s for graceful shutdown
- SIGKILLs if not stopped
- Removes the pid file

### 4. Verify it's actually stopped

```bash
ps aux | grep "mean_reversion_live" | grep -v grep | head -3
# If nothing: ✓ stopped
ls /Users/itayozer/dev/polymarket-mean-reversion/.combined.pid 2>&1
# If "No such file": pid file was cleaned ✓
```

### 5. Show final stats

```bash
cd /Users/itayozer/dev/polymarket-mean-reversion && python3 <<'EOF'
import json, pathlib
# enabled = strategies the engine was running (heartbeat persists across the stop);
# skip the 26 retired strategies' stale portfolios.
try:
    enabled = set(json.load(open("data/state/last_tick.json")).get("strategy_pnl", {}))
except Exception:
    enabled = set()
for f in sorted(pathlib.Path("data/portfolios").glob("*.json")):
    if enabled and f.stem not in enabled: continue
    d = json.loads(f.read_text())
    print(f"{d.get('strategy_id', f.stem)}: trades={d.get('n_trades',0)} pnl=${d.get('total_pnl',0):+.2f} wr={d.get('win_rate',0):.2f}")
EOF
```

### 6. Format response

```markdown
## mean-rev bot stopped — <UTC time>

✓ Process exited cleanly after Xs.

**Final stats:** (the 4 active edge strategies — determinism + stale-quote)
- det_lwd_v1: N trades, $X.XX
- det_lwd_v1_capped: N trades, $X.XX
- det_sqp_v1: N trades, $X.XX
- det_sqp_v1_capped: N trades, $X.XX

**State preserved in:**
- `data/portfolios/*.json` (atomic snapshots)
- `data/jsonl/<sid>/trades.jsonl` (full trade log)
- `data/live/*.csv.gz` (tick data — N rows captured)

To restart, run `/mean-rev-restart` or `./scripts/start_all.sh`.
```

## What this skill must NOT do

- Don't restart the bot in the same session.
- Don't delete state files (portfolios, jsonl, tick CSVs).
- Don't remove the KILL sentinel.
- Don't proceed without confirmation if the user's request is ambiguous.

## Edge cases

- If pid file is missing: tell user the bot wasn't running.
- If process is unresponsive after 30s SIGTERM: SIGKILL is automatic, mention it in response.
- If a `data/KILL` sentinel exists: confirm with user if they want to remove it after shutdown so the next start works.
