"""burst_cap — should a live disagree-family strategy cap simultaneous multi-coin fills?

Motivation: fav_disagree_live's first live day lost $10.56 in ONE simultaneous 3-coin
burst (window 1781113500: btc/eth/sol DOWN, all knife fills). One macro spot move
crosses several coins' strikes at once, so the "diversified" 4-coin book is really
one leveraged macro bet ([[sq-variance-macro-correlated]]).

BC1 — burst anatomy: per-window_start_ts firing groups from the family's decision
      frames (a BURST = >=2 coins firing the SAME strategy at the same
      window_start_ts); within-burst outcome agreement vs a permutation null;
      burst-vs-singleton EV under both fill models.
BC2 — intent-level cap policies applied to the decision frame BEFORE fill
      simulation (mirrors an Executor._blocked gate): uncapped vs max{1,2} per
      window-ts with keep-first-by-entry_sec / keep-cheapest-ask tie-breaks.
      Scored under v2 (idealized) + live_guarded (fills_live) at $5, future block
      06-05..09 (LC2 split override). Decision rule fixed in advance — see
      docs/research/test_ledger.md, "Burst-cap + capacity study (2026-06-11)".

NOTE on clustering: EV CIs here cluster by window_start_ts (the burst unit), NOT
by slug — the whole point is that same-ts fills are not independent.

Run: uv run python -m research.analysis.burst_cap [--stake 5]
Out: data/research/burst_cap/{anatomy.jsonl,policies.jsonl} + stdout tables
"""
from __future__ import annotations

import argparse
import itertools
import json
import os

import numpy as np
import pandas as pd

import research.analysis.hypothesis_sweep as hyp_sweep
import research.analysis.rejudge_live_model as rj
from research.analysis.edge_lab import load_base
from research.lib.stats import window_clustered_bootstrap
from research.sim.fills_live import DEFAULT_PARAMS_PATH, load_params

OUT_DIR = os.path.join("data", "research", "burst_cap")
FUTURE_START = "2026-06-05"   # LC2 split override (test_ledger Burst-cap study)

# The disagree family. fav_disagree = the rejudge CONFIGS entry (0.90 band);
# fav_disagree_live045 = the DEPLOYED live band (the incident config);
# early_disagree = the deployed early_disagree_live params (atlas family twin).
FAMILY = {
    "fav_disagree": dict(mode="disagree", t_lo=120, t_hi=360, dist_min=10.0,
                         ask_lo=0.05, ask_hi=0.90),
    "fav_disagree_live045": dict(mode="disagree", t_lo=120, t_hi=360, dist_min=10.0,
                                 ask_lo=0.05, ask_hi=0.45),
    "early_disagree": dict(mode="disagree", t_lo=450, t_hi=900, dist_min=10.0,
                           ask_lo=0.30, ask_hi=0.45),
}

POLICIES = ("uncapped", "max1_first", "max2_first", "max1_cheap", "max2_cheap")
SEEDS = (0, 1, 2, 3, 4)


# ---------------------------------------------------------------------------
# pure helpers (unit-tested in tests/research/test_burst_cap.py)
# ---------------------------------------------------------------------------
def policy_keep(dec: pd.DataFrame, policy: str) -> pd.DataFrame:
    """Apply an intent-level burst-cap policy to a decision frame.

    Policies: uncapped | max{1,2}_{first,cheap}. Per window_start_ts keep the
    first N rows under the tie-break ordering:
      first — earliest entry_sec (ties: lower entry_ask, then slug)
      cheap — lowest entry_ask  (ties: earlier entry_sec, then slug)
    Deterministic (stable mergesort + full tie-break key).
    """
    if policy == "uncapped" or dec.empty:
        return dec
    n = int(policy[3])          # max1_* / max2_*
    tie = policy.split("_")[1]
    keys = (["window_start_ts", "entry_sec", "entry_ask", "slug"] if tie == "first"
            else ["window_start_ts", "entry_ask", "entry_sec", "slug"])
    s = dec.sort_values(keys, kind="mergesort")
    return s.groupby("window_start_ts", group_keys=False).head(n)


def group_sizes(dec: pd.DataFrame) -> pd.Series:
    """window_start_ts -> number of coins firing (decision rows) at that ts."""
    return dec.groupby("window_start_ts")["slug"].count()


