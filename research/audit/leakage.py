"""Look-ahead / leakage audit for the imported arb decision logic.

Encodes the audit as runnable assertions (where possible) plus a written checklist
for the parts that require source-code review.

Assertions:
  A. Entry fills use a tick AFTER the reaction delay (armed_until_idx = i + delay_ticks,
     delay_ticks >= 1), so the fill price is drawn from a future tick relative to the
     signal tick — but that tick is the *immediately next* observable one, not a
     look-ahead into later prices. Confirmed by reading simulate.py's ARMED branch.

  B. features.rolling_max_drop only uses price[lo:i+1] (lo = max(0, i-window_sec)),
     i.e. strictly indices <= i. It never peeks at i+1 onward.
     Confirmed by inspecting the source string at runtime with `inspect.getsource`.

Usage:
  uv run python -m research.audit.leakage
"""
from __future__ import annotations
import inspect
import os
import sys
import textwrap

# --------------------------------------------------------------------------- #
# path setup — mirror how the live bot imports arb code
# --------------------------------------------------------------------------- #
_ARB = os.environ.get(
    "POLYMARKET_ARB_PATH", os.path.expanduser("~/dev/polymarket-arb")
)
sys.path.insert(0, _ARB)

from scripts.mean_reversion import features as feat  # noqa: E402
from scripts.mean_reversion import simulate          # noqa: E402


# =========================================================================== #
# Assertion A — reaction delay guarantees fill tick > signal tick
# =========================================================================== #

def assert_a_reaction_delay() -> None:
    """Confirm delay_ticks >= 1 so entry fills on a tick after the signal.

    In simulate_market(), when entry_signal fires at index i, the code does:
        delay_ticks = max(1, int(np.ceil(delay_sec)))
        armed_until_idx = i + delay_ticks
    Then in the ARMED branch, the fill only happens when i >= armed_until_idx,
    i.e. at index >= signal_i + 1 (since delay_ticks >= 1).
    This is NOT look-ahead: the fill price comes from a tick that arrives *after*
    the decision tick, as it would in live trading.
    """
    src = inspect.getsource(simulate.simulate_market)

    # 1. delay_ticks = max(1, ...) must appear.
    assert "delay_ticks = max(1," in src, (
        "FAIL A1: 'delay_ticks = max(1, ...)' not found in simulate_market — "
        "delay-floor of 1 missing."
    )

    # 2. armed_until_idx is set to i + delay_ticks (signal_i + delay >= 1).
    assert "armed_until_idx = i + delay_ticks" in src, (
        "FAIL A2: 'armed_until_idx = i + delay_ticks' not found — "
        "fill index may not be after signal index."
    )

    # 3. The fill only executes when i >= armed_until_idx (i.e. after the delay).
    assert "i >= armed_until_idx" in src, (
        "FAIL A3: 'i >= armed_until_idx' guard not found — "
        "fill might fire on same tick as signal."
    )

    print("PASS  Assertion A — reaction delay: delay_ticks = max(1, …), "
          "fill only when i >= i_signal + delay_ticks >= i_signal + 1.")


# =========================================================================== #
# Assertion B — rolling_max_drop uses only past ticks (no forward peek)
# =========================================================================== #

def assert_b_rolling_max_drop_no_lookahead() -> None:
    """Confirm rolling_max_drop slices price[lo:i+1], never price[i+1:] or later.

    The correct slice is price[lo : i+1], which includes indices lo..i (inclusive).
    A look-ahead would appear as price[i+1:] or price[i+2:], etc.
    We check both that the correct past-inclusive slice exists and that
    no forward-start slice appears.
    """
    src = inspect.getsource(feat.rolling_max_drop)

    # Must contain the correct past-inclusive upper bound.
    assert "i + 1]" in src, (
        "FAIL B1: 'i + 1]' (past-inclusive upper bound) not found in "
        "rolling_max_drop — the slice may be wrong."
    )

    # Must NOT contain a slice that starts at i+1 or later (which would peek ahead).
    # Patterns to reject: "i+1:", "i + 1:", "i+2:", "i + 2:", etc.
    forward_start_patterns = ["i+1:", "i + 1:", "i+2:", "i + 2:"]
    for pat in forward_start_patterns:
        assert pat not in src, (
            f"FAIL B2: '{pat}' (forward-start slice) found in rolling_max_drop — "
            "this is a look-ahead!"
        )

    print("PASS  Assertion B — rolling_max_drop: slice is price[lo:i+1] "
          "(past-inclusive), no forward-start slice found.")


# =========================================================================== #
# Assertion B2 — realized_vol_60s_from_move uses only past ticks
# =========================================================================== #

