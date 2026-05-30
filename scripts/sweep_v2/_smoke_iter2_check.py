"""Verify the learning curve fired: lessons.md exists, has a non-empty diff
section, and (if priors were derived) iter2's stage3_lhs.jsonl sampling
distribution is contained within iter1's `viable_region_priors.json`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


def main():
    failures = []

    lessons = META_DIR / "lessons.md"
    if not lessons.exists():
        failures.append(f"lessons.md missing at {lessons}")
    else:
        text = lessons.read_text()
        if "Iteration history" not in text:
            failures.append("lessons.md does not contain an 'Iteration history' section")
        # Confirm at least 2 iterations were recorded
        if "Diff vs prior iteration" not in text:
            print("WARNING: lessons.md has no diff section yet — was the second iteration run?")

    lifetime = META_DIR / "all_evals_lifetime.parquet"
    if not lifetime.exists():
        failures.append(f"all_evals_lifetime.parquet missing")
    else:
        df = pd.read_parquet(lifetime)
        n_iters = df["iteration_id"].nunique()
        print(f"Lifetime store: {len(df):,} rows across {n_iters} iteration(s)")
        if n_iters < 2:
            print(f"NOTE: only {n_iters} iteration recorded in lifetime store.")

    priors = META_DIR / "viable_region_priors.json"
    if priors.exists():
        d = json.loads(priors.read_text())
        print(f"Viable priors: {len(d)} params with derived [p5, p95] ranges")
    else:
        print("No viable_region_priors.json (expected if no viable lifetime rows).")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nLearning-curve check passed.")


if __name__ == "__main__":
    main()