def pair_counts(won: np.ndarray, group_ids: np.ndarray) -> dict:
    """Within-group pairwise outcome counts over all unordered same-group pairs.
    Returns ww (both win), ll (both lose), dis (disagree), tot."""
    ww = ll = dis = tot = 0
    df = pd.DataFrame({"w": np.asarray(won, dtype=int), "g": np.asarray(group_ids)})
    for _, g in df.groupby("g"):
        w = g["w"].to_numpy()
        if len(w) < 2:
            continue
        for i, j in itertools.combinations(range(len(w)), 2):
            tot += 1
            if w[i] == w[j]:
                if w[i] == 1:
                    ww += 1
                else:
                    ll += 1
            else:
                dis += 1
    return dict(ww=ww, ll=ll, dis=dis, tot=tot)


def agreement_rate(won, group_ids) -> float:
    c = pair_counts(won, group_ids)
    return (c["ww"] + c["ll"]) / c["tot"] if c["tot"] else float("nan")


def conditional_winrates(counts: dict) -> dict:
    """P(win | a same-burst partner won/lost) from unordered pair counts."""
    ww, ll, dis = counts["ww"], counts["ll"], counts["dis"]
    p_w = (2 * ww) / (2 * ww + dis) if (2 * ww + dis) else float("nan")
    p_l = dis / (2 * ll + dis) if (2 * ll + dis) else float("nan")
    return dict(p_win_given_partner_won=p_w, p_win_given_partner_lost=p_l)


def perm_null_agreement(won, group_ids, n: int = 2000, seed: int = 0) -> dict:
    """Permutation null: shuffle outcomes across fills (group structure kept).
    Returns observed agreement, null mean, and one-sided p = P(null >= obs)."""
    won = np.asarray(won, dtype=int)
    group_ids = np.asarray(group_ids)
    obs = agreement_rate(won, group_ids)
    if not np.isfinite(obs):
        return dict(obs=float("nan"), null_mean=float("nan"), p=float("nan"), n=n)
    rng = np.random.default_rng(seed)
    null = np.empty(n)
    for b in range(n):
        null[b] = agreement_rate(rng.permutation(won), group_ids)
    p = float((np.sum(null >= obs - 1e-12) + 1) / (n + 1))
    return dict(obs=float(obs), null_mean=float(null.mean()), p=p, n=n)


def max_drawdown(pnl: np.ndarray) -> float:
    """Max peak-to-trough drawdown of the cumulative P&L sequence (>= 0)."""
    pnl = np.asarray(pnl, dtype="f8")
    if pnl.size == 0:
        return 0.0
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.maximum(cum, 0.0))  # start flat at 0
    return float(np.max(peak - cum))


# ---------------------------------------------------------------------------
# decision frames + simulation (rejudge_live_model pattern)
# ---------------------------------------------------------------------------
def family_decisions(b: pd.DataFrame) -> dict:
    for name, cfg in FAMILY.items():
        rj.CONFIGS.setdefault(name, cfg)
    return {name: rj.decisions_for(name, b) for name in FAMILY}


def _sim(dec, cfg, mode, params, stake, seed) -> pd.DataFrame:
    """simulate_config + window_start_ts/entry_sec merged back (for grouping)."""
    t = rj.simulate_config(dec, cfg, mode, params, stake, seed=seed)
    if t.empty:
        return t
    return t.merge(dec[["slug", "window_start_ts", "entry_sec", "entry_ask"]],
                   on="slug", how="left")


def _policy_metrics(t: pd.DataFrame, n_uncapped_sig: int) -> dict:
    """Metrics for one simulated policy run (one split slice)."""
    if t.empty:
        return dict(n_sig=0, n_fills=0)
    f = t[t["filled"] == 1].sort_values(["window_start_ts", "entry_sec"])
    out = dict(n_sig=int(len(t)), n_fills=int(len(f)))
    if len(f) == 0:
        return out
    grp = f.groupby("window_start_ts")["pnl"].sum()
    out.update(
        total=float(f["pnl"].sum()),
        ev_fill=float(f["pnl"].mean()),
        wr=float(f["won"].mean() * 100),
        ev_per_uncapped_signal=float(f["pnl"].sum() / max(n_uncapped_sig, 1)),
        worst_group=float(grp.min()),
        max_dd=max_drawdown(f["pnl"].to_numpy()),
    )
    if len(f) >= 3:
        lo, _, hi = window_clustered_bootstrap(
            f["pnl"].values, f["window_start_ts"].values, n=2000)
        out.update(ci_lo=float(lo), ci_hi=float(hi))
    return out


