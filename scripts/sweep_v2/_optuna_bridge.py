"""Optuna ↔ param_space bridge — turns an Optuna trial into a sweep_v2 config dict."""
from __future__ import annotations

from typing import Any, Dict

from scripts.sweep_v2 import param_space


def trial_to_config(trial) -> Dict[str, Any]:
    """Build a sweep_v2 config dict by drawing each param from `trial.suggest_*`."""
    out: Dict[str, Any] = {}
    for name, kind, low, high, choices in param_space.PARAMS:
        if kind == "float":
            out[name] = trial.suggest_float(name, low, high)
        elif kind == "int":
            out[name] = trial.suggest_int(name, low, high)
        elif kind == "bool":
            out[name] = trial.suggest_categorical(name, [False, True])
        elif kind == "cat":
            # Optuna can't have None directly — substitute a sentinel string.
            cleaned = ["__NONE__" if c is None else c for c in choices]
            choice = trial.suggest_categorical(name, cleaned)
            out[name] = None if choice == "__NONE__" else choice
    return param_space._post_process(out)


def config_to_trial_dict(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Inverse — turn a sweep_v2 dict into Optuna-compatible param dict (for enqueue_trial)."""
    out: Dict[str, Any] = {}
    for name, kind, _, _, _ in param_space.PARAMS:
        v = cfg.get(name, None)
        if kind == "cat" and v is None:
            out[name] = "__NONE__"
        else:
            out[name] = v
    return out
