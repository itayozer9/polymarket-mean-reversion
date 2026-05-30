"""Sweep_v2 parameter space.

Defines the full tunable space (base SimConfig params from polymarket-arb +
new conditioning features) and provides encode/decode/sample helpers used by
every search stage (LHS, TPE, NSGA, CMA-ES, GA, surrogate).

Single source of truth — every optimizer reads from PARAM_SPACE so a change
here is consistent everywhere.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# Lazy import: SimConfig comes from polymarket-arb via the live adapter. We
# don't import here directly because some downstream consumers (e.g. the
# orchestrator's argparse) want to load this module without the heavy arb path.
def _arb():
    from mean_reversion_live.adapters.arb_imports import (
        EntryParams, ExitParams, FilterParams, HumanParams, FillParams, SimConfig,
    )
    return EntryParams, ExitParams, FilterParams, HumanParams, FillParams, SimConfig


# ---------------------------------------------------------------------------
# Parameter definitions
#
# Each entry: (path, type, low, high, choices_or_None)
#  - type: "float", "int", "cat", "bool"
#  - "cat" entries use `choices` as the categorical set.
#  - "bool" entries take {False, True}.
#  - "float" / "int" use [low, high].
# ---------------------------------------------------------------------------

PARAMS: List[Tuple[str, str, Any, Any, Optional[List[Any]]]] = [
    # Entry
    ("entry.side", "cat", None, None, ["UP", "DOWN", "BOTH"]),
    ("entry.entry_price_min", "float", 0.02, 0.45, None),
    ("entry.entry_price_max", "float", 0.10, 0.95, None),
    ("entry.drop_magnitude_pct", "float", 3.0, 50.0, None),
    ("entry.drop_window_sec", "int", 20, 300, None),
    ("entry.min_time_left_sec", "int", 60, 720, None),
    ("entry.proximity_max_pct", "float", 0.1, 100.0, None),
    ("entry.min_seconds_into_window", "int", 0, 720, None),
    # Exit
    ("exit.profit_target_pct", "float", 5.0, 300.0, None),
    ("exit.stop_loss_pct", "cat", None, None, [None, 25.0, 40.0, 55.0, 70.0, 85.0]),
    ("exit.max_hold_sec", "int", 60, 900, None),
    ("exit.trailing_stop_pct", "cat", None, None, [None, 10.0, 20.0, 30.0, 40.0]),
    # Filter
    ("filter.min_book_depth_usd", "float", 5.0, 100.0, None),
    ("filter.max_spread", "float", 0.005, 0.20, None),
    ("filter.book_imbalance_min", "cat", None, None, [None, 1.2, 1.5, 2.0, 3.0]),
    ("filter.vol_regime", "cat", None, None, ["LOW", "MED", "HIGH", "ALL"]),
    ("filter.time_of_day", "cat", None, None, ["ALL", "US", "EU", "ASIA", "OVERNIGHT"]),
    # Human / fill
    ("human.signal_skip_prob", "float", 0.0, 0.20, None),
    ("human.daily_trade_cap", "cat", None, None, [None, 5, 10, 20, 50]),
    ("human.concurrent_position_cap", "cat", None, None, [None, 1, 2, 4, 8]),
    ("fill.fee_rate", "float", 0.07, 0.07, None),  # locked
    ("fill.reject_prob", "float", 0.02, 0.10, None),
    # ── New sweep_v2 conditioning features (filter_v2.*). Applied post-hoc
    #    via features.filter_trades after simulate_market produces trades.
    ("filter_v2.use_macro_stress", "bool", None, None, None),
    ("filter_v2.macro_stress_min_symbols", "int", 1, 4, None),
    ("filter_v2.use_rv_regime", "bool", None, None, None),
    ("filter_v2.rv_regime", "cat", None, None, ["LOW", "MED", "HIGH"]),
    ("filter_v2.use_depth_imbalance", "bool", None, None, None),
    ("filter_v2.depth_imbalance_min", "float", 0.40, 0.80, None),
    ("filter_v2.use_btc_lead", "bool", None, None, None),
    ("filter_v2.btc_lead_pct_min", "float", 0.05, 1.0, None),
    ("filter_v2.use_spread_zscore", "bool", None, None, None),
    ("filter_v2.spread_zscore_max", "float", -2.0, 1.0, None),
    ("filter_v2.use_expiry_bucket", "bool", None, None, None),
    ("filter_v2.expiry_bucket", "cat", None, None, ["EARLY", "MID", "LATE"]),
]

PARAM_NAMES = [p[0] for p in PARAMS]
PARAM_INDEX = {n: i for i, n in enumerate(PARAM_NAMES)}


def random_sample(rng: np.random.Generator) -> Dict[str, Any]:
    """Uniform-random sample over the full space (used as fallback / GA mutation)."""
    out: Dict[str, Any] = {}
    for name, kind, low, high, choices in PARAMS:
        if kind == "float":
            out[name] = float(rng.uniform(low, high))
        elif kind == "int":
            out[name] = int(rng.integers(low, high + 1))
        elif kind == "bool":
            out[name] = bool(rng.integers(0, 2))
        elif kind == "cat":
            out[name] = choices[int(rng.integers(0, len(choices)))]
    return _post_process(out)


def lhs_samples(
    n: int,
    seed: int = 0,
    priors: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Latin Hypercube samples. If `priors` is given, narrow float/int ranges
    to the [p5, p95] of the prior empirical distribution for that param."""
    from scipy.stats import qmc

    sampler = qmc.LatinHypercube(d=len(PARAMS), seed=seed)
    raw = sampler.random(n=n)  # n × d in [0, 1]
    out_list = []
    for row in raw:
        cfg: Dict[str, Any] = {}
        for u, (name, kind, low, high, choices) in zip(row, PARAMS):
            lo, hi = low, high
            if priors and name in priors and kind in ("float", "int"):
                lo = max(lo, priors[name].get("p5", lo))
                hi = min(hi, priors[name].get("p95", hi))
                if hi <= lo:
                    lo, hi = low, high
            if kind == "float":
                cfg[name] = float(lo + u * (hi - lo))
            elif kind == "int":
                cfg[name] = int(round(lo + u * (hi - lo)))
            elif kind == "bool":
                cfg[name] = bool(u >= 0.5)
            elif kind == "cat":
                idx = int(min(len(choices) - 1, int(u * len(choices))))
                cfg[name] = choices[idx]
        out_list.append(_post_process(cfg))
    return out_list