def anatomy(name: str, dec: pd.DataFrame, cfg: dict, params, stake: float) -> dict:
    """BC1 — burst frequency + joint-outcome stats for one config (uncapped)."""
    gs = group_sizes(dec)
    rec = dict(config=name, n_decisions=int(len(dec)), n_groups=int(len(gs)),
               pct_groups_burst=round(float((gs >= 2).mean() * 100), 1),
               pct_decisions_in_burst=round(
                   float(gs[gs >= 2].sum() / max(gs.sum(), 1) * 100), 1),
               size_hist={int(k): int(v) for k, v in
                          gs.value_counts().sort_index().items()})
    for model, seed in (("v2", 0), ("live_guarded", 0)):
        t = _sim(dec, cfg, model, params, stake, seed)
        f = t[t["filled"] == 1] if len(t) else t
        m = dict(n_fills=int(len(f)))
        if len(f) >= 3:
            fs = group_sizes(f)
            burst_ts = set(fs[fs >= 2].index)
            fb = f[f["window_start_ts"].isin(burst_ts)]
            fsg = f[~f["window_start_ts"].isin(burst_ts)]
            counts = pair_counts(fb["won"].to_numpy(), fb["window_start_ts"].to_numpy())
            m.update(
                n_burst_fills=int(len(fb)), n_singleton_fills=int(len(fsg)),
                pair_counts=counts, **conditional_winrates(counts),
                perm=perm_null_agreement(fb["won"].to_numpy(),
                                         fb["window_start_ts"].to_numpy()),
                ev_burst=float(fb["pnl"].mean()) if len(fb) else None,
                ev_singleton=float(fsg["pnl"].mean()) if len(fsg) else None,
                wr_burst=float(fb["won"].mean() * 100) if len(fb) else None,
                wr_singleton=float(fsg["won"].mean() * 100) if len(fsg) else None,
                worst_group=float(f.groupby("window_start_ts")["pnl"].sum().min()),
            )
            if len(fb) >= 3:
                lo, _, hi = window_clustered_bootstrap(
                    fb["pnl"].values, fb["window_start_ts"].values, n=2000)
                m["ev_burst_ci"] = [round(float(lo), 2), round(float(hi), 2)]
            if len(fsg) >= 3:
                lo, _, hi = window_clustered_bootstrap(
                    fsg["pnl"].values, fsg["window_start_ts"].values, n=2000)
                m["ev_singleton_ci"] = [round(float(lo), 2), round(float(hi), 2)]
        rec[model] = m
    return rec


def policy_table(name: str, dec: pd.DataFrame, cfg: dict, params,
                 stake: float) -> list[dict]:
    """BC2 — score every cap policy under both fill models, future + FULL."""
    out = []
    n_unc = {sp: int(len(dec[dec["split"] == sp])) for sp in ("dev", "holdout", "future")}
    n_unc["FULL"] = int(len(dec))
    for policy in POLICIES:
        d = policy_keep(dec, policy)
        rec = dict(config=name, policy=policy, n_attempts=int(len(d)))
        # v2 (deterministic)
        t = _sim(d, cfg, "v2", params, stake, seed=0)
        for sp in ("future", "FULL"):
            s = t if sp == "FULL" else t[t["split"] == sp]
            rec[f"v2_{sp}"] = _policy_metrics(s, n_unc[sp])
        # live_guarded: seed-0 detail + seeds 0-4 spread
        per_seed = {sp: dict(total=[], ev=[], worst=[], dd=[]) for sp in ("future", "FULL")}
        for seed in SEEDS:
            t = _sim(d, cfg, "live_guarded", params, stake, seed=seed)
            for sp in ("future", "FULL"):
                s = t if sp == "FULL" else t[t["split"] == sp]
                m = _policy_metrics(s, n_unc[sp])
                if seed == 0:
                    rec[f"lg_{sp}"] = m
                if m.get("n_fills", 0) > 0:
                    per_seed[sp]["total"].append(m["total"])
                    per_seed[sp]["ev"].append(m["ev_fill"])
                    per_seed[sp]["worst"].append(m["worst_group"])
                    per_seed[sp]["dd"].append(m["max_dd"])
        for sp in ("future", "FULL"):
            ps = per_seed[sp]
            if ps["total"]:
                rec[f"lg_{sp}_seeds"] = {
                    k: dict(mean=float(np.mean(v)), sd=float(np.std(v)))
                    for k, v in ps.items() if v}
        out.append(rec)
    return out


