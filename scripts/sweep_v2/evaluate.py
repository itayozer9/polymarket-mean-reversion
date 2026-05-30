"""sweep_v2 evaluate — wraps polymarket-arb simulate_market with per-fold +
new-feature-aware filtering.

Single API:
    EvalContext.load_markets(symbols, date_start, date_end) once at startup.
    EvalContext.eval(cfg_dict) → per-fold + pooled metrics on the current fold mask.

Workers in a ProcessPoolExecutor each construct their own EvalContext via the
initializer, reading the data once per worker.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mean_reversion_live.adapters import arb_imports  # noqa: E402,F401

from scripts.mean_reversion import loaders as _arb_loaders  # noqa: E402

COMBINED_DIR = ROOT / "data" / "sweep_v2" / "combined"


def _ensure_loaders_pointed_at_sweep_v2():
    """Mutate loaders.DATA_DIR + OUTCOMES_FILE to our combined dir.

    This is called explicitly by sweep_v2 entrypoints — NOT at module import —
    so that tests that import this module (e.g. via pytest collection of
    tests/sweep_v2/) don't break the parity test by changing loaders state
    in test contexts that depend on the default relative path.
    """
    _arb_loaders.DATA_DIR = str(COMBINED_DIR)
    _arb_loaders.OUTCOMES_FILE = str(COMBINED_DIR / "outcomes.csv")


from mean_reversion_live.adapters.arb_imports import (  # noqa: E402
    Portfolio,
    SimConfig,
    iter_markets,
    load_outcomes,
)
from scripts.mean_reversion.simulate import simulate_market  # noqa: E402

from scripts.sweep_v2 import param_space  # noqa: E402
from scripts.sweep_v2 import features as feat  # noqa: E402

WINDOW_SEC_15M = 900


@dataclass
class FoldMask:
    """Set of slugs assigned to each of N folds (typically 5)."""
    n_folds: int
    slug_to_fold: Dict[str, int]  # slug → fold index (0..n_folds-1)

    def fold_slugs(self, fold_idx: int) -> set:
        return {s for s, f in self.slug_to_fold.items() if f == fold_idx}


@dataclass
class EvalContext:
    symbols: List[str]
    markets: Dict[str, List[Tuple[str, np.ndarray]]]  # symbol → list[(slug, arr)]
    outcomes: Dict[str, Tuple[str, float]]
    feature_lookup: Optional[feat.FeatureLookup]
    fold_mask: Optional[FoldMask] = None

    @classmethod
    def build(
        cls,
        symbols: List[str],
        date_start: str,
        date_end: str,
        feature_lookup: Optional[feat.FeatureLookup] = None,
    ) -> "EvalContext":
        _ensure_loaders_pointed_at_sweep_v2()
        markets: Dict[str, List[Tuple[str, np.ndarray]]] = {}
        for sym in symbols:
            mlist = list(iter_markets("15m", sym, date_start, date_end))
            markets[sym] = mlist
        outcomes = load_outcomes()
        return cls(
            symbols=symbols,
            markets=markets,
            outcomes=outcomes,
            feature_lookup=feature_lookup,
        )

    def all_slugs(self) -> List[Tuple[str, str]]:
        out = []
        for sym, mlist in self.markets.items():
            for slug, _arr in mlist:
                out.append((sym, slug))
        return out

    def n_markets(self) -> int:
        return sum(len(v) for v in self.markets.values())


# ---------------------------------------------------------------------------
# Re-entry loop (copied from scripts/backtest_sweep.py — multi-trade per window)
# ---------------------------------------------------------------------------

def _run_market_reentry(
    slug: str,
    arr: np.ndarray,
    cfg: SimConfig,
    portfolio: Portfolio,
    rng: np.random.Generator,
    outcome,
    max_iterations: int = 20,
):
    start_idx = 0
    iterations = 0
    while start_idx < len(arr) - 30 and iterations < max_iterations:
        slice_arr = arr[start_idx:]
        if len(slice_arr) < 30:
            break
        n_before = len(portfolio.trades)
        simulate_market(slug, slice_arr, cfg, WINDOW_SEC_15M, portfolio, rng, outcome)
        n_after = len(portfolio.trades)
        if n_after == n_before:
            break
        last_trade = portfolio.trades[-1]
        offsets = np.where(arr["timestamp_ms"][start_idx:] > last_trade.exit_ts_ms)[0]
        if len(offsets) == 0:
            break
        new_start = start_idx + int(offsets[0])
        if new_start <= start_idx:
            break
        start_idx = new_start
        iterations += 1


# ---------------------------------------------------------------------------
# Single-config evaluation across a slug-set (one fold) or all markets.
# ---------------------------------------------------------------------------

def _summarise(trades: list) -> Dict[str, Any]:
    if not trades:
        return {
            "n_trades": 0, "n_wins": 0, "win_rate": 0.0,
            "net_pnl": 0.0, "avg_pnl": 0.0, "pnl_std": 0.0, "max_dd": 0.0,
        }
    pnls = np.array([t.pnl for t in trades], dtype="f8")
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = float(np.min(cum - peak))
    return {
        "n_trades": len(pnls),
        "n_wins": int((pnls > 0).sum()),
        "win_rate": float((pnls > 0).mean()),
        "net_pnl": float(pnls.sum()),
        "avg_pnl": float(pnls.mean()),
        "pnl_std": float(pnls.std(ddof=0)) if len(pnls) > 1 else 0.0,
        "max_dd": dd,  # negative number (worst drawdown from peak)
    }


def eval_on_slugs(
    ctx: EvalContext,
    cfg_dict: Dict[str, Any],
    slug_filter: Optional[set],
    seed: int = 42,
    return_trades: bool = False,
) -> Dict[str, Any]:
    """Run one config across markets whose slug is in slug_filter (or all if None).
    Applies polymarket-arb simulator THEN post-hoc filter_v2 trade filtering.
    """
    sim_cfg = param_space.to_sim_config(cfg_dict)
    v2 = param_space.extract_v2_filter(cfg_dict)

    per_symbol: Dict[str, Dict[str, Any]] = {}
    all_trades = []
    for sym in ctx.symbols:
        rng = np.random.default_rng(seed)
        portfolio = Portfolio(human=sim_cfg.human, bankroll=1000.0)
        for slug, arr in ctx.markets[sym]:
            if slug_filter is not None and slug not in slug_filter:
                continue
            outcome = ctx.outcomes.get(slug)
            _run_market_reentry(slug, arr, sim_cfg, portfolio, rng, outcome)

        # Post-hoc filter_v2 application
        sym_trades = list(portfolio.trades)
        if any(v2.get(f"filter_v2.use_{k}") for k in (
            "macro_stress", "rv_regime", "depth_imbalance", "btc_lead",
            "spread_zscore", "expiry_bucket",
        )) and ctx.feature_lookup is not None:
            sym_trades = feat.filter_trades(sym_trades, sym, v2, ctx.feature_lookup)

        all_trades.extend(sym_trades)
        per_symbol[sym] = _summarise(sym_trades)
        if return_trades:
            per_symbol[sym]["trades"] = sym_trades

    out: Dict[str, Any] = {
        "per_symbol": {s: {k: v for k, v in d.items() if k != "trades"}
                       for s, d in per_symbol.items()},
        "aggregate": _summarise(all_trades),
    }
    if return_trades:
        out["all_trades"] = all_trades
    return out


def eval_kfold(
    ctx: EvalContext,
    cfg_dict: Dict[str, Any],
    seed: int = 42,
) -> Dict[str, Any]:
    """Run all K folds and return per-fold metrics + cross-fold Sharpe + pooled OOF."""
    assert ctx.fold_mask is not None, "EvalContext.fold_mask must be set for k-fold."
    K = ctx.fold_mask.n_folds
    per_fold = []
    pooled_trades: list = []
    for k in range(K):
        slug_set = ctx.fold_mask.fold_slugs(k)
        # In the "test on this fold" framing: each fold IS the held-out set; we
        # evaluate the strategy ON the held-out windows. The "training" doesn't
        # happen here — search optimizers consume the cross-fold mean as their
        # signal. So all evaluations are out-of-fold by construction.
        res = eval_on_slugs(ctx, cfg_dict, slug_set, seed=seed, return_trades=True)
        per_fold.append({
            **res["aggregate"],
            "fold": k,
            "n_slugs": len(slug_set),
        })
        pooled_trades.extend(res["all_trades"])

    pnls = np.array([f["net_pnl"] for f in per_fold], dtype="f8")
    n_trades_per_fold = np.array([f["n_trades"] for f in per_fold], dtype="i8")
    avg_pnls_per_fold = np.array([f["avg_pnl"] for f in per_fold], dtype="f8")
    cross_fold_sharpe = (
        float(avg_pnls_per_fold.mean() / avg_pnls_per_fold.std(ddof=0))
        if avg_pnls_per_fold.std(ddof=0) > 0 else 0.0
    )
    pooled = _summarise(pooled_trades)
    return {
        "per_fold": per_fold,
        "pooled": pooled,
        "cross_fold_pnl_mean": float(pnls.mean()),
        "cross_fold_pnl_std": float(pnls.std(ddof=0)) if len(pnls) > 1 else 0.0,
        "cross_fold_sharpe": cross_fold_sharpe,
        "min_fold_n_trades": int(n_trades_per_fold.min()),
        "folds_positive": int((pnls > 0).sum()),
        "pooled_trades": pooled_trades,  # caller may discard
    }


# ---------------------------------------------------------------------------
# Multiprocess: per-worker context (workers re-init data once each).
# ---------------------------------------------------------------------------

_WORKER_CTX: Optional[EvalContext] = None


def worker_init(symbols, date_start, date_end, fold_mask_dict, feature_cache_path):
    global _WORKER_CTX
    fl = feat.FeatureLookup.from_parquet(Path(feature_cache_path)) if feature_cache_path else None
    ctx = EvalContext.build(symbols, date_start, date_end, feature_lookup=fl)
    if fold_mask_dict:
        ctx.fold_mask = FoldMask(
            n_folds=fold_mask_dict["n_folds"],
            slug_to_fold={s: int(f) for s, f in fold_mask_dict["slug_to_fold"].items()},
        )
    _WORKER_CTX = ctx


def worker_eval_kfold(cfg_dict, seed=42):
    out = eval_kfold(_WORKER_CTX, cfg_dict, seed=seed)
    out.pop("pooled_trades", None)
    return out


def worker_eval_slugs(cfg_dict, slug_list, seed=42):
    """Run on a specific slug list (used by stress / walk-forward / replay).
    Strips trades to keep return payload small."""
    slug_set = set(slug_list) if slug_list else None
    out = eval_on_slugs(_WORKER_CTX, cfg_dict, slug_set, seed=seed, return_trades=False)
    return out
