"""Stage 5 — NSGA-II multi-objective Pareto search.

Optimizes (sharpe, n_trades, –max_drawdown) simultaneously to expose the
frequency / quality / drawdown trade-off frontier.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import optuna

from scripts.sweep_v2 import _runner, _optuna_bridge, evaluate, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


def make_multi_objective(pool_args, seed=42):
    def objective(trial):
        cfg = _optuna_bridge.trial_to_config(trial)
        from scripts.sweep_v2.evaluate import _WORKER_CTX
        if _WORKER_CTX is None:
            evaluate.worker_init(
                pool_args["symbols"],
                pool_args["date_start"],
                pool_args["date_end"],
                pool_args["fold_mask_dict"],
                pool_args["feature_cache_path"],
            )
        res = evaluate.worker_eval_kfold(cfg, seed=seed)
        trial.set_user_attr("config", cfg)
        trial.set_user_attr("result", res)
        pooled = res.get("pooled", {})
        n_trades = pooled.get("n_trades", 0)
        if n_trades < 30:
            return -1e9, 0, -1e9
        sharpe = float(res.get("cross_fold_sharpe", 0.0))
        # NSGA wants to maximize all three; minimize drawdown = maximize -dd
        # pooled max_dd is already negative (worst drawdown), so use as-is.
        return sharpe, float(n_trades), float(pooled.get("max_dd", -1e9))
    return objective


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-trials", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=43)
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage5_nsga.jsonl"))
    parser.add_argument("--storage", default=str(META_DIR / "optuna_nsga.db"))
    args = parser.parse_args()

    META_DIR.mkdir(parents=True, exist_ok=True)
    sampler = optuna.samplers.NSGAIISampler(seed=args.seed, population_size=100)
    study = optuna.create_study(
        study_name="sweep_v2_nsga",
        storage=f"sqlite:///{args.storage}",
        sampler=sampler,
        directions=["maximize", "maximize", "maximize"],
        load_if_exists=True,
    )

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)
    study.optimize(
        make_multi_objective(pool_args, seed=args.seed),
        n_trials=args.n_trials,
        n_jobs=1,
        show_progress_bar=False,
    )

    rows = []
    for t in study.trials:
        if t.state != optuna.trial.TrialState.COMPLETE:
            continue
        cfg = t.user_attrs.get("config")
        res = t.user_attrs.get("result")
        if cfg is None or res is None:
            continue
        rows.append({
            "config_id": param_space.hash_id(cfg),
            "config": cfg,
            "result": res,
            "trial_number": t.number,
        })
    _runner.write_jsonl(rows, Path(args.out))
    print(f"  Stage 5: wrote {len(rows)} NSGA-II rows → {args.out}")
    print(f"  Stage 5: Pareto front size = {len(study.best_trials)}")


if __name__ == "__main__":
    main()