def decide(policies: list[dict]) -> dict:
    """Apply the pre-registered BC2 decision rule to one config's policy table."""
    unc = next(p for p in policies if p["policy"] == "uncapped")
    u_lg = unc.get("lg_future_seeds", {})
    u_v2 = unc.get("v2_future", {})
    if not u_lg or "worst" not in u_lg or not u_v2.get("n_fills"):
        return dict(recommend=None, reason="insufficient future fills to judge")
    u_worst, u_total = u_lg["worst"]["mean"], u_lg["total"]["mean"]
    u_worst_v2, u_total_v2 = u_v2["worst_group"], u_v2["total"]
    qual, ev_grounds = [], []
    for p in policies:
        if p["policy"] == "uncapped":
            continue
        lg, v2 = p.get("lg_future_seeds", {}), p.get("v2_future", {})
        if not lg or "worst" not in lg or not v2.get("n_fills"):
            continue
        # improvement = how much LESS negative the worst group gets vs uncapped
        worst_cut = (lg["worst"]["mean"] - u_worst) / abs(u_worst) if u_worst < 0 else 1.0
        keep_frac = lg["total"]["mean"] / u_total if u_total > 0 else float("inf")
        v2_worst_ok = v2["worst_group"] >= u_worst_v2 - 1e-9
        v2_keep_ok = (v2["total"] >= 0.80 * u_total_v2) if u_total_v2 > 0 \
            else (v2["total"] >= u_total_v2)
        leg1 = worst_cut >= 0.25
        leg2 = (keep_frac >= 0.80) if u_total > 0 else (lg["total"]["mean"] >= u_total)
        if leg1 and leg2 and v2_worst_ok and v2_keep_ok:
            qual.append((p["policy"], lg["total"]["mean"], lg["worst"]["mean"]))
        if lg["total"]["mean"] > u_total and v2["total"] > u_total_v2:
            ev_grounds.append((p["policy"], lg["total"]["mean"]))
    if qual:
        best = sorted(qual, key=lambda x: (-x[1], x[2]))[0]
        return dict(recommend=best[0], grounds="risk_rule",
                    detail=dict(qualifiers=[q[0] for q in qual]))
    if ev_grounds:
        best = max(ev_grounds, key=lambda x: x[1])
        return dict(recommend=best[0], grounds="ev_grounds",
                    detail=dict(ev_qualifiers=[q[0] for q in ev_grounds]))
    return dict(recommend=None, grounds="no_policy_qualified",
                reason="honest negative — keep uncapped")


