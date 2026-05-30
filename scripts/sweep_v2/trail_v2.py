"""Phase 2 — two-step trail with activation threshold.

The base engine's `exit.trailing_stop_pct` activates the moment peak > entry.
This module replaces it with a smarter trail:
  - `activation_pct`: only start trailing once PnL has reached at least X%.
  - `lock_pct`: once active, exit if bid drops to peak × (1 − lock_pct/100).

Optionally multi-step: a list of (activation_pct, lock_pct) tuples where the
tightest applicable rule wins (e.g., once +50% lock at peak−30%, once +100%
tighten to peak−15%).

Implementation: monkey-patches `scripts.mean_reversion.simulate.exit_signal`
at sweep_v2 entrypoint time. The arb engine source is untouched.
"""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple


# Thread-local config so concurrent workers don't trample each other.
_local = threading.local()


def set_trail_v2(steps: Optional[List[Tuple[float, float]]]):
    """Set the trail-v2 staircase for the current thread.
    steps = list of (activation_pct, lock_pct) tuples sorted ascending by
    activation_pct. None disables trail-v2 (engine falls back to default
    exit.trailing_stop_pct behavior).
    """
    _local.steps = sorted(steps, key=lambda s: s[0]) if steps else None


def get_trail_v2():
    return getattr(_local, "steps", None)


def install_patch():
    """Replace simulate.exit_signal in-place. Safe to call multiple times."""
    from scripts.mean_reversion import simulate as sim_mod
    from scripts.mean_reversion import signals as sig_mod

    original = sig_mod.exit_signal

    def _patched(tick, position, ex, seconds_in_position):
        steps = get_trail_v2()
        if steps is None:
            return original(tick, position, ex, seconds_in_position)
        # Mirror the original logic but swap in v2 trail.
        bid = sig_mod._side_bid(tick, position.side)
        if bid <= 0:
            if seconds_in_position >= ex.max_hold_sec:
                return "max_hold"
            return None
        pnl_pct = (bid - position.entry_price) / position.entry_price * 100
        if pnl_pct >= ex.profit_target_pct:
            return "profit_target"
        if ex.stop_loss_pct is not None and pnl_pct <= -ex.stop_loss_pct:
            return "stop_loss"
        # v2 trail: find the tightest active step (highest activation reached).
        peak_pnl_pct = (position.peak_mid - position.entry_price) / position.entry_price * 100
        active = None
        for act, lock in steps:
            if peak_pnl_pct >= act:
                active = (act, lock)
            else:
                break
        if active is not None:
            floor = position.peak_mid * (1.0 - active[1] / 100.0)
            if bid <= floor:
                return "v2_trail"
        if seconds_in_position >= ex.max_hold_sec:
            return "max_hold"
        return None

    sim_mod.exit_signal = _patched


def make_staircase(activation_locks: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Build a sorted staircase from raw input."""
    return sorted(activation_locks, key=lambda x: x[0])
