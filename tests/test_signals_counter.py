"""Incremental signals_today counter (fix for the heartbeat loop-hog).

2026-06-12: the heartbeat's `_count_signals_today` re-parsed EVERY strategy's
ENTIRE signals.jsonl (4.4 GB / 6.35M records, never rotated) with stdlib json
every 5s — a ~42s synchronous block of the main event loop per tick. That
starved the WS reader past its 20s ping timeout (connections died every ~25-30s
since ~Jun 5), left books 10-25s stale, and killed all live fills once the 5m
markets tripled resync cost.

Replacement under test: SignalsTodayCounter — remembers a byte offset per file
and parses only newly-appended lines. Boot initializes at end-of-file (the
counter means "today's signals observed since boot"); files first seen after
boot are read from byte 0; counts reset at UTC midnight.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.engine.signals_counter import SignalsTodayCounter  # noqa: E402

DAY_MS = 86_400_000
MIDNIGHT = (1781222400) * 1000  # 2026-06-12 00:00:00 UTC in ms
NOW = MIDNIGHT + 10 * 3600 * 1000  # 10:00 UTC


def _write_lines(path: Path, ts_list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for ts in ts_list:
            f.write(json.dumps({"ts_ms": ts, "type": "entry"}) + "\n")


def test_boot_skips_existing_history(tmp_path):
    f = tmp_path / "sid_a" / "signals.jsonl"
    _write_lines(f, [NOW - 3600_000, NOW - 1800_000])  # pre-boot history (today!)
    c = SignalsTodayCounter(tmp_path)
    assert c.poll(NOW) == 0  # history is not re-parsed — count is since-boot


def test_counts_only_newly_appended_lines(tmp_path):
    f = tmp_path / "sid_a" / "signals.jsonl"
    _write_lines(f, [NOW - 3600_000])
    c = SignalsTodayCounter(tmp_path)
    assert c.poll(NOW) == 0
    _write_lines(f, [NOW + 1000, NOW + 2000, NOW + 3000])
    assert c.poll(NOW + 5000) == 3
    # no growth -> unchanged, and nothing re-parsed
    assert c.poll(NOW + 6000) == 3
    _write_lines(f, [NOW + 7000])
    assert c.poll(NOW + 8000) == 4


def test_skips_lines_from_other_days(tmp_path):
    f = tmp_path / "sid_a" / "signals.jsonl"
    f.parent.mkdir(parents=True)
    f.touch()
    c = SignalsTodayCounter(tmp_path)
    _write_lines(f, [NOW + 1000, MIDNIGHT - 5000])  # one today, one yesterday
    assert c.poll(NOW + 2000) == 1


def test_file_created_after_boot_counted_from_start(tmp_path):
    (tmp_path / "sid_a").mkdir()
    c = SignalsTodayCounter(tmp_path)
    assert c.poll(NOW) == 0
    f = tmp_path / "sid_b" / "signals.jsonl"
    _write_lines(f, [NOW + 1000, NOW + 2000])
    assert c.poll(NOW + 3000) == 2


def test_midnight_rollover_resets(tmp_path):
    f = tmp_path / "sid_a" / "signals.jsonl"
    f.parent.mkdir(parents=True)
    f.touch()
    c = SignalsTodayCounter(tmp_path)
    _write_lines(f, [NOW + 1000])
    assert c.poll(NOW + 2000) == 1
    nxt = MIDNIGHT + DAY_MS  # next UTC midnight
    _write_lines(f, [nxt + 1000])
    assert c.poll(nxt + 2000) == 1  # yesterday's 1 dropped, today's 1 counted


def test_malformed_lines_ignored(tmp_path):
    f = tmp_path / "sid_a" / "signals.jsonl"
    f.parent.mkdir(parents=True)
    f.touch()
    c = SignalsTodayCounter(tmp_path)
    with open(f, "a") as fh:
        fh.write("not json\n")
        fh.write(json.dumps({"ts_ms": NOW + 1000}) + "\n")
    assert c.poll(NOW + 2000) == 1