def _fmt_policy_line(p: dict) -> str:
    v2, lg = p.get("v2_future", {}), p.get("lg_future", {})
    sd = p.get("lg_future_seeds", {})
    def g(d, k, fmt="+.2f"):
        v = d.get(k)
        return format(v, fmt) if isinstance(v, (int, float)) else "-"
    lg_tot = (f"{sd['total']['mean']:+.1f}±{sd['total']['sd']:.0f}"
              if sd.get("total") else "-")
    lg_worst = (f"{sd['worst']['mean']:+.1f}" if sd.get("worst") else "-")
    lg_dd = (f"{sd['dd']['mean']:.1f}" if sd.get("dd") else "-")
    return (f"  {p['policy']:11s} att={p['n_attempts']:4d} | v2 fut: n={v2.get('n_fills','-'):>3} "
            f"tot={g(v2,'total','+.1f'):>7} ev={g(v2,'ev_fill'):>6} "
            f"worst={g(v2,'worst_group','+.1f'):>6} dd={g(v2,'max_dd','.1f'):>5} | "
            f"lg fut(seeds): n={lg.get('n_fills','-'):>3} tot={lg_tot:>12} "
            f"ev={g(lg,'ev_fill'):>6}[{g(lg,'ci_lo')},{g(lg,'ci_hi')}] "
            f"worst={lg_worst:>6} dd={lg_dd:>5}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stake", type=float, default=5.0)
    args = ap.parse_args()

    hyp_sweep.set_future_override(FUTURE_START)
    b = hyp_sweep.apply_future_override(load_base())
    params = load_params(DEFAULT_PARAMS_PATH)
    print(f"burst_cap: future={FUTURE_START}.., stake ${args.stake:.0f}, "
          f"fill model {params.version} (kappa={params.kappa}, "
          f"{params.n_attempts} attempts)\n")

    decs = family_decisions(b)
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---- BC1 anatomy ----
    print("=== BC1 — burst anatomy (uncapped, FULL window) ===")
    anat = []
    for name, dec in decs.items():
        rec = anatomy(name, dec, FAMILY[name], params, args.stake)
        anat.append(rec)
        v2 = rec["v2"]
        print(f"\n{name}: {rec['n_decisions']} decisions / {rec['n_groups']} window-ts "
              f"groups; bursts = {rec['pct_groups_burst']}% of groups, "
              f"{rec['pct_decisions_in_burst']}% of decisions; sizes {rec['size_hist']}")
        if v2.get("pair_counts"):
            pc, pm = v2["pair_counts"], v2["perm"]
            print(f"  v2 fills {v2['n_fills']} ({v2['n_burst_fills']} in bursts): "
                  f"pair agreement {pm['obs']*100:.0f}% vs null {pm['null_mean']*100:.0f}% "
                  f"(p={pm['p']:.4f}; ww={pc['ww']} ll={pc['ll']} dis={pc['dis']})")
            print(f"  P(win|partner won)={v2['p_win_given_partner_won']*100:.0f}% vs "
                  f"P(win|partner lost)={v2['p_win_given_partner_lost']*100:.0f}%  |  "
                  f"EV burst ${v2['ev_burst']:+.2f} {v2.get('ev_burst_ci','')} vs "
                  f"singleton ${v2['ev_singleton']:+.2f} {v2.get('ev_singleton_ci','')}  "
                  f"WR {v2['wr_burst']:.0f}%/{v2['wr_singleton']:.0f}%  "
                  f"worst group ${v2['worst_group']:+.2f}")
        lg = rec["live_guarded"]
        if lg.get("pair_counts"):
            pm = lg["perm"]
            print(f"  lg(seed0) fills {lg['n_fills']} ({lg['n_burst_fills']} burst): "
                  f"agreement {pm['obs']*100:.0f}% vs null {pm['null_mean']*100:.0f}% "
                  f"(p={pm['p']:.4f}); EV burst ${lg['ev_burst']:+.2f} vs singleton "
                  f"${lg['ev_singleton']:+.2f}; worst group ${lg['worst_group']:+.2f}")
    with open(os.path.join(OUT_DIR, "anatomy.jsonl"), "w") as fh:
        for r in anat:
            fh.write(json.dumps(r) + "\n")

    # cross-strategy same-window overlap (descriptive)
    f045 = set(decs["fav_disagree_live045"]["window_start_ts"])
    fearly = set(decs["early_disagree"]["window_start_ts"])
    both = f045 & fearly
    print(f"\ncross-strategy: fav_disagree_live045 x early_disagree share "
          f"{len(both)} window-ts ({len(f045)} vs {len(fearly)} fired) — same "
          f"settlement event, different entry times")

    # ---- BC2 policies ----
    print("\n=== BC2 — cap policies (future = 06-05..09, $%.0f) ===" % args.stake)
    all_pol = []
    for name, dec in decs.items():
        print(f"\n{name}:")
        pols = policy_table(name, dec, FAMILY[name], params, args.stake)
        for p in pols:
            print(_fmt_policy_line(p))
        verdict = decide(pols)
        print(f"  -> pre-registered rule: {verdict}")
        for p in pols:
            p["verdict_for_config"] = verdict
        all_pol.extend(pols)
    with open(os.path.join(OUT_DIR, "policies.jsonl"), "w") as fh:
        for r in all_pol:
            fh.write(json.dumps(r) + "\n")
    print(f"\n-> {OUT_DIR}/anatomy.jsonl + policies.jsonl")


if __name__ == "__main__":
    main()
