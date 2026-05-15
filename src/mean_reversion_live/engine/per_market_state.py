"""Per-market FLAT/ARMED/HOLDING state machine.

This is the eversion of the inner loop of `polymarket-arb`'s
`simulate.simulate_market`. The body must be a near-mechanical translation —
any drift breaks the load-bearing replay parity test.

Approach: buffer the last N ticks where N >= drop_window_sec + 5, then build
the same numpy arrays + call `_precompute_features` / `_entry_features_at`
that the batch simulator uses. This guarantees identical features and
identical decisions.

Optional `observer` callback (PARITY-SAFE):
    Called once at the end of every on_tick via a try/finally block, AFTER
    all rng draws and AFTER all state mutations. The callback receives a dict
    summarizing what happened on this tick. It MUST NOT raise (we swallow);
    it MUST NOT call rng. Use it for logging / analysis only.
"""
from __future__ import annotations
from collections import deque
from dataclasses import replace
from typing import Callable, Deque, Optional, Tuple

import numpy as np

from mean_reversion_live.adapters.arb_imports import (
    EntryFeatures,
    EntryParams,
    Position,
    SimConfig,
    TICK_DTYPE,
    TickEvent,
    Trade,
    _entry_features_at,
    _precompute_features,
    _side_ask_depth,
    _side_bid,
    _side_mid,
    _side_price,
    _tick_event,
    calc_fee,
    entry_signal,
    exit_signal,
    Portfolio,
)


# Single-param relaxation factors for near-miss detection. All checks are
# applied independently; we report which one (if any) would have made the
# signal fire on its own.
_NEAR_MISS_DROP_FACTOR = 0.8     # require ≥ 80% of drop_magnitude_pct
_NEAR_MISS_PROX_FACTOR = 1.5     # allow up to 1.5× proximity_max_pct
_NEAR_MISS_TIME_FACTOR = 0.5     # require ≥ 50% of min_time_left_sec
_NEAR_MISS_PRICE_FACTOR = 0.25   # widen the price band by ±25%


def _detect_near_miss(
    tick: TickEvent,
    features: EntryFeatures,
    entry: EntryParams,
    filt,
    window_duration_sec: int,
) -> Optional[str]:
    """Pure-function check: would relaxing exactly ONE entry param have fired the signal?

    Returns the name of the relaxed param ("drop" | "prox" | "time" | "price") or None.
    Performs no rng draws and mutates no state.
    """
    # drop relaxation
    if entry.drop_magnitude_pct > 0:
        relaxed = replace(entry, drop_magnitude_pct=entry.drop_magnitude_pct * _NEAR_MISS_DROP_FACTOR)
        if entry_signal(tick, features, relaxed, filt, window_duration_sec) is not None:
            return "drop"
    # proximity relaxation
    if entry.proximity_max_pct < 1e9:
        relaxed = replace(entry, proximity_max_pct=entry.proximity_max_pct * _NEAR_MISS_PROX_FACTOR)
        if entry_signal(tick, features, relaxed, filt, window_duration_sec) is not None:
            return "prox"
    # min_time_left relaxation
    if entry.min_time_left_sec > 0:
        relaxed = replace(entry, min_time_left_sec=int(entry.min_time_left_sec * _NEAR_MISS_TIME_FACTOR))
        if entry_signal(tick, features, relaxed, filt, window_duration_sec) is not None:
            return "time"
    # price-band relaxation
    new_lo = max(0.0, entry.entry_price_min * (1 - _NEAR_MISS_PRICE_FACTOR))
    new_hi = min(1.0, entry.entry_price_max * (1 + _NEAR_MISS_PRICE_FACTOR))
    relaxed = replace(entry, entry_price_min=new_lo, entry_price_max=new_hi)
    if entry_signal(tick, features, relaxed, filt, window_duration_sec) is not None:
        return "price"
    return None


def _features_to_dict(tick: TickEvent, features: EntryFeatures) -> dict:
    return {
        "yes_mid": tick.yes_mid,
        "no_mid": tick.no_mid,
        "spread_yes": tick.spread_yes,
        "spread_no": tick.spread_no,
        "yes_bid_depth": tick.yes_bid_depth,
        "yes_ask_depth": tick.yes_ask_depth,
        "yes_drop_pct": features.yes_drop_in_window * 100,
        "no_drop_pct": features.no_drop_in_window * 100,
        "proximity_pct": features.proximity,
        "yes_book_imbalance": features.yes_book_imbalance,
        "no_book_imbalance": features.no_book_imbalance,
        "realized_vol_60s": features.realized_vol_60s,
        "vol_bucket": features.vol_bucket,
        "hour_bucket": features.hour_bucket,
        "seconds_into_window": tick.seconds_into_window,
    }


