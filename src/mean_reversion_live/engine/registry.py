"""Load strategies.yaml → list of StrategyHandle."""
from __future__ import annotations
from pathlib import Path
from typing import List

import yaml
import structlog

from mean_reversion_live.adapters.arb_imports import (
    EntryParams, ExitParams, FilterParams, FillParams, HumanParams, SimConfig,
)
import json

from mean_reversion_live.engine import trail_v2_runtime
from mean_reversion_live.engine.determinism_state import DetParams
from mean_reversion_live.engine.stale_quote_state import StaleQuoteParams
from mean_reversion_live.engine.strategy import StrategyHandle

log = structlog.get_logger(__name__)


def _build_sim_config(d: dict) -> SimConfig:
    return SimConfig(
        entry=EntryParams(**d["entry"]),
        exit=ExitParams(**d["exit"]),
        filter=FilterParams(**d["filter"]),
        human=HumanParams(**d["human"]),
        fill=FillParams(**d["fill"]),
    )


def load_strategies(yaml_path: Path, data_dir: Path) -> List[StrategyHandle]:
    """Parse strategies.yaml. Skips entries with enabled=false.

    Strategies may carry an optional `trail_v2_staircase` field — a list of
    [activation_pct, lock_pct] pairs. When present, trail_v2_runtime patches
    the engine's exit_signal so the trailing logic only kicks in after the
    activation threshold and tightens through each step.
    """
    raw = yaml.safe_load(open(yaml_path))
    if not isinstance(raw, list):
        raise ValueError(f"strategies.yaml must be a list, got {type(raw).__name__}")
    out: List[StrategyHandle] = []
    needs_trail_v2 = False
    for entry in raw:
        if not entry.get("enabled", True):
            log.info("strategy_disabled", id=entry.get("id"))
            continue
        sid = entry["id"]
        cfg = _build_sim_config(entry["sim_config"])
        tf = entry.get("timeframe", "15m")
        sc = float(entry.get("starting_capital_usd", 1000.0))
        # Determinism strategy (Phase 1 edge): uses DeterminismState, not the
        # mean-reversion machine. The sim_config still supplies human caps + fee.
        det_params = None
        if entry.get("type") == "determinism":
            dd = entry.get("determinism", {})
            det_params = DetParams(
                t_min_sec=int(dd.get("t_min_sec", 1)),
                t_max_sec=int(dd.get("t_max_sec", 60)),
                dist_min_bps=float(dd.get("dist_min_bps", 5.0)),
                max_ask=float(dd.get("max_ask", 0.90)),
                min_ask=float(dd.get("min_ask", 0.50)),
                fixed_bet_usd=float(cfg.human.fixed_bet_usd),
                fee_rate=float(cfg.fill.fee_rate),
                max_daily_loss_usd=(float(dd["max_daily_loss_usd"])
                                    if dd.get("max_daily_loss_usd") is not None else None),
            )
        # Stale-quote (Phase 2) strategy: loads a frozen empirical P(Up|z) curve.
        sq_params = None
        if entry.get("type") == "stale_quote":
            sq = entry.get("stale_quote", {})
            cf = sq.get("curve_file", "data/research/stale_quote_curve.json")
            curve = json.load(open(cf))
            sq_params = StaleQuoteParams(
                zc=[float(x) for x in curve["z"]],
                p_up=[float(x) for x in curve["p_up"]],
                margin=float(sq.get("margin", 0.08)),
                max_mispricing=float(sq.get("max_mispricing", 0.30)),
                jump_bps=float(sq.get("jump_bps", 8.0)),
                t_lo_left=int(sq.get("t_lo_left", 60)),
                t_hi_left=int(sq.get("t_hi_left", 840)),
                min_ask=float(sq.get("min_ask", 0.05)),
                max_ask=float(sq.get("max_ask", 0.95)),
                fixed_bet_usd=float(cfg.human.fixed_bet_usd),
                fee_rate=float(cfg.fill.fee_rate),
                max_daily_loss_usd=(float(sq["max_daily_loss_usd"])
                                    if sq.get("max_daily_loss_usd") is not None else None),
            )
        staircase = entry.get("trail_v2_staircase")
        if staircase:
            needs_trail_v2 = True
            trail_v2_runtime.register(cfg.exit, [tuple(pair) for pair in staircase])
        out.append(StrategyHandle(
            id=sid,
            name=entry.get("name", sid),
            cfg=cfg,
            timeframe=tf,
            starting_capital_usd=sc,
            data_dir=data_dir,
            det_params=det_params,
            sq_params=sq_params,
        ))
    if needs_trail_v2:
        trail_v2_runtime.install_patch()
    return out
