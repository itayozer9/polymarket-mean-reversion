"""The ARMED SET — which strategies may risk real money — as a test tripwire.

Several paper-mode test modules assert that adding a new engine mode did not change
what is armed. That assertion is deliberately a HARD-CODED expectation, not something
derived from strategies.yaml: derived, it would be a tautology and would silently bless
an accidental `live: true`. Hard-coded, an unintended arming turns the suite red.

It lives here, once, because it used to be copy-pasted into three modules: the
2026-08-08 retirement of `det_lwd_live` (a deliberate user decision) therefore broke
three tests at once, and the suite sat red for 8 days until the 08-16 status check —
long enough to have hidden a real regression. One deliberate arming change should be
one deliberate edit, here.

Keep in sync with PORTFOLIO.md section 5 (the armed-set table) and `strategies.yaml`.

History: `fav_disagree_hi_live` disarmed 2026-08-07 (adverse selection above ask 0.45);
`det_lwd_live` retired 2026-08-08; `det_d12_dual_live` and `det_d12_wide_live` killed /
benched 2026-06-18 and 06-09; `early_disagree_live` demoted 2026-06-18.
"""
from __future__ import annotations

# Strategies with `live: true` in strategies.yaml — i.e. permitted to place real orders.
ARMED_LIVE_IDS = {"fav_disagree_live"}
