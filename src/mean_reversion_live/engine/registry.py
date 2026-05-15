"""Load strategies.yaml → list of StrategyHandle."""
from __future__ import annotations
from pathlib import Path
from typing import List

import yaml
import structlog

from mean_reversion_live.adapters.arb_imports import (
    EntryParams, ExitParams, FilterParams, FillParams, HumanParams, SimConfig,
)
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
    """Parse strategies.yaml. Skips entries with enabled=false."""
    raw = yaml.safe_load(open(yaml_path))
    if not isinstance(raw, list):
        raise ValueError(f"strategies.yaml must be a list, got {type(raw).__name__}")
    out: List[StrategyHandle] = []
    for entry in raw:
        if not entry.get("enabled", True):
            log.info("strategy_disabled", id=entry.get("id"))
            continue
        sid = entry["id"]
        cfg = _build_sim_config(entry["sim_config"])
        tf = entry.get("timeframe", "15m")
        sc = float(entry.get("starting_capital_usd", 1000.0))
        out.append(StrategyHandle(
            id=sid,
            name=entry.get("name", sid),
            cfg=cfg,
            timeframe=tf,
            starting_capital_usd=sc,
            data_dir=data_dir,
        ))
    return out
