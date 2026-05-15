"""Per-market FLAT/ARMED/HOLDING state machine.

This is the eversion of the inner loop of `polymarket-arb`'s
`simulate.simulate_market`. The body must be a near-mechanical translation —
any drift breaks the load-bearing replay parity test.

Approach: buffer the last N ticks where N >= drop_window_sec + 5, then build
the same numpy arrays + call `_precompute_features` / `_entry_features_at`
that the batch simulator uses. This guarantees identical features and
identical decisions.
"""
from __future__ import annotations
from collections import deque
from typing import Deque, Optional, Tuple

import numpy as np

from mean_reversion_live.adapters.arb_imports import (
    EntryFeatures,
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


class PerMarketState:
    """Tick-by-tick state machine for one (strategy, market) pair.

    Drives the exact same decision logic as `simulate.simulate_market` but
    consumes ticks one at a time so a live WS stream can feed it.
    """

    def __init__(self, slug: str, cfg: SimConfig, window_duration_sec: int):
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

    def on_tick(
        self,
        arr_row: np.ndarray,
        portfolio: Portfolio,
        rng: np.random.Generator,
        outcome: Optional[Tuple[str, float]] = None,
    ) -> Optional[Trade]:
        """Consume one tick (numpy structured row of TICK_DTYPE).

        Returns a Trade if this tick closed a position, else None.
        Mutates `portfolio` via on_entry / on_exit.

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
                return trade
            return None

        # ───────── ARMED ─────────
        if self.state == "ARMED" and self._armed_side is not None:
            if i >= self._armed_until_idx:
                if rng.random() < self.cfg.fill.reject_prob:
                    self.state = "FLAT"
                    self._armed_side = None
                    self._has_traded = True
                    return None
                pos = self._try_fill_entry(tick, self._armed_side)
                if pos is None:
                    self.state = "FLAT"
                    self._armed_side = None
                    self._has_traded = True
                    return None
                self.position = pos
                self.state = "HOLDING"
                self._entry_seconds = tick.seconds_into_window
                portfolio.on_entry(tick.timestamp_ms)
                self._armed_side = None
            return None

        # ───────── FLAT ─────────
        if self._has_traded:
            return None
        if not portfolio.can_enter(tick.timestamp_ms):
            return None
        if self.cfg.human.signal_skip_prob > 0 and rng.random() < self.cfg.human.signal_skip_prob:
            return None
        # Recompute features over the buffer. This is O(buf_size * drop_window_sec)
        # per tick but buf_size is tiny (35 for drop_window_sec=30) so it's cheap.
        precomp_local = _precompute_features(local_arr, self.cfg.entry.drop_window_sec)
        ef = _entry_features_at(precomp_local, i_local, tick.timestamp_ms)
        side = entry_signal(tick, ef, self.cfg.entry, self.cfg.filter, self.window_duration_sec)
        if side is None:
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