class PerMarketState:
    """Tick-by-tick state machine for one (strategy, market) pair.

    Drives the exact same decision logic as `simulate.simulate_market` but
    consumes ticks one at a time so a live WS stream can feed it.
    """

    def __init__(
        self,
        slug: str,
        cfg: SimConfig,
        window_duration_sec: int,
        observer: Optional[Callable[[dict], None]] = None,
    ):
        self.slug = slug
        self.cfg = cfg
        self.window_duration_sec = window_duration_sec
        # We need at least `drop_window_sec` of prior ticks to compute the
        # rolling drop feature. The batch simulator does this once over the
        # full array, but for streaming we keep the smallest sufficient buffer.
        # _precompute_features uses a rolling-max over (i - window_sec, i],
        # so we keep `drop_window_sec + 5` ticks for safety.
        self._buf_size = cfg.entry.drop_window_sec + 5
        # We buffer raw tick rows in their numpy structured form so we can
        # rebuild a small array and call the SAME _precompute_features that
        # the batch simulator uses.
        self._tick_buffer: Deque[np.ndarray] = deque(maxlen=self._buf_size)

        # State machine variables — mirror simulate_market's locals.
        self.state = "FLAT"  # FLAT | ARMED | HOLDING
        self.position: Optional[Position] = None
        self._armed_until_idx = -1
        self._armed_side: Optional[str] = None
        self._has_traded = False
        self._entry_seconds = 0
        # We use the absolute tick index (count of ticks seen) so the ARMED
        # delay logic produces the same delay_ticks count as the batch sim.
        self._tick_count = 0

        # PARITY-SAFE observer hook. See module docstring.
        self._observer = observer

    def on_tick(
        self,
        arr_row: np.ndarray,
        portfolio: Portfolio,
        rng: np.random.Generator,
        outcome: Optional[Tuple[str, float]] = None,
    ) -> Optional[Trade]:
        """Consume one tick. Wraps `_on_tick_impl` to invoke the observer once
        at the very end, AFTER all rng draws and state changes.

        The observer is invoked via try/finally so it runs even if a downstream
        bug raises — it never blocks the trade return path.
        """
        decision_event: dict = {
            "ts_ms": int(arr_row["timestamp_ms"]),
            "slug": self.slug,
            "state_before": self.state,
            "decision": "unknown",
            "side_signal": None,
            "near_miss": None,
            "trade_closed": False,
            "features": None,
        }
        trade: Optional[Trade] = None
        try:
            trade = self._on_tick_impl(arr_row, portfolio, rng, outcome, decision_event)
            decision_event["trade_closed"] = trade is not None
            return trade
        finally:
            decision_event["state_after"] = self.state
            if self._observer is not None:
                try:
                    self._observer(decision_event)
                except Exception:  # pragma: no cover — observers must not break the engine
                    pass

    def _on_tick_impl(
        self,
        arr_row: np.ndarray,
        portfolio: Portfolio,
        rng: np.random.Generator,
        outcome: Optional[Tuple[str, float]],
        decision: dict,
    ) -> Optional[Trade]:
        """The actual tick processor.

        IMPORTANT: this method must process EVERY tick, including the very first,
        so that the RNG draw sequence (signal_skip_prob, reaction_delay) matches
        the batch simulator exactly. Skipping tick 0 would shift the RNG state.
        """
        self._tick_buffer.append(arr_row)
        i = self._tick_count
        self._tick_count += 1

        tick = _tick_event(np.array([arr_row], dtype=TICK_DTYPE), 0)

        # Build a small local array of the last buffered ticks; call the SAME
        # _precompute_features as the batch simulator. `i_local` is the index
        # of the current tick within that buffer.
        local_arr = np.array(list(self._tick_buffer), dtype=TICK_DTYPE)
        i_local = len(self._tick_buffer) - 1

        # ───────── HOLDING ─────────
        if self.state == "HOLDING" and self.position is not None:
            bid_now = (
                float(arr_row["yes_best_bid"]) if self.position.side == "UP"
                else float(arr_row["no_best_bid"])
            )
            if bid_now > self.position.peak_mid:
                self.position.peak_mid = bid_now
            seconds_held = tick.seconds_into_window - self._entry_seconds
            reason = exit_signal(tick, self.position, self.cfg.exit, seconds_held)
            if reason is None and tick.seconds_into_window >= self.window_duration_sec - 2:
                reason = "forced_resolution"
            if reason is not None:
                trade = self._close_position(tick, reason, portfolio, outcome, seconds_held)
                self.position = None
                self.state = "FLAT"
                self._has_traded = True
                decision["decision"] = f"trade_closed_{reason}"
                return trade
            decision["decision"] = "holding"
            return None

        # ───────── ARMED ─────────
        if self.state == "ARMED" and self._armed_side is not None:
            if i >= self._armed_until_idx:
                if rng.random() < self.cfg.fill.reject_prob:
                    armed_side = self._armed_side
                    self.state = "FLAT"
                    self._armed_side = None
                    self._has_traded = True
                    decision["decision"] = "rejected_fill"
                    decision["side_signal"] = armed_side
                    return None
                pos = self._try_fill_entry(tick, self._armed_side)
                if pos is None:
                    self.state = "FLAT"
                    armed_side = self._armed_side
                    self._armed_side = None
                    self._has_traded = True
                    decision["decision"] = "skipped_no_fill"
                    decision["side_signal"] = armed_side
                    return None
                self.position = pos
                self.state = "HOLDING"
                self._entry_seconds = tick.seconds_into_window
                portfolio.on_entry(tick.timestamp_ms)
                decision["decision"] = "fired"
                decision["side_signal"] = self._armed_side
                self._armed_side = None
                return None
            decision["decision"] = "armed_waiting"
            decision["side_signal"] = self._armed_side
            return None

        # ───────── FLAT ─────────
        if self._has_traded:
            decision["decision"] = "skipped_already_traded"
            return None
        if not portfolio.can_enter(tick.timestamp_ms):
            decision["decision"] = "skipped_can_enter"
            return None
        if self.cfg.human.signal_skip_prob > 0 and rng.random() < self.cfg.human.signal_skip_prob:
            decision["decision"] = "skipped_skip_prob"
            return None
        # Recompute features over the buffer. This is O(buf_size * drop_window_sec)
        # per tick but buf_size is tiny (35 for drop_window_sec=30) so it's cheap.
        precomp_local = _precompute_features(local_arr, self.cfg.entry.drop_window_sec)
        ef = _entry_features_at(precomp_local, i_local, tick.timestamp_ms)
        side = entry_signal(tick, ef, self.cfg.entry, self.cfg.filter, self.window_duration_sec)
        if side is None:
            # Near-miss check (PURE — no rng, no state change).
            decision["near_miss"] = _detect_near_miss(
                tick, ef, self.cfg.entry, self.cfg.filter, self.window_duration_sec
            )
            decision["features"] = _features_to_dict(tick, ef)
            decision["decision"] = "near_miss" if decision["near_miss"] else "flat"
            return None
        # ARM with reaction delay.
        delay_sec = rng.uniform(
            self.cfg.human.reaction_delay_min_sec,
            self.cfg.human.reaction_delay_max_sec,
        )
        delay_ticks = max(1, int(np.ceil(delay_sec)))
        self._armed_until_idx = i + delay_ticks
        self._armed_side = side
        self.state = "ARMED"
        decision["decision"] = "armed_new"
        decision["side_signal"] = side
        decision["features"] = _features_to_dict(tick, ef)
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Internals — match _try_fill_entry / _close_position in simulate.py
    # ──────────────────────────────────────────────────────────────────────

    def _try_fill_entry(self, tick: TickEvent, side: str) -> Optional[Position]:
        ask = _side_price(tick, side)
        if ask <= 0 or ask >= 1.0:
            return None
        depth_usd = _side_ask_depth(tick, side)
        bet = self.cfg.human.fixed_bet_usd
        fillable_usd = min(bet, depth_usd)
        if fillable_usd < 1.0:
            return None
        shares = fillable_usd / ask
        return Position(
            side=side,
            entry_price=ask,
            entry_tick_idx=-1,
            entry_ts_ms=tick.timestamp_ms,
            shares=shares,
            bet_usd=fillable_usd,
            peak_mid=(tick.yes_best_bid if side == "UP" else tick.no_best_bid),
        )

    def _close_position(
        self,
        tick: TickEvent,
        reason: str,
        portfolio: Portfolio,
        outcome: Optional[Tuple[str, float]],
        seconds_held: int,
    ) -> Trade:
        pos = self.position
        assert pos is not None
        fee_entry = calc_fee(pos.shares, pos.entry_price, self.cfg.fill.fee_rate)

        if reason == "forced_resolution" and outcome is not None:
            won = (outcome[0] == "Up" and pos.side == "UP") or (
                outcome[0] == "Down" and pos.side == "DOWN"
            )
            exit_price = 1.0 if won else 0.0
            fee_exit = calc_fee(pos.shares, exit_price, self.cfg.fill.fee_rate)
            pnl = (pos.shares * exit_price) - pos.bet_usd - fee_entry - fee_exit
        else:
            bid = _side_bid(tick, pos.side)
            if bid <= 0:
                mid = _side_mid(tick, pos.side)
                exit_price = max(0.0, mid)
            else:
                exit_price = bid
            fee_exit = calc_fee(pos.shares, exit_price, self.cfg.fill.fee_rate)
            pnl = (pos.shares * exit_price) - pos.bet_usd - fee_entry - fee_exit

        trade = Trade(
            slug=self.slug,
            side=pos.side,
            entry_ts_ms=pos.entry_ts_ms,
            exit_ts_ms=tick.timestamp_ms,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            bet_usd=pos.bet_usd,
            fee_total=fee_entry + fee_exit,
            pnl=pnl,
            exit_reason=reason,
            seconds_held=seconds_held,
        )
        portfolio.on_exit(trade)
        return trade