def _post_process(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Enforce constraints that can't be encoded per-axis."""
    # entry_price_min <= entry_price_max
    if cfg["entry.entry_price_min"] >= cfg["entry.entry_price_max"]:
        cfg["entry.entry_price_min"], cfg["entry.entry_price_max"] = (
            min(cfg["entry.entry_price_min"], cfg["entry.entry_price_max"]) * 0.5,
            max(cfg["entry.entry_price_min"], cfg["entry.entry_price_max"]),
        )
        cfg["entry.entry_price_min"] = max(0.02, cfg["entry.entry_price_min"])
    return cfg


def to_sim_config(cfg_dict: Dict[str, Any]):
    """Build a SimConfig (polymarket-arb dataclass) from a flat sweep_v2 dict.
    filter_v2.* entries are stripped — they're applied post-hoc, not by simulate_market.
    """
    EntryParams, ExitParams, FilterParams, HumanParams, FillParams, SimConfig = _arb()

    def g(name, default=None):
        return cfg_dict.get(name, default)

    entry = EntryParams(
        side=g("entry.side", "BOTH"),
        entry_price_min=g("entry.entry_price_min", 0.10),
        entry_price_max=g("entry.entry_price_max", 0.30),
        drop_magnitude_pct=g("entry.drop_magnitude_pct", 20.0),
        drop_window_sec=g("entry.drop_window_sec", 60),
        min_time_left_sec=g("entry.min_time_left_sec", 300),
        proximity_max_pct=g("entry.proximity_max_pct", 100.0),
        min_seconds_into_window=g("entry.min_seconds_into_window", 30),
    )
    exit_ = ExitParams(
        profit_target_pct=g("exit.profit_target_pct", 25.0),
        stop_loss_pct=g("exit.stop_loss_pct", None),
        max_hold_sec=g("exit.max_hold_sec", 300),
        trailing_stop_pct=g("exit.trailing_stop_pct", None),
    )
    filt = FilterParams(
        min_book_depth_usd=g("filter.min_book_depth_usd", 20.0),
        max_spread=g("filter.max_spread", 0.05),
        book_imbalance_min=g("filter.book_imbalance_min", None),
        vol_regime=g("filter.vol_regime", "ALL"),
        time_of_day=g("filter.time_of_day", "ALL"),
        multi_tier_entry=1,
        correlated_signal_filter=False,
    )
    human = HumanParams(
        reaction_delay_min_sec=0.5,
        reaction_delay_max_sec=2.0,
        signal_skip_prob=g("human.signal_skip_prob", 0.0),
        daily_trade_cap=g("human.daily_trade_cap", None),
        post_loss_cooldown_sec=0,
        concurrent_position_cap=g("human.concurrent_position_cap", None),
        fixed_bet_usd=10.0,
    )
    fill = FillParams(
        fee_rate=g("fill.fee_rate", 0.07),
        reject_prob=g("fill.reject_prob", 0.03),
        use_next_tick_for_fill=True,
        realistic_fill_model=True,
    )
    return SimConfig(entry=entry, exit=exit_, filter=filt, human=human, fill=fill)


def extract_v2_filter(cfg_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Return only the filter_v2.* keys (used by post-hoc trade filtering)."""
    return {k: v for k, v in cfg_dict.items() if k.startswith("filter_v2.")}


def hash_id(cfg_dict: Dict[str, Any]) -> str:
    """Deterministic short hash including filter_v2.*."""
    import hashlib

    blob = json.dumps(cfg_dict, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def flatten_for_dataframe(cfg_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize a config dict for pandas / parquet.

    All values are cast to string to keep each column's dtype stable (mixed
    None + float would break the parquet schema). `unflatten_from_dataframe`
    inverts the cast.
    """
    out: Dict[str, Any] = {}
    for name, kind, _, _, _ in PARAMS:
        v = cfg_dict.get(name, None)
        if v is None:
            out[name] = "__NONE__"
        elif kind == "bool":
            out[name] = "True" if v else "False"
        else:
            out[name] = str(v)
    return out


def unflatten_from_dataframe(row: Dict[str, Any]) -> Dict[str, Any]:
    """Inverse of flatten_for_dataframe — casts strings back to typed values."""
    out: Dict[str, Any] = {}
    for name, kind, low, high, choices in PARAMS:
        v = row.get(name, None)
        if v is None or v == "__NONE__":
            out[name] = None
            continue
        if kind == "float":
            out[name] = float(v)
        elif kind == "int":
            out[name] = int(float(v))
        elif kind == "bool":
            out[name] = (str(v).lower() == "true")
        elif kind == "cat":
            # match the original choice's type by trying numeric first
            for c in choices:
                if c is None:
                    continue
                if str(c) == str(v):
                    out[name] = c
                    break
            else:
                out[name] = v
    return out
