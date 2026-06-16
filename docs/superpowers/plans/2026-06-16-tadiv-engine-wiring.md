# ta_divergence Engine Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Deploy the campaign's one survivor — `ta_divergence` — as a live-engine PAPER twin so it collects forward data, then (follow-up) add a parity-faithful variant to A/B against it.

**Architecture:** Add a new `tadiv_approx` entry mode to `DeterminismState` following the EXACT separate-path pattern of `psettle`/`xb` (a `_mode_entry` method that returns early, leaving the `consistent`/`disagree` flow byte-identical so the load-bearing `tests/test_paper_engine_replay.py` stays green). The approximation reuses the rolling spot buffer already in the state (`RollingMove.vel_bps`) — NO new tick fields, NO new feature computation. The faithful variant (PART B, follow-up) adds an EMA-of-spot slope feature parity-pinned to `research/dataset/ta_features.py`.

**Tech Stack:** Python, the existing `mean_reversion_live` engine, pytest, uv. Research basis: `docs/research/TA_STRATEGIES_2026-06-16.md` (spec `ta_divergence_2585/2588`: buy the spot-move side, `t_lo=60 t_hi=300`, `ret_min` 3–5 bps, ask 0.30–0.55).

---

## Mapping research → engine (the key equivalence)

`fam_ta_divergence` buys the move-implied side when `abs(ta_ret_30s) >= ret_min`, `abs(ta_ema_slope) >= slope_min`, and `sign(ta_ema_slope) == sign(ta_ret_30s)`, in `time_left ∈ [t_lo, t_hi]`, on the bought side's ask in `[ask_lo, ask_hi]`.

