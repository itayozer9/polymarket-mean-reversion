"""Verify stress-test perturbation helpers produce well-formed configs."""
from __future__ import annotations

import numpy as np
import pytest

from scripts.sweep_v2 import param_space, stress


def _baseline():
    rng = np.random.default_rng(0)
    return param_space.random_sample(rng)


def test_perturb_numeric_clips_to_bounds():
    cfg = _baseline()
    cfg["entry.drop_magnitude_pct"] = 49.0  # near upper bound (50)
    perturbed = stress.perturb_numeric(cfg, "entry.drop_magnitude_pct", 0.10)
    # 49 * 1.10 = 53.9 — must be clipped to 50.0
    assert perturbed["entry.drop_magnitude_pct"] <= 50.0


def test_joint_perturbation_returns_valid_config():
    cfg = _baseline()
    rng = np.random.default_rng(7)
    perturbed = stress.joint_perturbation(cfg, rng, radius=0.20)
    # All numeric params remain within their declared bounds
    for name, kind, lo, hi, _ in param_space.PARAMS:
        if kind not in ("float", "int"):
            continue
        v = perturbed.get(name)
        if v is None:
            continue
        assert lo - 1e-6 <= v <= hi + 1e-6, f"{name}={v} outside [{lo}, {hi}]"


def test_adversarial_cfg_increases_costs():
    cfg = _baseline()
    cfg["fill.reject_prob"] = 0.03
    adv = stress.adversarial_cfg(cfg)
    assert adv["fill.fee_rate"] == 0.08
    assert adv["fill.reject_prob"] >= 0.06
