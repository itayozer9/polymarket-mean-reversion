"""The collector-dark alarm (`scripts/check_collector_liveness.sh`).

Why this alarm exists: on 2026-08-09 the tick collector wrote ZERO rows for 43
minutes while the heartbeat stayed at 1s, active_markets stayed non-zero, and the
process stayed alive. Nothing reported it - it was found by a human reading the log.
That is the third silent-data-stop of this project (external CPU 06-06, self-inflicted
heartbeat parse 06-12, DNS 08-09), and the same shape as the Chainlink outage that
went 32h unnoticed until `rows_ok=0` got an alarm in hourly_monitor.sh.

`aggregator_status` is logged every 10s with `rows_written` counting the rows emitted
in that cycle. A healthy collector always has a current 15m window (they are
contiguous), so rows_written > 0 every cycle. Sustained zero = dark.

The alarm requires the last THREE consecutive cycles to be zero (~30s) so a single
unlucky sample at a window boundary cannot fire it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_collector_liveness.sh"


def _agg(rows_written: int, ts: str = "2026-08-09T22:21:05.001375Z") -> str:
    return (f"{ts} [info     ] aggregator_status              "
            f"active_markets=14 books_seen=42 elapsed_sec=10 "
            f"rows_written={rows_written} trades_seen=479698")


def _run(log_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), str(log_path)],
                          capture_output=True, text=True)


def test_healthy_collector_is_silent(tmp_path):
    """rows_written>0 every cycle: exit 0, no output."""
    log = tmp_path / "combined.log"
    log.write_text("\n".join(_agg(70) for _ in range(5)) + "\n")

    r = _run(log)

    assert r.returncode == 0, f"healthy collector must not alarm: {r.stdout}{r.stderr}"
    assert r.stdout.strip() == "", f"healthy collector must print nothing, got: {r.stdout!r}"


def test_sustained_zero_rows_fires_the_alarm(tmp_path):
    """Three consecutive zero cycles = the 2026-08-09 signature: alarm."""
    log = tmp_path / "combined.log"
    log.write_text("\n".join([_agg(70), _agg(7), _agg(0), _agg(0), _agg(0)]) + "\n")

    r = _run(log)

    assert r.returncode != 0, "sustained rows_written=0 must exit non-zero"
    assert "rows_written=0" in r.stdout or "dark" in r.stdout.lower(), (
        f"alarm must name the symptom, got: {r.stdout!r}")


def test_single_zero_cycle_does_not_fire(tmp_path):
    """One zero sample surrounded by healthy cycles is not an outage."""
    log = tmp_path / "combined.log"
    log.write_text("\n".join([_agg(0), _agg(70), _agg(58), _agg(70)]) + "\n")

    r = _run(log)

    assert r.returncode == 0, f"a single zero cycle must not alarm: {r.stdout}"


def test_recovered_collector_does_not_fire(tmp_path):
    """The incident then recovery: zeros are in the past, latest cycles are healthy.

    This is the state the bot was in after 22:45 - the alarm must go quiet on its own
    rather than latch, or it would still be screaming the next morning.
    """
    log = tmp_path / "combined.log"
    log.write_text("\n".join([_agg(0), _agg(0), _agg(0), _agg(7), _agg(70), _agg(70)]) + "\n")

    r = _run(log)

    assert r.returncode == 0, f"a recovered collector must not alarm: {r.stdout}"


def test_missing_aggregator_lines_fires(tmp_path):
    """No aggregator_status at all = collector not running. Same precedent as the
    Chainlink alarm's empty-match case, which reports rather than silently passing."""
    log = tmp_path / "combined.log"
    log.write_text("2026-08-09T22:21:05Z [info     ] spot_ws_status rows_written=944222\n")

    r = _run(log)

    assert r.returncode != 0, "absent aggregator_status must be reported, not ignored"


def test_missing_log_file_fires(tmp_path):
    """A vanished/rotated-away log must not read as healthy."""
    r = _run(tmp_path / "does_not_exist.log")

    assert r.returncode != 0, "a missing log must be reported, not treated as healthy"
