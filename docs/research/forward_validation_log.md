# Forward-validation log — determinism late-window pickoff (det_lwd_v1 rule)

Rule: 15m, last 60s, |spot−strike|≥5 bps (favourite agrees w/ spot), fav taker
ask ≤0.90, $10, hold to resolution. Fresh `cb_spot`, true-outcome settle, latency 2s.
Append a new block after each `uv run python -m research.build_joined &&
uv run python -m research.forward_validate`. Dev = selection; OOS = unseen.

## 2026-05-29 — clean window 05-23 .. 05-29 (dev 23-27 / OOS 28-29)

| date | split | trades | WR | $/trade | cum $ |
|---|---|---|---|---|---|
| 05-23 | dev | 71 | 0.845 | +1.16 | +82 |
| 05-24 | dev | 20 | 0.950 | +1.64 | +115 |
| 05-25 | dev | 43 | 0.814 | −0.10 | +111 |
| 05-26 | dev | 58 | 0.879 | +1.27 | +184 |
| 05-27 | dev | 54 | 0.981 | +2.80 | +336 |
| 05-28 | OOS | 69 | 0.899 | +1.62 | +447 |
| 05-29 | OOS | 18 | 0.944 | +1.90 | +482 |

**OOS summary:** n=87, WR 0.908, +$1.68/trade, 90% CI [+0.97, +2.39]. 6/7 days green.
Next: append 05-30+ as the bot collects them.
