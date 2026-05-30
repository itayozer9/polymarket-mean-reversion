"""Verify the 5-fold split is bit-stable across runs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.sweep_v2 import folds


ROOT = Path(__file__).resolve().parent.parent.parent


def test_folds_balanced_and_reproducible(tmp_path):
    outcomes = ROOT / "data" / "outcomes.csv"
    if not outcomes.exists():
        pytest.skip("data/outcomes.csv not present in this checkout")

    p1 = tmp_path / "folds_a.json"
    p2 = tmp_path / "folds_b.json"
    pa = folds.write_folds(outcomes, ["btc", "eth", "sol", "xrp"],
                            "2026-05-15", "2026-05-23", n_folds=5, seed=20260523, out_path=p1)
    pb = folds.write_folds(outcomes, ["btc", "eth", "sol", "xrp"],
                            "2026-05-15", "2026-05-23", n_folds=5, seed=20260523, out_path=p2)

    # Reproducible: identical slug→fold mapping with the same seed
    assert pa["slug_to_fold"] == pb["slug_to_fold"]

    # Folds approximately balanced (each within 10% of the mean)
    sizes = list(pa["fold_sizes"].values())
    mean = sum(sizes) / len(sizes)
    for s in sizes:
        assert 0.85 * mean <= s <= 1.15 * mean, f"fold size {s} is far from mean {mean}"


def test_different_seed_yields_different_assignment(tmp_path):
    outcomes = ROOT / "data" / "outcomes.csv"
    if not outcomes.exists():
        pytest.skip("data/outcomes.csv not present in this checkout")

    p1 = tmp_path / "folds_seed1.json"
    p2 = tmp_path / "folds_seed2.json"
    pa = folds.write_folds(outcomes, ["btc", "eth", "sol", "xrp"],
                            "2026-05-15", "2026-05-23", n_folds=5, seed=1, out_path=p1)
    pb = folds.write_folds(outcomes, ["btc", "eth", "sol", "xrp"],
                            "2026-05-15", "2026-05-23", n_folds=5, seed=2, out_path=p2)

    # At least some assignments should differ
    diffs = sum(1 for s in pa["slug_to_fold"] if pa["slug_to_fold"][s] != pb["slug_to_fold"].get(s))
    assert diffs > 0, "Changing seed should change some fold assignments"
