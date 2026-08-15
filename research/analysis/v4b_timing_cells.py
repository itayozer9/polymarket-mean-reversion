"""V4b — Edge Hunt v4, the two FROZEN disagree-timing cells (sealed 2026-08-01).

Registered cells (prereg names them with 4 fields; the atlas encodes 5). The ask range
0.30-0.40 exists only on the CHEAP side, so `side=CHEAP` is implied, and `DOWN` is the
disagree flag the atlas writes as `D`. Both labels below were verified to exist verbatim
in the V3b artifact these cells were drawn from (`edge_atlas_v3/atlas_cells.parquet`).

  prereg `a0.35-0.40 | tl450-900 | cl2-5  | DOWN` -> CHEAP|a0.35-0.40|tl450-900|cl2-5|D
  prereg `a0.30-0.35 | tl450-900 | cl5-12 | DOWN` -> CHEAP|a0.30-0.35|tl450-900|cl5-12|D

Gate, as registered: pooled per cell over the WHOLE campaign window (2026-07-24..08-14,
no dev/holdout split - this leg is a forward test of frozen cells, not a selection),
official labels, guarded fills, **n >= 40 AND CI-lo > 0, BH within k=2**.

CI convention is the prereg's own stats line: slug-clustered bootstrap **95%**
[p2.5, p97.5] (the score_gates convention). NOTE this is deliberately stricter than
edge_atlas.clustered_ci, which returns a 5%/95% (90%) interval - that one is the V4a
instrument's internal convention and is not what V4b registered.

PASS => paper twin via the existing early-disagree engine mode + standard 14-day twin
gate. FAIL => the early-timing thread closes permanently (second and last look).

  PYTHONPATH=. nice -n 19 uv run python -m research.analysis.v4b_timing_cells
"""
from __future__ import annotations

import numpy as np

import research.analysis.edge_atlas as ea
from research.analysis.atlas_v4 import use_v4_frame, WIN_LO, WIN_HI

CELLS = (
    "CHEAP|a0.35-0.40|tl450-900|cl2-5|D",
    "CHEAP|a0.30-0.35|tl450-900|cl5-12|D",
)
N_MIN = 40
FDR_Q = 0.10


def _ci95(values, groups, n: int = 5000, seed: int = 0):
    """Slug-clustered bootstrap [p2.5, p97.5] — score_gates convention."""
    v = np.asarray(values, dtype="f8")
    g = np.asarray(groups)
    uniq = np.unique(g)
    idx = {k: np.where(g == k)[0] for k in uniq}
    rng = np.random.default_rng(seed)
    stats = np.empty(n, "f8")
    for b in range(n):
        rows = np.concatenate([idx[k] for k in rng.choice(uniq, size=len(uniq), replace=True)])
        stats[b] = v[rows].mean()
    return float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def main() -> None:
    use_v4_frame()
    t = ea.atlas_tick_frame()
    d = t["date"].astype(str)
    t = t[(d >= WIN_LO) & (d <= WIN_HI)].copy()
    t["split"] = "dev"                     # single pooled block; no selection here
    slip = ea._load_slip()
    obs = ea.build_obs(t, slip)
    obs["label"] = [ea.cell_label(c) for c in obs["cell"].to_numpy()]
    print(f"[v4b] window {WIN_LO}..{WIN_HI} | obs {len(obs):,} | slip +{slip:.4f}")
    print(f"[v4b] coins {sorted(t['symbol'].unique())}\n")

    rows = []
    for lab in CELLS:
        g = obs[obs["label"] == lab]
        if len(g) == 0:
            rows.append((lab, 0, np.nan, np.nan, np.nan, np.nan, np.nan))
            continue
        v = g["ret_net"].to_numpy("f8")
        lo, hi = _ci95(v, g["slug"].to_numpy())
        p_pos, _ = ea.bootstrap_p_one_sided(v)
        rows.append((lab, len(g), float(v.mean()), lo, hi, float(g["won"].mean()), p_pos))

    pv = np.array([r[6] for r in rows], dtype="f8")
    rej = ea.benjamini_hochberg(pv, FDR_Q)

    print(f"{'cell':38}{'n':>5}{'net/$1':>9}{'CI95':>22}{'WR':>6}{'p_pos':>8}{'BH':>5}  verdict")
    any_pass = False
    for (lab, n, net, lo, hi, wr, p), bh in zip(rows, rej):
        ok = bool(n >= N_MIN and lo > 0 and bh)
        any_pass |= ok
        ci = f"[{lo:+.3f},{hi:+.3f}]" if n else "-"
        print(f"{lab:38}{n:>5}{net:>+9.3f}{ci:>22}{wr*100 if n else float('nan'):>5.0f}%"
              f"{p:>8.4f}{str(bool(bh)):>5}  {'PASS' if ok else 'FAIL'}"
              f"{'' if n >= N_MIN else f' (n<{N_MIN})'}")
    print(f"\nV4b verdict: {'PASS' if any_pass else 'FAIL'} — "
          f"{'paper twin via early-disagree mode + 14d twin gate' if any_pass else 'early-timing thread CLOSES (second and last look)'}")


if __name__ == "__main__":
    main()
