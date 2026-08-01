"""5m discovery is restricted to CO-TERMINAL windows (start :10/:25/:40/:55,
i.e. ending on a quarter-hour boundary).

Those are the only 5m books anything reads: the engine's mode="xb" co5 lookup
(last 300s of each 15m window) and research's ticks5m_coterminal join. The
other two-thirds of 5m windows were pure WS message load — at 15m+5m full
subscription the collector process saturated one core and its connections died
every ~25-30s from inbound backlog (2026-06-12 zero-fill outage).
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.clients.gamma import candidate_window_starts  # noqa: E402

QUARTER = 1781250300  # 2026-06-12 07:45:00 UTC — a quarter-hour boundary


def test_15m_keeps_all_four_slots():
    now = QUARTER + 720  # :12 into the quarter
    starts = candidate_window_starts("15m", now)
    assert starts == [QUARTER - 900, QUARTER, QUARTER + 900, QUARTER + 1800]


def test_5m_only_coterminal_mid_quarter():
    # :12 into the quarter -> 5m slots :05 :10 :15 :20; co-terminal = :10 only
    now = QUARTER + 720
    starts = candidate_window_starts("5m", now)
    assert starts == [QUARTER + 600]
    assert all((s + 300) % 900 == 0 for s in starts)


def test_5m_only_coterminal_early_quarter():
    # :03 into the quarter -> slots :55(prev) :00 :05 :10; co-terminal = :55(prev), :10
    now = QUARTER + 180
    starts = candidate_window_starts("5m", now)
    assert starts == [QUARTER - 300, QUARTER + 600]
    assert all((s + 300) % 900 == 0 for s in starts)
