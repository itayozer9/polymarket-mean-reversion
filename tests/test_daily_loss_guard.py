"""Unit tests for the redesigned DailyLossGuard (docs/research/daily_cap_redesign.md).

Three modes:
  - "soft_settled" (default, legacy): blocks on SETTLED daily PnL only; re-arms on
    recovery; blind to open positions; no restart replay. Kept byte-for-byte so the
    existing det_sqp_v1_capped paper twin is unchanged.
  - "hard_worstcase": reserves each open position's worst-case loss against the cap
    at entry -> realized daily PnL provably never < -cap. Re-arming is safe.
  - "hard_worstcase_latch": same hard bound, but stops for the rest of the UTC day
    once the limit is reached (no resume on recovery).
"""
from __future__ import annotations
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mean_reversion_live.engine.determinism_state import DailyLossGuard  # noqa: E402

DAY = 86_400_000
T0 = 1_700_000_000_000  # fixed instant inside one UTC day


# ── backward-compatibility ────────────────────────────────────────────────────

def test_cap_none_never_blocks_any_mode():
    for mode in ("soft_settled", "hard_worstcase", "hard_worstcase_latch"):
        g = DailyLossGuard(max_daily_loss_usd=None, mode=mode)
        assert g.would_block(T0, 10.0) is False
        g.on_entry(T0, "p0", 10.0)        # no-op, must not raise
        g.on_settle(T0, "p0", -10.0)      # no-op, must not raise
        assert g.would_block(T0, 10.0) is False


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        DailyLossGuard(max_daily_loss_usd=50.0, mode="nonsense")


def test_legacy_blocked_record_shims_still_work():
    # The existing det/sq tests call blocked()/record() directly — keep them working.
    g = DailyLossGuard(max_daily_loss_usd=15.0)  # default soft_settled
    assert not g.blocked(T0)
    g.record(T0, -10.0)
    assert not g.blocked(T0)
    g.record(T0, -8.0)            # cumulative -18 <= -15
    assert g.blocked(T0)
    assert not g.blocked(T0 + 2 * DAY)   # next UTC day resets


# ── soft_settled (legacy semantics via the new interface) ─────────────────────

def test_soft_settled_is_blind_to_open_exposure():
    g = DailyLossGuard(max_daily_loss_usd=50.0, mode="soft_settled")
    # open six $10 positions (worst case -$60) but nothing settled yet
    for i in range(6):
        g.on_entry(T0, f"p{i}", 10.0)
    # soft mode ignores open exposure -> still NOT blocked (this is the leak)
    assert g.would_block(T0, 10.0) is False


def test_soft_settled_rearms_on_recovery():
    g = DailyLossGuard(max_daily_loss_usd=50.0, mode="soft_settled")
    g.on_settle(T0, "a", -55.0)            # settled -55 <= -50 -> blocked
    assert g.would_block(T0, 10.0) is True
    g.on_settle(T0, "b", +20.0)            # settled -35 -> re-arms (the bug)
    assert g.would_block(T0, 10.0) is False


# ── hard_worstcase: the real bound ────────────────────────────────────────────

def test_hard_worstcase_reserves_open_exposure():
    g = DailyLossGuard(max_daily_loss_usd=50.0, mode="hard_worstcase")
    # five open $10 positions -> worst case -$50, exactly at the cap
    for i in range(5):
        assert g.would_block(T0, 10.0) is False
        g.on_entry(T0, f"p{i}", 10.0)
    # a sixth would push worst-case to -$60 < -$50 -> blocked, though nothing settled
    assert g.would_block(T0, 10.0) is True


def test_hard_worstcase_rearms_safely_after_a_winner():
    g = DailyLossGuard(max_daily_loss_usd=50.0, mode="hard_worstcase")
    for i in range(5):
        g.on_entry(T0, f"p{i}", 10.0)     # worst case -$50, at the cap
    assert g.would_block(T0, 10.0) is True
    g.on_settle(T0, "p0", +12.0)          # winner settles -> headroom returns
    # worst case now = +12 - 4*10 = -28 ; a new $10 entry -> -38 >= -50 -> allowed
    assert g.would_block(T0, 10.0) is False


def test_hard_worstcase_bound_never_breached_property():
    rng = random.Random(20260530)
    cap = 50.0
    max_loss = 10.0
    for _ in range(300):
        g = DailyLossGuard(max_daily_loss_usd=cap, mode="hard_worstcase")
        settled = 0.0
        open_ids: list[str] = []
        nid = 0
        for _ in range(80):
            if open_ids and rng.random() < 0.5:
                pid = open_ids.pop(rng.randrange(len(open_ids)))
                pnl = rng.uniform(0.0, 40.0) if rng.random() < 0.5 else -max_loss
                g.on_settle(T0, pid, pnl)
                settled += pnl
            elif not g.would_block(T0, max_loss):
                pid = f"p{nid}"; nid += 1
                g.on_entry(T0, pid, max_loss)
                open_ids.append(pid)
            assert settled >= -cap - 1e-9     # realized never below the cap
        # drain remaining opens as worst case (all losers) — still bounded
        for pid in open_ids:
            g.on_settle(T0, pid, -max_loss)
            settled += -max_loss
            assert settled >= -cap - 1e-9


# ── hard_worstcase_latch: stop for the day ────────────────────────────────────

def test_latch_stays_blocked_after_recovery_then_resets_next_day():
    g = DailyLossGuard(max_daily_loss_usd=50.0, mode="hard_worstcase_latch")
    for i in range(5):
        g.on_entry(T0, f"p{i}", 10.0)     # worst case hits -$50 -> latches
    assert g.would_block(T0, 10.0) is True
    g.on_settle(T0, "p0", +30.0)          # big winner restores headroom...
    assert g.would_block(T0, 10.0) is True   # ...but latch keeps it shut for the day
    # next UTC day: latch + accumulators reset
    assert g.would_block(T0 + 2 * DAY, 10.0) is False


# ── UTC rollover + restart replay ─────────────────────────────────────────────

def test_utc_rollover_resets_all_state():
    g = DailyLossGuard(max_daily_loss_usd=50.0, mode="hard_worstcase")
    g.on_entry(T0, "p0", 10.0)
    g.on_settle(T0, "p0", -10.0)
    g.on_entry(T0, "p1", 10.0)            # leaves an open position + settled -10
    assert g.would_block(T0 + 2 * DAY, 10.0) is False   # new day: clean slate
    # and the worst-case accounting really did reset (5 fresh $10 opens fit)
    for i in range(5):
        assert g.would_block(T0 + 2 * DAY, 10.0) is False
        g.on_entry(T0 + 2 * DAY, f"q{i}", 10.0)
    assert g.would_block(T0 + 2 * DAY, 10.0) is True


def test_replay_settled_rebuilds_realized_in_hard_mode():
    g = DailyLossGuard(max_daily_loss_usd=50.0, mode="hard_worstcase")
    g.replay_settled(T0, -45.0)           # restart: replay today's settled losses
    # with -45 settled, a new $10 worst-case -> -55 < -50 -> blocked
    assert g.would_block(T0, 10.0) is True


def test_replay_settled_is_noop_in_soft_mode():
    # soft mode preserves the legacy restart amnesia (so the paper twin is unchanged)
    g = DailyLossGuard(max_daily_loss_usd=50.0, mode="soft_settled")
    g.replay_settled(T0, -80.0)
    assert g.would_block(T0, 10.0) is False
