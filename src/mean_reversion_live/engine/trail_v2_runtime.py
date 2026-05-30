"""Trail-v2 staircase runtime extension for the live engine.

The polymarket-arb engine's `exit.trailing_stop_pct` activates the moment the
position has been in any profit. Trail-v2 adds an activation threshold (only
start trailing once peak PnL has reached X%) and supports a multi-step
staircase that tightens as the trade goes deeper into profit.

Implementation: at app startup, `install_patch()` rebinds
`scripts.mean_reversion.signals.exit_signal` AND the local reference in
`mean_reversion_live.engine.per_market_state` to a wrapper that consults a
per-strategy staircase keyed by `id(cfg.exit)`. If no staircase is registered,
the wrapper delegates to the original engine logic so existing strategies are
unaffected.

CLAUDE.md forbids editing polymarket-arb source; this monkey-patch is the only
seam where we can add custom exit semantics for the live paper bot.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

import structlog

log = structlog.get_logger(__name__)


# Registry: id(ExitParams) → staircase
_STAIRCASES: Dict[int, List[Tuple[float, float]]] = {}
_LOCK = threading.Lock()
_PATCH_INSTALLED = False


def register(ex_params, staircase: Optional[List[Tuple[float, float]]]) -> None:
    """Associate a staircase with a specific ExitParams instance.

    `staircase` is a list of (activation_pct, lock_pct) tuples, sorted
    ascending by activation_pct. None or [] disables trail-v2 for this
    ExitParams (engine falls back to legacy `trailing_stop_pct` logic).
    """
    if not staircase:
        return
    cleaned = sorted([(float(a), float(l)) for a, l in staircase], key=lambda s: s[0])
    with _LOCK:
        _STAIRCASES[id(ex_params)] = cleaned
    log.info("trail_v2_registered", exit_id=id(ex_params), staircase=cleaned)


def get_staircase(ex_params) -> Optional[List[Tuple[float, float]]]:
    return _STAIRCASES.get(id(ex_params))


def has_any_registrations() -> bool:
    return bool(_STAIRCASES)


def install_patch() -> None:
    """Idempotent. Patches both the engine module and per_market_state's local ref."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from scripts.mean_reversion import signals as sig_mod
    original = sig_mod.exit_signal

    def _patched(tick, position, ex, seconds_in_position):
        steps = _STAIRCASES.get(id(ex))
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
                return "trailing_stop"  # reuse engine's exit reason for parity
        if seconds_in_position >= ex.max_hold_sec:
            return "max_hold"
        return None

    sig_mod.exit_signal = _patched

    # PerMarketState imports exit_signal by name at module level — rebind.
    try:
        from mean_reversion_live.engine import per_market_state as pms_mod
        pms_mod.exit_signal = _patched
    except ImportError:
        pass

    _PATCH_INSTALLED = True
    log.info("trail_v2_patch_installed")