In the engine, `move_pct = (spot - strike)/strike * 100` and strike is constant within a window, so:
- `RollingMove.vel_bps(30)` = `(move_now - move_30ago) * 100` = the 30s spot change in bps **relative to strike** ≈ `ta_ret_30s` (research uses bps relative to spot; they differ by <1% since strike≈spot — fine for a 3–10 bps threshold gate, NOT byte-identical → that's exactly what PART B fixes).
- `sign(vel_bps(30))` = the move-implied side (UP if > 0).
- `vel_bps(10)` agreeing in sign with `vel_bps(30)` is the engine proxy for "EMA slope agrees with the 30s return".

So `tadiv_approx` gate: `time_left ∈ [t_min_sec, t_max_sec]`, `vel_bps(30) >= tadiv_ret_min_bps` (signed → buy UP) or `<= -tadiv_ret_min_bps` (buy DOWN), `sign(vel_bps(10)) == sign(vel_bps(30))`, bought-side ask in `[min_ask, max_ask]`, depth ≥ bet.

---

## File Structure

- **Modify** `src/mean_reversion_live/engine/determinism_state.py` — add `tadiv_*` fields to `DetParams`, a `mode == "tadiv_approx"` dispatch (next to the psettle/xb dispatch), and a `_tadiv_entry()` method. Fail-fast config check in `__init__` (like psettle/xb).
- **Modify** `src/mean_reversion_live/engine/registry.py` — parse the `tadiv_*` params from YAML.
- **Test** `tests/test_tadiv_state.py` (CREATE) — unit tests for the new mode (gate fires/skips, side selection up & down, byte-identical no-op for other modes).
- **Modify** `strategies.yaml` — add paper twin `tadiv_approx_v1` (`live: false`).
- **Modify** `STATE.md` — dated entry.
- PART B (follow-up): `src/mean_reversion_live/engine/det_features.py` (EMA slope on `RollingMove`), a `tadiv` faithful mode, a parity test vs `ta_features`.

---

## PART A — Approximation mode (execute now)

### Task A1: `DetParams` fields + fail-fast check

**Files:** Modify `src/mean_reversion_live/engine/determinism_state.py`; Test `tests/test_tadiv_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tadiv_state.py
import pytest
from mean_reversion_live.engine.determinism_state import DetParams, DeterminismState


def test_tadiv_params_default_off():
    p = DetParams()
    assert p.mode == "consistent"
    assert getattr(p, "tadiv_ret_min_bps", "MISSING") is None


def test_tadiv_requires_ret_min():
    # mode=tadiv_approx with no ret_min must fail fast at construction
    p = DetParams(mode="tadiv_approx")
    with pytest.raises(Exception):
        DeterminismState("btc-x", p, window_duration_sec=900)
```

- [ ] **Step 2: Run to confirm fail**

Run: `uv run pytest tests/test_tadiv_state.py -v`
Expected: FAIL (`tadiv_ret_min_bps` missing / no exception raised).

- [ ] **Step 3: Add fields to `DetParams`** (after the `xb_*` block, before `class DeterminismState`):

```python
    # ── mode="tadiv_approx" (2026-06-16): TA-divergence APPROXIMATION twin, PAPER ──
    # Live twin of research fam_ta_divergence (docs/research/TA_STRATEGIES_2026-06-16.md,
    # spec ta_divergence_2585/2588). Buys the spot-move-implied side when the 30s spot
    # move >= tadiv_ret_min_bps and the 10s velocity agrees in sign (EMA-slope proxy),
    # in time_left [t_min_sec, t_max_sec], bought-side ask in [min_ask, max_ask].
    # Uses RollingMove.vel_bps only — NO new tick field. APPROXIMATION: vel_bps is bps
    # vs strike, research ta_ret_30s is bps vs spot (<1% diff); the parity-faithful
    # mode="tadiv" is a separate follow-up. All-None default keeps other modes
    # byte-identical.
    tadiv_ret_min_bps: Optional[float] = None     # |30s spot move| floor (bps), the ret_min
```

- [ ] **Step 4: Add the fail-fast check in `DeterminismState.__init__`** (next to the psettle/xb checks, ~line 263–279):

```python
        # mode="tadiv_approx": fail-fast config check (TA-divergence approximation twin).
        if params.mode == "tadiv_approx" and params.tadiv_ret_min_bps is None:
            raise ValueError("mode='tadiv_approx' requires tadiv_ret_min_bps")
```

- [ ] **Step 5: Run to confirm pass**

Run: `uv run pytest tests/test_tadiv_state.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/mean_reversion_live/engine/determinism_state.py tests/test_tadiv_state.py
git commit -m "feat(tadiv): DetParams fields + fail-fast for tadiv_approx mode"
```

### Task A2: `_tadiv_entry` dispatch + gate

**Files:** Modify `src/mean_reversion_live/engine/determinism_state.py`; Test `tests/test_tadiv_state.py`

- [ ] **Step 1: Write the failing tests** (append). These drive a `DeterminismState` through ticks; reuse the construction conventions of the existing `tests/test_determinism_state.py` (READ it first for the exact tick-row dict shape, `on_tick` signature, and how `move`/`seconds_into_window`/`time_left` are supplied — match it verbatim). The behavioral assertions to encode:
  - An UP spot move ≥ ret_min within the time band, with 10s velocity also up, on an ask inside the band, FIRES buying UP.
  - A DOWN spot move ≤ −ret_min FIRES buying DOWN.
  - A move below ret_min does NOT fire.
  - A move where `vel_bps(10)` sign disagrees with `vel_bps(30)` does NOT fire (slope-proxy gate).
  - A `mode="consistent"` state run through the same ticks behaves exactly as before (no `tadiv` interference) — i.e. the dispatch is a clean separate path.

Write each as a concrete test using the real tick-row shape from `test_determinism_state.py`.

- [ ] **Step 2: Run to confirm fail**

Run: `uv run pytest tests/test_tadiv_state.py -v`
Expected: FAIL (no `_tadiv_entry`; mode not dispatched).

- [ ] **Step 3: Add the dispatch** (next to psettle/xb, ~line 344):

```python
        # ── mode="tadiv_approx": TA-divergence approximation entry. Same separate-
        #    path pattern as psettle/xb — returns here, legacy flow byte-identical. ──
        if self.p.mode == "tadiv_approx":
            return self._tadiv_entry(row, d, ts, time_left, move, ymid, portfolio)
```

- [ ] **Step 4: Implement `_tadiv_entry`** (add after `_psettle_entry`/`_xb_entry`). Model its structure on `_psettle_entry`: read `sec`, compute the signal, apply the bought-side ask band + depth, build the same `ctx` dict shape (set `"strategy_kind": "tadiv_approx"`), set `self.pos`, `self.state="HOLDING"`, call `portfolio.on_entry` + guard, emit. Gate logic:

```python
    def _tadiv_entry(self, row, d: dict, ts: int, time_left: int, move: float,
                     ymid: float, portfolio: Portfolio) -> None:
        """mode="tadiv_approx" gate + entry — APPROXIMATION twin of research
        fam_ta_divergence. Buy the 30s-spot-move side when |vel_bps(30)| >=
        tadiv_ret_min_bps and vel_bps(10) agrees in sign (EMA-slope proxy).
        Reached only after the shared gates (time band, healthy book, can_enter,
        daily-loss guard)."""
        p = self.p
        sec = int(row["seconds_into_window"])
        v30 = self._roll.vel_bps(30)
        v10 = self._roll.vel_bps(10)
        # signal: the 30s move direction; require magnitude + 10s-velocity agreement
        if abs(v30) < float(p.tadiv_ret_min_bps):
            d["decision"] = "skipped_tadiv_small_move"; self._emit(d); return None
        if (v30 > 0) != (v10 > 0):
            d["decision"] = "skipped_tadiv_slope_disagree"; self._emit(d); return None
        buy_yes = v30 > 0
        side = "UP" if buy_yes else "DOWN"
        ask = float(row["yes_best_ask"]) if buy_yes else float(row["no_best_ask"])
        if not (p.min_ask <= ask <= p.max_ask):
            d["decision"] = "skipped_tadiv_ask_band"; self._emit(d); return None
        depth_shares = float(row["yes_ask_depth"]) if buy_yes else float(row["no_ask_depth"])
        if depth_shares * ask < p.fixed_bet_usd:
            d["decision"] = "skipped_no_fill"; self._emit(d); return None
        shares = p.fixed_bet_usd / ask
        hour, dow = utc_hour_dow(ts)
        ctx = {
            "strategy_kind": "tadiv_approx", "symbol": symbol_of(self.slug),
            "utc_hour": hour, "dow": dow, "entry_sec": sec, "time_left": time_left,
            "fav_side": side, "entry_ask": round(ask, 4), "yes_mid": round(ymid, 4),
            "spread_yes": round(float(row["spread_yes"]), 4),
            "ask_depth_usd": round(depth_shares * ask, 1),
            "spot_vel_10s_bps": round(v10, 2), "spot_vel_30s_bps": round(v30, 2),
            "rvol_60s_bps": round(self._roll.rvol_bps(60), 2),
        }
        self.pos = {"side": side, "entry": ask, "shares": shares,
                    "bet": p.fixed_bet_usd,
                    "fee_entry": _fee(shares, ask, p.fee_rate),
                    "ts": ts, "entry_sec": sec, "ctx": ctx}
        self.state = "HOLDING"
        portfolio.on_entry(ts)
        if self._guard is not None:
            self._guard.on_entry(ts, self.slug, self.pos["bet"] + self.pos["fee_entry"])
        d["decision"] = "fired"; d["side_signal"] = side
        d["features"] = {"vel30_bps": round(v30, 2), "vel10_bps": round(v10, 2),
                         "entry_ask": round(ask, 4), "time_left": time_left}
        self._emit(d)
        return None
```

Verify `symbol_of`, `_fee`, `utc_hour_dow`, `Portfolio` are already imported in the module (they are, used by `_psettle_entry`). If `_tadiv_entry` is reached only after the shared time-band gate, the `[t_min_sec, t_max_sec]` band is already enforced upstream — confirm by reading where `_psettle_entry` is dispatched relative to the shared gate; if the shared time gate is NOT upstream of the dispatch, add `if not (p.t_min_sec <= time_left <= p.t_max_sec): self._emit(d); return None` at the top of `_tadiv_entry`.

- [ ] **Step 5: Run to confirm pass**

Run: `uv run pytest tests/test_tadiv_state.py -v`
Expected: all passed.

- [ ] **Step 6: Run the load-bearing replay-parity test + determinism suite**

Run: `uv run pytest tests/test_paper_engine_replay.py tests/test_determinism_state.py -v`
Expected: all passed (the new mode must NOT change existing behavior).

- [ ] **Step 7: Commit**

```bash
git add src/mean_reversion_live/engine/determinism_state.py tests/test_tadiv_state.py
git commit -m "feat(tadiv): tadiv_approx entry path (separate, replay-parity preserved)"
```

### Task A3: registry parsing

**Files:** Modify `src/mean_reversion_live/engine/registry.py`; Test `tests/test_tadiv_state.py` (or the registry test file if one exists — check `tests/` for `test_registry*.py` and follow its pattern)

- [ ] **Step 1: Write the failing test** — a YAML/dict fragment with `mode: tadiv_approx`, `tadiv_ret_min_bps: 5`, `t_min_sec/t_max_sec/min_ask/max_ask` parses into a `DetParams` with those values. READ `registry.py` first to match how it maps YAML keys → `DetParams` (e.g. whether it passes `**params` or maps explicitly; psettle/xb keys are the template to copy).

- [ ] **Step 2: Run to confirm fail.**

- [ ] **Step 3: Add `tadiv_ret_min_bps` to the registry's param mapping** wherever the psettle/xb keys are mapped (copy that exact mechanism).

- [ ] **Step 4: Run to confirm pass.**

- [ ] **Step 5: Commit**

```bash
git add src/mean_reversion_live/engine/registry.py tests/test_tadiv_state.py
git commit -m "feat(tadiv): registry parses tadiv_approx params"
```

### Task A4: deploy paper twin (PRODUCTION — gated on user go)

**Files:** Modify `strategies.yaml`, `STATE.md`

> This task merges the branch to `main` and restarts the running paper bot. STOP and get explicit user confirmation before executing (the controller will handle the merge + restart per finishing-a-development-branch + mean-rev-restart conventions).

- [ ] **Step 1: Add the paper twin to `strategies.yaml`** — copy an existing `live:false` determinism twin block (e.g. `det_d12_wide_v1`), set `id: tadiv_approx_v1`, `mode: tadiv_approx`, `tadiv_ret_min_bps: 5.0`, `t_min_sec: 60`, `t_max_sec: 300`, `min_ask: 0.30`, `max_ask: 0.55`, `fixed_bet_usd: 10.0`, `live: false`, `daily_loss_mode: hard_worstcase`. (A second twin `tadiv_approx_ret3_v1` with `tadiv_ret_min_bps: 3.0` is optional — the campaign's best variants were ret_min 3 and 5.)

- [ ] **Step 2: Validate the YAML parses BEFORE restart**

Run: `uv run python -c "from mean_reversion_live.engine.registry import load_strategies; print(len(load_strategies()))"`
Expected: prints the new count without raising.

- [ ] **Step 3: Full suite green**

Run: `uv run pytest -q`
Expected: all passed.

- [ ] **Step 4: Merge branch → main, then safe-window restart of run_combined ONLY** (executor untouched). Verify heartbeat fresh + `strategies_loaded` includes `tadiv_approx_v1`. Confirm it FIRES within a sensible window (or that "no intent = no opportunity yet", per the mean-rev monitoring conventions).

- [ ] **Step 5: Append a dated `STATE.md` entry** — campaign outcome + the twin(s) deployed + the forward-monitoring gate (≥7 clean days realized EV/fill CI-lower > 0 before any live talk).

- [ ] **Step 6: Commit**

```bash
git add strategies.yaml STATE.md
git commit -m "feat(tadiv): deploy tadiv_approx paper twin + campaign state log"
```

---

## PART B — Parity-faithful `tadiv` mode (follow-up; execute after user review)

The approximation uses `vel_bps` (bps vs strike) and a 10s-velocity slope proxy. The faithful variant matches `research/dataset/ta_features.py` exactly: EMA(span=30)-of-spot slope in bps-vs-spot and the 30s return in bps-vs-spot, with `sign(ema_slope) == sign(ret_30s)`.

- **Task B1:** Add `ema_slope_bps(span=30)` (and an explicit `ret_bps(win)` documenting the spot-vs-strike vs spot-vs-spot distinction) to `RollingMove` in `det_features.py`, OR track raw spot in the state. Unit-test against hand-computed EMA values.
- **Task B2:** Add `mode="tadiv"` (`_tadiv_faithful_entry`) using those features; new `DetParams` fields (`tadiv_slope_min_bps`, reuse `tadiv_ret_min_bps`).
- **Task B3:** A research-parity test (`tests/research/test_tadiv_parity.py`) pinning the engine's EMA-slope + 30s-return arithmetic to `ta_features._features_one` on a replayed window (the psettle/xb parity-test pattern).
- **Task B4:** Deploy `tadiv_faithful_v1` paper twin alongside `tadiv_approx_v1` for a forward A/B (does the parity subtlety matter?).

Gate for any live promotion of either twin (unchanged): ≥7 clean forward days realized EV/fill CI-lower > 0, present-first per `feedback_supervised_realmoney`.

---

## Self-Review notes

- **Replay-parity:** every new mode is a separate early-return path (psettle/xb pattern); the `consistent`/`disagree` flow is untouched → `tests/test_paper_engine_replay.py` stays green (asserted in Task A2 Step 6).
- **No silent production:** Task A4 is explicitly gated on user go; twins are `live:false`.
- **Approximation honesty:** the vel_bps-vs-research-ta_ret_30s unit mismatch is documented in the DetParams comment and is the explicit reason PART B exists.
- **Type consistency:** `tadiv_ret_min_bps`, `_tadiv_entry`, `mode="tadiv_approx"`, `strategy_kind="tadiv_approx"` used identically across A1–A4.
