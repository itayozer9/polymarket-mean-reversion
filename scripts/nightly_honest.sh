#!/bin/zsh
# Nightly honest scoreboard (Edge Hunt v2, 2026-07-02).
# Fetches yesterday's official on-chain outcomes (15m + 5m), re-settles every paper
# ledger on them, and regenerates data/research/paper_official/scoreboard.md.
# Official labels == real-money resolution (parity-pinned), so the scoreboard is a
# true out-of-sample stream. Exits nonzero on fetch/resettle failure so a wrapper
# (cron mail / status skill) can surface it.
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"  # launchd's PATH lacks uv

SINCE=$(date -u -v-3d +%Y-%m-%d 2>/dev/null || date -u -d '3 days ago' +%Y-%m-%d)

uv run python -m research.dataset.official_outcomes --timeframes 15m,5m --since "$SINCE"
uv run python -m research.analysis.resettle_official
uv run python -m research.analysis.reconcile_executor
uv run pytest tests/research/test_resettle_official.py -q --no-header 2>&1 | tail -1

# Full suite — WARN-ONLY, and deliberately LAST. Nothing else ran it on a schedule, so
# it sat RED for 8 days: the 2026-08-08 det_lwd_live retirement broke 3 modules that each
# hard-coded the armed set, and it surfaced 08-16 only because a status check happened to
# run pytest. A red suite hides real regressions; that is the cost, not the failing asserts.
# Never allowed to abort the nightly (labels are the money-critical output; tests are the
# canary), hence the pipe — `set -e` sees tail's status, not pytest's.
# nice'd: this is ~10min and CPU-heavy, and on 2026-08-03 exactly this workload starved the
# live executor's 2 Hz poll loop until two real intents aged out and were dropped.
# The sweep_v2 ignore is a known environment gap (lightgbm absent) — it fails COLLECTION,
# which would otherwise make this warn fire every night and train everyone to ignore it.
# Drop the ignore if lightgbm is ever installed.
SUITE=$(nice -n 19 uv run pytest -q --ignore=tests/sweep_v2/test_surrogate.py 2>&1 | tail -1)
echo "[nightly_honest] suite: $SUITE"
# ${SUITE:l} lowercases (zsh): pytest prints "ERROR" for a COLLECTION failure, which
# `*error*` would miss case-sensitively — and a collection error is the loudest thing
# there is (a test module that cannot even import). Silently calling that green is the
# failure this check exists to prevent, so match case-insensitively and include "no tests".
case "${SUITE:l}" in
  *failed*|*error*|*"no tests ran"*)
    echo "[warn] TEST SUITE RED — $SUITE"
    echo "[warn]   reproduce: nice -n 19 uv run pytest -q --ignore=tests/sweep_v2/test_surrogate.py" ;;
esac

echo "[nightly_honest] OK $(date -u +%Y-%m-%dT%H:%MZ)"
