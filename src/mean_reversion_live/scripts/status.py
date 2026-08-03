"""Read-only status reporter: are processes alive, last tick age, per-strategy stats."""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

from mean_reversion_live.config import get_settings


def _read_pid(path: Path):
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
    except (ValueError, OSError):
        return None
    return pid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _respawn_count_today(repo: Path) -> int:
    """Count `respawn_loop_spawn` events since UTC midnight from logs/respawn.log."""
    path = repo / "logs" / "respawn.log"
    if not path.exists():
        return 0
    midnight = dt.datetime.now(dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    count = 0
    try:
        with open(path, "r") as f:
            for line in f:
                # Line format: "2026-05-15T08:35:01Z respawn_loop_spawn n=0"
                if " respawn_loop_spawn " not in line:
                    continue
                try:
                    ts = dt.datetime.strptime(line[:20], "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=dt.timezone.utc
                    )
                except ValueError:
                    continue
                # Subtract 1 from raw spawn count: the initial spawn isn't a respawn.
                if ts >= midnight:
                    count += 1
    except OSError:
        return 0
    return max(0, count - 1)


def main() -> int:
    settings = get_settings()
    repo = Path(__file__).resolve().parents[3]
    pid = _read_pid(repo / ".combined.pid")
    print("# Mean-Reversion Live — Status")
    print()
    print(f"- repo: `{repo}`")
    if pid is None:
        print(f"- combined process: **NOT RUNNING** (no pid file)")
    elif not _alive(pid):
        print(f"- combined process: **DEAD** (pid file pid={pid} but no such process)")
    else:
        print(f"- combined process: alive (pid={pid})")

    kill_file = settings.kill_sentinel
    if kill_file.exists():
        print(f"- KILL sentinel: **PRESENT** at {kill_file} — process will exit at next check")
    else:
        print(f"- KILL sentinel: absent")

    print(f"- respawns today: {_respawn_count_today(repo)}")

    # last_tick heartbeat
    state_path = settings.state_path / "last_tick.json"
    if state_path.exists():
        try:
            hb = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            hb = None
        if hb:
            ts_ms = hb.get("ts_ms", 0)
            age_s = int(time.time()) - ts_ms // 1000
            print(f"- last tick heartbeat: {age_s}s ago")
            print(f"- active markets: {hb.get('active_markets', '?')}")
            print(f"- queue size: {hb.get('queue_size', '?')}")
            if "books_in_memory" in hb:
                print(f"- books in memory: {hb['books_in_memory']}")
            if "disk_free_gb" in hb and hb["disk_free_gb"] is not None:
                print(f"- disk free: {hb['disk_free_gb']} GB")
            if "signals_today" in hb:
                print(f"- signals logged today: {hb['signals_today']}")
            if "strategy_pnl" in hb:
                print()
                # ENGINE-settled = the paper engine's own Chainlink read. It disagrees with
                # real on-chain settlement on ~17.6% of identical markets, biased 2.4:1 in
                # our favour, so these dollars run ~3x hot. Honest numbers (official on-chain
                # labels) live in data/research/paper_official/ — see scoreboard.md.
                print("## Per-strategy PnL (current session — ENGINE-settled, ~3x inflated)")
                print()
                print("| strategy_id | trades | PnL |")
                print("|---|---|---|")
                for sid, pnl in hb.get("strategy_pnl", {}).items():
                    n = hb.get("strategy_trades", {}).get(sid, 0)
                    print(f"| `{sid}` | {n} | ${pnl:+.2f} |")
    else:
        print(f"- last tick: no heartbeat file yet at {state_path}")

    # Portfolio files
    print()
    print("## Portfolio files")
    print()
    pf_dir = settings.portfolios_path
    if pf_dir.exists():
        for pf in sorted(pf_dir.glob("*.json")):
            try:
                d = json.loads(pf.read_text())
                print(f"- `{pf.name}`: n_trades={d.get('n_trades', 0)} pnl=${d.get('total_pnl', 0):+.2f} wr={d.get('win_rate', 0):.3f}")
            except Exception:
                print(f"- `{pf.name}`: <unreadable>")
    else:
        print("- (no portfolios dir)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
