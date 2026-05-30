"""Stage 16b — Generate `lessons.md` human-readable diary diffing this
iteration against prior."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    META_DIR.mkdir(parents=True, exist_ok=True)
    lessons_path = META_DIR / "lessons.md"
    lifetime_path = META_DIR / "all_evals_lifetime.parquet"

    lines = [f"# sweep_v2 Lessons — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", ""]

    if not lifetime_path.exists():
        lines.append("No lifetime store yet — first iteration.")
        lessons_path.write_text("\n".join(lines))
        return

    df = pd.read_parquet(lifetime_path)
    iterations = sorted(df["iteration_id"].unique().tolist())
    lines.append(f"- Lifetime evaluations: **{len(df):,}** across **{len(iterations)}** iterations.")

    # Iteration breakdown
    lines.append("")
    lines.append("## Iteration history")
    lines.append("")
    lines.append("| iteration_id | n_rows | n_viable (pnl>0,trades≥30) | viable_pct | top_sharpe |")
    lines.append("|--------------|-------:|---------------------------:|-----------:|-----------:|")
    for it in iterations:
        sub = df[df["iteration_id"] == it]
        viable = sub[(sub["pooled_net_pnl"] > 0) & (sub["min_fold_n_trades"] >= 30)]
        pct = (100.0 * len(viable) / max(1, len(sub)))
        top_sharpe = sub["cross_fold_sharpe"].max() if len(sub) > 0 else float("nan")
        lines.append(f"| {it} | {len(sub):,} | {len(viable):,} | {pct:.1f}% | {top_sharpe:.3f} |")
    lines.append("")

    # Diff between last two iterations
    if len(iterations) >= 2:
        prev, last = iterations[-2], iterations[-1]
        sub_prev = df[df["iteration_id"] == prev]
        sub_last = df[df["iteration_id"] == last]

        viable_prev = sub_prev[(sub_prev["pooled_net_pnl"] > 0) & (sub_prev["min_fold_n_trades"] >= 30)]
        viable_last = sub_last[(sub_last["pooled_net_pnl"] > 0) & (sub_last["min_fold_n_trades"] >= 30)]

        prev_pct = 100.0 * len(viable_prev) / max(1, len(sub_prev))
        last_pct = 100.0 * len(viable_last) / max(1, len(sub_last))
        lines.append("## Diff vs prior iteration")
        lines.append("")
        lines.append(f"- viable_pct: prior={prev_pct:.1f}% → now={last_pct:.1f}% ({last_pct-prev_pct:+.1f} pp)")
        lines.append(f"- top sharpe: prior={sub_prev['cross_fold_sharpe'].max():.3f} → now={sub_last['cross_fold_sharpe'].max():.3f}")
        lines.append("")

        # Failure mode breakdown
        if "failure_class" in df.columns:
            prev_fail = sub_prev["failure_class"].value_counts(normalize=True) * 100
            last_fail = sub_last["failure_class"].value_counts(normalize=True) * 100
            lines.append("### Failure modes (% of iteration)")
            lines.append("")
            lines.append("| class | prior | now |")
            lines.append("|-------|------:|----:|")
            classes = set(prev_fail.index) | set(last_fail.index)
            for c in sorted(classes):
                p = prev_fail.get(c, 0.0)
                n = last_fail.get(c, 0.0)
                lines.append(f"| {c} | {p:.1f}% | {n:.1f}% |")
            lines.append("")

    # Viable region priors summary
    priors_path = META_DIR / "viable_region_priors.json"
    if priors_path.exists():
        priors = json.loads(priors_path.read_text())
        lines.append("## Current viable-region priors (used by next iteration's LHS)")
        lines.append("")
        lines.append("| param | p5 | median | p95 | n_support |")
        lines.append("|-------|---:|-------:|----:|----------:|")
        for name, stat in sorted(priors.items()):
            lines.append(
                f"| `{name}` | {stat.get('p5', 0):.3g} | {stat.get('median', 0):.3g} "
                f"| {stat.get('p95', 0):.3g} | {stat.get('n_support', 0)} |"
            )
        lines.append("")

    # Graveyard
    graveyard = META_DIR / "feature_graveyard.md"
    if graveyard.exists():
        lines.append("## Feature graveyard")
        lines.append("")
        lines.append(graveyard.read_text())
        lines.append("")

    lessons_path.write_text("\n".join(lines))
    print(f"  Stage 16: lessons.md updated → {lessons_path}")


if __name__ == "__main__":
    main()
