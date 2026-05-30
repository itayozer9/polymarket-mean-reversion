"""Stage 15 — Markdown report + proposed_strategies_v3.yaml + near_misses.md."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml

from scripts.sweep_v2 import _runner, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
DOCS_DIR = ROOT / "docs" / "sweep_v2"


def fmt_pnl(v: float) -> str:
    sign = "+" if v >= 0 else "−"
    return f"{sign}${abs(v):.2f}"


def _gather_all_evals(stage_paths: List[Path]) -> List[Dict[str, Any]]:
    rows = []
    for p in stage_paths:
        if p.exists():
            rows.extend(_runner.read_jsonl(p))
    return rows


def build_report(
    survivors_path: Path,
    portfolio_path: Path,
    all_evals_paths: List[Path],
    out_md: Path,
    out_yaml: Path,
    out_near_misses: Path,
):
    survivors = _runner.read_jsonl(survivors_path) if survivors_path.exists() else []
    final = [s for s in survivors if s.get("march_replay", {}).get("pass")]
    portfolio = json.loads(portfolio_path.read_text()) if portfolio_path.exists() else {}
    all_evals = _gather_all_evals(all_evals_paths)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ── Main report ────────────────────────────────────────────────────────
    md = [
        "# sweep_v2 — Strategy Discovery Report",
        f"*Generated {ts}*",
        "",
        "## Headline",
        f"- Total evaluations across stages 3-8: **{len(all_evals):,}**",
        f"- Final survivors (Bonferroni-GOLD + stress + walk-forward + replay + March): **{len(final)}**",
        "",
    ]

    if not final:
        md.append("## Result: NULL")
        md.append("")
        md.append(
            "Zero configs cleared the strict bar end-to-end. This is the same direction "
            "as the prior 3,000-config sweep on the same data. Near-misses are listed "
            "in `near_misses.md` if available.",
        )
    else:
        md.append("## Final picks")
        md.append("")
        md.append("| # | config_id | sharpe | pooled_pnl | n_trades | pooled_p_value | folds_pass | march_pnl |")
        md.append("|---|-----------|-------:|-----------:|---------:|---------------:|-----------:|----------:|")
        for i, r in enumerate(final, 1):
            cid = r["config_id"]
            sharpe = r["result"].get("cross_fold_sharpe", 0.0)
            pooled_pnl = r.get("pooled_net_pnl", 0.0)
            n_trades = r.get("pooled_n_trades", 0)
            pv = r.get("wilcoxon_p", 1.0)
            fp = r.get("n_folds_pass", 0)
            mpnl = r.get("march_replay", {}).get("net_pnl", 0.0)
            md.append(
                f"| {i} | `{cid}` | {sharpe:.3f} | {fmt_pnl(pooled_pnl)} | {n_trades} "
                f"| {pv:.2e} | {fp}/5 | {fmt_pnl(mpnl)} |"
            )
        md.append("")

        md.append("## Per-pick detail")
        md.append("")
        for i, r in enumerate(final, 1):
            md.append(f"### {i}. `{r['config_id']}`")
            md.append("")
            md.append("**Config:**")
            md.append("```json")
            md.append(json.dumps(r["config"], indent=2, default=str))
            md.append("```")
            md.append("")
            if r.get("stress"):
                md.append("**Stress tests:**")
                for axis, val in r["stress"].items():
                    if axis == "all_pass":
                        continue
                    p = val.get("pass") if isinstance(val, dict) else val
                    md.append(f"- {axis}: pass={p}")
                md.append("")

    # Portfolio / SHAP
    if portfolio:
        md.append("## SHAP feature importance (top 15)")
        md.append("")
        for tf in portfolio.get("top_features", []):
            md.append(f"- {tf['name']}: {tf['importance']:.4f}")
        md.append("")
        if portfolio.get("clusters"):
            md.append("## Correlation clusters of final survivors")
            md.append("")
            clusters: Dict[int, List[str]] = {}
            for c in portfolio["clusters"]:
                clusters.setdefault(c["cluster"], []).append(c["config_id"])
            for cid, members in clusters.items():
                md.append(f"- Cluster {cid}: {', '.join(members)}")
            md.append("")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md))
    print(f"  Stage 15: wrote report → {out_md}")

    # ── YAML for proposed strategies ───────────────────────────────────────
    yaml_payload = {"strategies": []}
    for i, r in enumerate(final, 1):
        yaml_payload["strategies"].append({
            "id": f"sweep_v2_pick_{i:02d}",
            "config_id": r["config_id"],
            "enabled": False,  # user must explicitly enable
            "source": "sweep_v2",
            "stress_pass": r.get("stress_pass", False),
            "walk_forward_pass": r.get("walk_forward", {}).get("pass", False),
            "march_pass": r.get("march_replay", {}).get("pass", False),
            "config": r["config"],
        })
    out_yaml.write_text(yaml.safe_dump(yaml_payload, sort_keys=False))
    print(f"  Stage 15: wrote proposed strategies → {out_yaml}")

    # ── near_misses.md ─────────────────────────────────────────────────────
    # Top 50 by cross_fold_sharpe with min_fold_n_trades >= 30 from all evals
    eligible = [r for r in all_evals if r["result"].get("min_fold_n_trades", 0) >= 30]
    eligible.sort(key=lambda r: r["result"].get("cross_fold_sharpe", -1e9), reverse=True)
    near = eligible[:50]
    nm = [
        "# Near-misses",
        f"*Generated {ts}*",
        "",
        "Top 50 configs by cross-fold Sharpe with ≥30 min-fold trades. "
        "These configs looked promising in the search but did not survive "
        "the full Stage-9 → Stage-13 gauntlet. Worth a manual look for the next iteration.",
        "",
        "| Rank | config_id | sharpe | pooled_pnl | min_fold_trades |",
        "|------|-----------|-------:|-----------:|----------------:|",
    ]
    for i, r in enumerate(near, 1):
        cid = r["config_id"]
        sharpe = r["result"].get("cross_fold_sharpe", 0.0)
        pn = r["result"].get("pooled", {}).get("net_pnl", 0.0)
        mft = r["result"].get("min_fold_n_trades", 0)
        nm.append(f"| {i} | `{cid}` | {sharpe:.3f} | {fmt_pnl(pn)} | {mft} |")
    out_near_misses.write_text("\n".join(nm))
    print(f"  Stage 15: wrote near-misses → {out_near_misses}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--survivors", default=str(SWEEP_DIR / "stage13_replay_march.jsonl"))
    parser.add_argument("--portfolio", default=str(SWEEP_DIR / "stage14_portfolio.json"))
    parser.add_argument("--all-evals", nargs="+",
                        default=[str(SWEEP_DIR / f"stage{n}_{name}.jsonl") for n, name in [
                            (3, "lhs"), (4, "tpe"), (5, "nsga"),
                            (6, "cmaes"), (7, "ga"), (8, "surrogate"),
                        ]])
    parser.add_argument("--date-tag", default=datetime.utcnow().strftime("%Y-%m-%d"))
    args = parser.parse_args()

    out_md = DOCS_DIR / f"REPORT_{args.date_tag}.md"
    out_yaml = ROOT / "proposed_strategies_v3.yaml"
    out_near = DOCS_DIR / f"near_misses_{args.date_tag}.md"
    build_report(
        Path(args.survivors), Path(args.portfolio),
        [Path(p) for p in args.all_evals],
        out_md, out_yaml, out_near,
    )


if __name__ == "__main__":
    main()