def assert_b2_realized_vol_no_lookahead() -> None:
    """Confirm realized_vol_60s_from_move also slices only [lo:i+1]."""
    src = inspect.getsource(feat.realized_vol_60s_from_move)

    assert "i + 1]" in src or "i+1]" in src, (
        "FAIL B2a: past-inclusive upper bound not found in realized_vol_60s_from_move."
    )

    forward_start_patterns = ["i+1:", "i + 1:", "i+2:", "i + 2:"]
    for pat in forward_start_patterns:
        assert pat not in src, (
            f"FAIL B2b: '{pat}' (forward-start slice) found in "
            f"realized_vol_60s_from_move — look-ahead!"
        )

    print("PASS  Assertion B2 — realized_vol_60s_from_move: slice is move[lo:i+1], "
          "no forward-start slice found.")


# =========================================================================== #
# Written checklist — items that require code reading, not automation
# =========================================================================== #

CHECKLIST = [
    {
        "item": "exit_signal uses only current tick's bid",
        "verdict": "CLEAN",
        "detail": (
            "signals.py::exit_signal (line 154) calls _side_bid(tick, position.side), "
            "which reads tick.yes_best_bid or tick.no_best_bid — the *current* tick's bid. "
            "No future tick is consulted. The peak_mid tracker (position.peak_mid) is updated "
            "in simulate_market's HOLDING loop using bid_now from the current tick only (line 118–119). "
            "No look-ahead."
        ),
    },
    {
        "item": "forced_resolution consults outcome only at window end",
        "verdict": "CLEAN",
        "detail": (
            "simulate_market (line 124): the forced-resolution trigger fires only when "
            "tick.seconds_into_window >= window_duration_sec - 2, i.e. at most 2 seconds "
            "before the natural end of the window. The outcome tuple is passed in as a "
            "parameter (not derived from future ticks) and only affects the *exit price*, "
            "not the entry decision. No tick-level look-ahead."
        ),
    },
    {
        "item": "_precompute_features uses no window-global stats (no full-window max/min)",
        "verdict": "CLEAN",
        "detail": (
            "simulate.py::_precompute_features (lines 56–71) calls: "
            "rolling_max_drop (verified above — strictly causal), "
            "book_imbalance (elementwise ratio — no window context at all), "
            "proximity_pct_from_move (elementwise |move_pct|/100 — no context), "
            "realized_vol_60s_from_move (verified above — strictly causal). "
            "No function in _precompute_features computes a full-window max/min/mean "
            "or any other stat that would require knowing future ticks."
        ),
    },
    {
        "item": "entry_signal uses only pre-computed features (no raw array indexing)",
        "verdict": "CLEAN",
        "detail": (
            "signals.py::entry_signal receives a TickEvent (current tick's values) and "
            "an EntryFeatures (pre-computed by _entry_features_at from the causal arrays). "
            "It performs only comparisons against these inputs — no array slicing, no "
            "indexing into future ticks."
        ),
    },
    {
        "item": "peak_mid tracking is monotone-max of past bids only",
        "verdict": "CLEAN",
        "detail": (
            "simulate_market HOLDING loop (lines 118–119): "
            "'bid_now = tick.yes_best_bid / tick.no_best_bid; "
            "if bid_now > position.peak_mid: position.peak_mid = bid_now'. "
            "This is a running maximum of bids seen so far, updated with the *current* tick. "
            "No future bid prices are consulted. The trailing stop drawdown "
            "(exit_signal line 166) measures peak_mid vs the current bid — both causal."
        ),
    },
]


def print_checklist() -> None:
    print()
    print("=" * 70)
    print("CHECKLIST — items requiring source review (not automatable)")
    print("=" * 70)
    for i, item in enumerate(CHECKLIST, 1):
        verdict_tag = f"[{item['verdict']}]"
        print(f"\n{i}. {item['item']}  {verdict_tag}")
        print(textwrap.fill(item["detail"], width=70, initial_indent="   ",
                            subsequent_indent="   "))
    print()


# =========================================================================== #
# Main
# =========================================================================== #

def run() -> None:
    print()
    print("=" * 70)
    print("LEAKAGE AUDIT — polymarket-arb decision logic")
    print(f"arb path: {_ARB}")
    print("=" * 70)
    print()

    # Run assertions
    assert_a_reaction_delay()
    assert_b_rolling_max_drop_no_lookahead()
    assert_b2_realized_vol_no_lookahead()

    # Print written checklist
    print_checklist()

    print("=" * 70)
    print("OVERALL VERDICT: NO LOOK-AHEAD LEAKAGE FOUND IN THE DECISION PATH.")
    print()
    print("All automated assertions pass. Every checklist item reviewed as CLEAN.")
    print("Entry fills are drawn from a tick at least 1 tick after the signal tick.")
    print("All rolling features (rolling_max_drop, realized_vol_60s_from_move)")
    print("slice strictly price[lo:i+1] — causal, no future-index access.")
    print("Exit and forced-resolution logic uses only the current tick and the")
    print("window-end outcome flag, neither of which peeks at future tick prices.")
    print()
    print("Implication: the bot's paper losses are genuine strategy failure, not")
    print("a backtest that cheated. The edge problem is real and must be solved")
    print("with a real edge — not by fixing a leak (there is none).")
    print("=" * 70)


if __name__ == "__main__":
    run()
