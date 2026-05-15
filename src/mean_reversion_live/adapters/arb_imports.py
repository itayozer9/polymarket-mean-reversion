"""Single import surface for polymarket-arb's mean_reversion package.

We don't copy code. Instead this module:
1. Injects polymarket-arb into sys.path
2. Re-exports the symbols we use from signals.py, simulate.py, config.py, portfolio.py

If polymarket-arb renames or changes public signatures, this is the ONE place
that needs updating — everything else in the live engine imports from here.
"""
from __future__ import annotations
import sys
from pathlib import Path

from mean_reversion_live.config import get_settings


def _inject_polymarket_arb_path() -> None:
    arb = Path(get_settings().polymarket_arb_path).resolve()
    if not arb.exists():
        raise RuntimeError(
            f"polymarket-arb path not found: {arb}\n"
            f"Expected POLYMARKET_ARB_PATH (or .env polymarket_arb_path) to point at "
            f"the polymarket-arb checkout."
        )
    signals_py = arb / "scripts" / "mean_reversion" / "signals.py"
    if not signals_py.exists():
        raise RuntimeError(
            f"polymarket-arb path exists ({arb}) but is missing the expected "
            f"signals.py at {signals_py}. Check that this is the polymarket-arb "
            f"repo root and that scripts/mean_reversion/ hasn't been moved."
        )
    if str(arb) not in sys.path:
        sys.path.insert(0, str(arb))


_inject_polymarket_arb_path()

# Re-exports from polymarket-arb. Use these everywhere in the live engine.
from scripts.mean_reversion.signals import (  # noqa: E402
    TickEvent,
    EntryFeatures,
    Position,
    entry_signal,
    exit_signal,
    _side_price,
    _side_bid,
    _side_mid,
    _side_ask_depth,
)
from scripts.mean_reversion.simulate import (  # noqa: E402
    calc_fee,
    _precompute_features,
    _entry_features_at,
    _tick_event,
)
from scripts.mean_reversion.config import (  # noqa: E402
    SimConfig,
    EntryParams,
    ExitParams,
    FilterParams,
    HumanParams,
    FillParams,
)
from scripts.mean_reversion.portfolio import (  # noqa: E402
    Portfolio,
    Trade,
)
from scripts.mean_reversion.loaders import (  # noqa: E402
    iter_markets,
    load_outcomes,
    window_duration_sec,
    TICK_DTYPE,
)
from scripts.mean_reversion import features as features_mod  # noqa: E402


__all__ = [
    "TickEvent", "EntryFeatures", "Position",
    "entry_signal", "exit_signal",
    "_side_price", "_side_bid", "_side_mid", "_side_ask_depth",
    "calc_fee", "_precompute_features", "_entry_features_at", "_tick_event",
    "SimConfig", "EntryParams", "ExitParams", "FilterParams", "HumanParams", "FillParams",
    "Portfolio", "Trade",
    "iter_markets", "load_outcomes", "window_duration_sec", "TICK_DTYPE",
    "features_mod",
]
