"""Per-strategy isolation + migration tests for the standalone live executor.

The executor (`scripts/live_executor.py`) runs MULTIPLE live strategies (det_lwd_live and
det_d12_wide_live) off one shared wallet. Each strategy must be an INDEPENDENT "book": its own
bankroll balance, its own per-UTC-day loss cap, its own per-slug dedup, its own concurrency —
so one strategy hitting a cap can never block another, and both can trade the same window.

No mocking: these exercise the real Executor state/cap logic against tmp state files. The only
external dependency (Gamma resolution) is kept out of the tested path — the settlement booking
math is exercised via the pure `_apply_settlement` (the network fetch is the caller's job).
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import live_executor as le  # noqa: E402


def _shipped_default(name, cast):
    """The os.getenv fallback literal for `name`, read from the executor's source.

    The module runs load_dotenv() at import, so its live constants carry whatever the
    deployment currently arms. Tests that pin "ships unarmed" must therefore assert the
    SHIPPED fallback, not the ambient value — otherwise arming a knob in .env (a normal,
    signed-off act) turns the suite permanently red."""
    import re
    src = (REPO / "scripts" / "live_executor.py").read_text()
    m = re.search(rf'os\.getenv\(\s*"{name}"\s*,\s*"([^"]*)"\s*\)', src)
    assert m, f"no os.getenv fallback found for {name} — did the constant get renamed?"
    return cast(m.group(1))


def _slug(symbol="btc"):
    """A 15m slug whose window ends ~25min out, so the `time_left` gate always passes."""
    ws = int(time.time()) + 600          # window_end = ws + 900 = now + 1500s
    return f"{symbol}-updown-15m-{ws}"


def _intent(sid, slug, bankroll=100, daily=25):
    return {"strategy_id": sid, "slug": slug, "bankroll_usd": bankroll, "max_daily_loss_usd": daily}


# --- migration -------------------------------------------------------------

def test_flat_v1_migrates_losslessly_into_det_lwd_live(tmp_path):
    flat = {"done_slugs": ["btc-updown-15m-1780900200", "eth-updown-15m-1780903800"],
            "deployed": 121.547073, "realized_total": 1.991701,
            "realized_by_day": {"2026-06-08": 1.991701}, "pending": []}
    sp = tmp_path / "executor_state.json"
    sp.write_text(json.dumps(flat))

    ex = le.Executor(clob=None, mode="dry_run", state_path=sp)
    assert set(ex.books.keys()) == {"det_lwd_live"}
    b = ex.books["det_lwd_live"]
    assert b.realized_total == 1.991701
    assert b.realized_by_day == {"2026-06-08": 1.991701}
    assert b.done_slugs == set(flat["done_slugs"])
    assert b.deployed == 121.547073
    assert b.bankroll_usd == le.DEFAULT_BANKROLL_USD
    assert b.max_daily_loss_usd == le.DEFAULT_DAILY_CAP_USD

    # original backed up; file rewritten as v2
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("executor_state.flatv1.")]
    assert len(backups) == 1
    raw = json.loads(sp.read_text())
    assert raw["version"] == 2 and "det_lwd_live" in raw["strategies"]

    # idempotent: a second load sees v2, does NOT re-migrate, makes no new backup
    ex2 = le.Executor(clob=None, mode="dry_run", state_path=sp)
    assert ex2.books["det_lwd_live"].realized_total == 1.991701
    assert len([p for p in tmp_path.iterdir() if p.name.startswith("executor_state.flatv1.")]) == 1


def test_flat_migration_annotates_pending_with_owner(tmp_path):
    flat = {"done_slugs": [], "deployed": 5.0, "realized_total": 0.0, "realized_by_day": {},
            "pending": [{"slug": "btc-updown-15m-1780900200", "side": "UP", "usdc": 5.0, "shares": 10.0}]}
    sp = tmp_path / "executor_state.json"
    sp.write_text(json.dumps(flat))
    ex = le.Executor(clob=None, mode="dry_run", state_path=sp)
    pend = ex.books["det_lwd_live"].pending
    assert len(pend) == 1 and pend[0]["strategy_id"] == "det_lwd_live"


# --- per-strategy isolation ------------------------------------------------

def test_daily_cap_isolated_between_strategies(tmp_path):
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json")
    a = ex._book("det_lwd_live", bankroll=100, daily_cap=25)
    a.realized_by_day[le._utc_day()] = -25.0          # A pinned at its daily cap
    slug = _slug()
    assert "daily loss cap" in ex._blocked(_intent("det_lwd_live", slug))   # A blocked
    assert ex._blocked(_intent("det_d12_wide_live", slug)) is None          # B unaffected


def test_done_slugs_isolated_same_slug_both_strategies(tmp_path):
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json")
    slug = _slug()
    ex._book("det_lwd_live").done_slugs.add(slug)      # A already traded this window
    assert "already traded" in ex._blocked(_intent("det_lwd_live", slug))   # A deduped
    assert ex._blocked(_intent("det_d12_wide_live", slug)) is None          # B can still trade it


def test_bankroll_exhaustion_isolated(tmp_path):
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json")
    slug = _slug()
    ex._book("det_lwd_live").realized_total = -100.0   # A bankroll gone
    assert "bankroll exhausted" in ex._blocked(_intent("det_lwd_live", slug))
    assert ex._blocked(_intent("det_d12_wide_live", slug)) is None


def test_unknown_strategy_gets_fresh_isolated_book(tmp_path):
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json")
    slug = _slug()
    assert ex._blocked(_intent("brand_new", slug, bankroll=100, daily=25)) is None
    assert "brand_new" in ex.books
    assert ex.books["brand_new"].bankroll_usd == 100
    assert ex.books["brand_new"].max_daily_loss_usd == 25


def test_caps_come_from_the_intent(tmp_path):
    """A strategy with a $10 daily cap trips at -$10, independent of the $25 default."""
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json")
    b = ex._book("tight", bankroll=50, daily_cap=10)
    b.realized_by_day[le._utc_day()] = -10.0
    assert "daily loss cap" in ex._blocked(_intent("tight", _slug(), bankroll=50, daily=10))


# --- concurrency -----------------------------------------------------------

def test_per_strategy_concurrency_cap(tmp_path):
    # C2: caps count RESERVATIONS (inflight_slugs, made before the first await), not
    # ladders (b.open) — counting open let N intents pass _blocked during each other's
    # preflight awaits and all ladder at once.
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json")
    a = ex._book("A")
    a.inflight_slugs = {_slug("btc"), _slug("eth")}
    assert len(a.inflight_slugs) >= le.PER_STRAT_MAX_CONCURRENT
    assert "max concurrent" in ex._blocked(_intent("A", _slug("sol")))
    # a different strategy with no reservations is unaffected
    assert ex._blocked(_intent("B", _slug("sol"))) is None


def test_global_concurrency_ceiling(tmp_path):
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json")
    # spread one reservation across enough strategies to hit the global ceiling
    for i in range(le.GLOBAL_MAX_CONCURRENT):
        ex._book(f"S{i}").inflight_slugs = {_slug(f"c{i}")}
    reason = ex._blocked(_intent("FRESH", _slug()))
    assert reason is not None and "global max concurrent" in reason


# --- settlement booking ----------------------------------------------------

def test_settlement_books_into_correct_strategy(tmp_path):
    sp = tmp_path / "s.json"
    settl = tmp_path / "settlements.jsonl"
    ex = le.Executor(clob=None, mode="dry_run", state_path=sp, settlements_path=settl)
    wend = int(time.time()) - 100
    b = ex._book("det_d12_wide_live", bankroll=100, daily_cap=25)
    # win: bought 10 shares for $5 -> pnl = shares - usdc = +5
    p = {"strategy_id": "det_d12_wide_live", "slug": f"btc-updown-15m-{wend-900}",
         "side": "UP", "usdc": 5.0, "shares": 10.0}
    won, pnl, day = ex._apply_settlement("det_d12_wide_live", b, p, "UP", wend)
    assert won is True and pnl == 5.0
    assert b.realized_total == 5.0 and b.realized_by_day[le._utc_day(wend)] == 5.0
    # other strategy untouched
    assert ex.books.get("det_lwd_live") is None

    # ledger line carries the strategy_id
    lines = settl.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["strategy_id"] == "det_d12_wide_live" and rec["pnl"] == 5.0 and rec["won"] is True

    # loss: pnl = -usdc
    p2 = {"strategy_id": "det_d12_wide_live", "slug": f"btc-updown-15m-{wend-900}",
          "side": "DOWN", "usdc": 5.0, "shares": 10.0}
    won2, pnl2, _ = ex._apply_settlement("det_d12_wide_live", b, p2, "UP", wend)
    assert won2 is False and pnl2 == -5.0
    assert b.realized_total == 0.0


# --- persistence -----------------------------------------------------------

def test_v2_save_load_roundtrip(tmp_path):
    sp = tmp_path / "s.json"
    ex = le.Executor(clob=None, mode="dry_run", state_path=sp)
    b = ex._book("det_d12_wide_live", bankroll=100, daily_cap=25)
    b.done_slugs.add("btc-updown-15m-123")
    b.realized_total = 7.5
    b.realized_by_day = {"2026-06-08": 7.5}
    b.deployed = 12.34
    b.pending = [{"strategy_id": "det_d12_wide_live", "slug": "x", "side": "UP",
                  "usdc": 5.0, "shares": 10.0}]
    ex._save_state()

    ex2 = le.Executor(clob=None, mode="dry_run", state_path=sp)
    b2 = ex2.books["det_d12_wide_live"]
    assert b2.realized_total == 7.5
    assert b2.realized_by_day == {"2026-06-08": 7.5}
    assert b2.done_slugs == {"btc-updown-15m-123"}
    assert b2.deployed == 12.34
    assert b2.pending == b.pending
    assert b2.bankroll_usd == 100 and b2.max_daily_loss_usd == 25

    raw = json.loads(sp.read_text())
    assert raw["version"] == 2 and raw["deployed_total"] == 12.34


def test_corrupt_state_refuses_to_trade(tmp_path):
    sp = tmp_path / "s.json"
    sp.write_text("{not valid json")
    ex = le.Executor(clob=None, mode="dry_run", state_path=sp)
    assert ex._load_failed is True
    reason = ex._blocked(_intent("A", _slug()))
    assert reason is not None and "state load failed" in reason


# --- laddered fill (capped at the per-strategy max_ask, never the 0.92 hardcode) ----------

class _FakeClob:
    """Minimal clob: records fill_or_chase kwargs and returns a fixed AggregatedFill-like."""
    def __init__(self, agg, tick="0.01"):
        self._agg = agg
        self._tick = tick
        self.chase_calls = []

    async def get_tick_size(self, token_id):
        return self._tick

    async def fill_or_chase(self, **kw):
        self.chase_calls.append(kw)
        return self._agg


def _full_intent(sid, slug, *, side="UP", entry_ask=0.70, bet=5.0, max_ask=0.85):
    return {"strategy_id": sid, "slug": slug, "side": side, "entry_ask": entry_ask,
            "bet_usd": bet, "max_ask": max_ask, "bankroll_usd": 100, "max_daily_loss_usd": 25}


async def test_laddered_fill_books_aggregate_and_caps_at_max_ask(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    agg = SimpleNamespace(total_shares=6.5, total_usdc=4.9, attempts=2, stopped_reason="filled")
    clob = _FakeClob(agg)
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl")
    slug = _slug()
    await ex.handle(_full_intent("det_d12_wide_live", slug, entry_ask=0.70, max_ask=0.85))
    b = ex.books["det_d12_wide_live"]
    # booked ONCE from the aggregate totals (no double-fill)
    assert len(b.pending) == 1
    assert b.pending[0]["shares"] == 6.5 and b.pending[0]["usdc"] == 4.9
    assert b.deployed == 4.9 and slug in b.done_slugs
    assert b.open == 0                                   # concurrency counter released
    # fill_or_chase was driven at the per-strategy ceiling (0.85), NOT 0.92
    assert clob.chase_calls and clob.chase_calls[0]["price_ceiling"] == 0.85
    assert clob.chase_calls[0]["target_price"] == 0.70  # not entry_ask+0.05


async def test_ceiling_never_exceeds_abs_max_price(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    agg = SimpleNamespace(total_shares=5.0, total_usdc=4.5, attempts=1, stopped_reason="filled")
    clob = _FakeClob(agg)
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl")
    # a (hypothetical) strategy with max_ask 0.99 must still be capped at ABS_MAX_PRICE
    await ex.handle(_full_intent("S", _slug(), entry_ask=0.88, max_ask=0.99))
    assert clob.chase_calls[0]["price_ceiling"] == le.ABS_MAX_PRICE


async def test_quoted_ask_above_band_skips_without_filling(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    agg = SimpleNamespace(total_shares=0.0, total_usdc=0.0, attempts=0, stopped_reason="n/a")
    clob = _FakeClob(agg)
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl")
    slug = _slug()
    # ask 0.90 above the validated band 0.85 -> a fill there is -EV -> skip, never call fill_or_chase
    await ex.handle(_full_intent("det_d12_wide_live", slug, entry_ask=0.90, max_ask=0.85))
    assert clob.chase_calls == []
    b = ex.books["det_d12_wide_live"]
    assert len(b.pending) == 0 and slug not in b.done_slugs  # not consumed; nothing booked


# --- execution-integrity guards (floor abort / depth pre-flight / latency gate) -----------
#
# Diagnosed from 4.5 days of live fills (research/analysis/live_gap_attribution.py):
#   * knife-catch fills (book collapsed below quote between signal and order) are a -EV
#     cohort -> pre-flight FLOOR abort;
#   * 62 API-400 misses were IOCs priced below the real touch (fill_or_chase breaks on the
#     IOC error and never advances) -> pre-flight lifts the ladder start to the real touch;
#   * dry in-band books sometimes refill -> ONE delayed retry instead of immediate chase;
#   * intents older than the validated 10s latency-survival bound -> dropped.
# Modes: off (legacy byte-identical) | shadow (check+log, place anyway) | on (enforce).
# EXEC_GUARDS_ENFORCE_SIDS scopes enforcement per strategy for the A/B.

def _book_dict(ask_levels, bid_levels=()):
    """Order-book in the dict shape; price/size as strings like the SDK returns."""
    return {"asks": [{"price": str(p), "size": str(s)} for p, s in ask_levels],
            "bids": [{"price": str(p), "size": str(s)} for p, s in bid_levels]}


class _FakeClob2(_FakeClob):
    """_FakeClob + a scripted get_book: returns books in order, last one repeats."""
    def __init__(self, agg, books=None, tick="0.01", book_exc=None):
        super().__init__(agg, tick)
        self.books = list(books or [])
        self.book_calls = 0
        self.book_exc = book_exc

    async def get_book(self, token_id):
        self.book_calls += 1
        if self.book_exc is not None:
            raise self.book_exc
        if not self.books:
            return None
        if len(self.books) > 1:
            return self.books.pop(0)
        return self.books[0]


def _read_fills(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_parse_book_object_and_dict_shapes():
    # dict shape (unsorted on purpose)
    asks, bids = le.parse_book(_book_dict([(0.75, 10), (0.72, 5)], [(0.60, 4), (0.65, 7)]))
    assert asks == [(0.72, 5.0), (0.75, 10.0)]          # asks ascending
    assert bids == [(0.65, 7.0), (0.60, 4.0)]           # bids descending
    # object shape (py-clob OrderBookSummary-like)
    book = SimpleNamespace(
        asks=[SimpleNamespace(price="0.50", size="10")],
        bids=[SimpleNamespace(price="0.45", size="3")])
    asks, bids = le.parse_book(book)
    assert asks == [(0.50, 10.0)] and bids == [(0.45, 3.0)]
    # degenerate
    assert le.parse_book(None) == ([], [])


def test_band_depth():
    asks = [(0.70, 5.0), (0.74, 10.0), (0.90, 99.0)]
    assert le.band_depth(asks, 0.0, 0.85) == 15.0
    assert le.band_depth(asks, 0.72, 0.85) == 10.0


def test_latency_gate_drops_stale_intent_enforced(tmp_path):
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json", guards="on")
    it = _intent("A", _slug())
    it["ts_ms"] = (time.time() - 30) * 1000.0
    reason = ex._blocked(it)
    assert reason is not None and "intent age" in reason


def test_latency_gate_shadow_logs_but_allows(tmp_path):
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json", guards="shadow")
    it = _intent("A", _slug())
    it["ts_ms"] = (time.time() - 30) * 1000.0
    assert ex._blocked(it) is None
    # fresh intent passes even enforced
    ex2 = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s2.json", guards="on")
    it2 = _intent("A", _slug())
    it2["ts_ms"] = time.time() * 1000.0
    assert ex2._blocked(it2) is None


async def test_preflight_floor_abort_places_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    agg = SimpleNamespace(total_shares=7.0, total_usdc=2.8, attempts=1, stopped_reason="filled")
    # book collapsed: best ask 0.40 vs entry 0.66 -> the favourite flipped; a fill is a knife-catch
    clob = _FakeClob2(agg, books=[_book_dict([(0.40, 50)])])
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="on")
    slug = _slug()
    await ex.handle(_full_intent("det_lwd_live", slug, entry_ask=0.66, max_ask=0.88))
    assert clob.chase_calls == []                        # nothing placed
    recs = _read_fills(fp)
    assert len(recs) == 1 and recs[0]["ok"] is False
    assert recs[0]["note"].startswith("guard:floor_abort")
    assert recs[0]["guard"]["verdict"] == "abort_floor"
    b = ex.books["det_lwd_live"]
    assert slug in b.done_slugs                          # clean miss, intent consumed
    assert b.open == 0 and len(b.pending) == 0


async def test_preflight_dry_single_retry_then_sends_at_touch(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    monkeypatch.setattr(le, "EXEC_DRY_RETRY_S", 0.01)
    agg = SimpleNamespace(total_shares=7.2, total_usdc=5.18, attempts=1, stopped_reason="filled")
    thin = _book_dict([(0.72, 1)])                       # 1 share < 50% of target
    deep = _book_dict([(0.72, 50)])
    clob = _FakeClob2(agg, books=[thin, deep])
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="on")
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.70, max_ask=0.88))
    assert clob.book_calls == 2                          # pre-flight + one delayed re-check
    assert len(clob.chase_calls) == 1
    # ladder starts at the REAL touch (0.72), not the stale intent ask (0.70):
    # an IOC below the touch is an API-400, not a fill
    assert clob.chase_calls[0]["target_price"] == 0.72
    recs = _read_fills(fp)
    assert recs[0]["ok"] is True and recs[0]["guard"]["verdict"] == "ok"


async def test_preflight_dry_after_retry_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    monkeypatch.setattr(le, "EXEC_DRY_RETRY_S", 0.01)
    agg = SimpleNamespace(total_shares=0.0, total_usdc=0.0, attempts=0, stopped_reason="n/a")
    thin = _book_dict([(0.72, 1)])
    clob = _FakeClob2(agg, books=[thin, thin])
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="on")
    slug = _slug()
    await ex.handle(_full_intent("det_lwd_live", slug, entry_ask=0.70, max_ask=0.88))
    assert clob.chase_calls == []
    recs = _read_fills(fp)
    assert recs[0]["ok"] is False
    assert recs[0]["note"].startswith("guard:dry_after_retry")
    assert slug in ex.books["det_lwd_live"].done_slugs


async def test_shadow_mode_checks_but_places(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    agg = SimpleNamespace(total_shares=7.0, total_usdc=2.8, attempts=1, stopped_reason="filled")
    clob = _FakeClob2(agg, books=[_book_dict([(0.40, 50)])])     # collapsed book
    fp = tmp_path / "fills.jsonl"
    # enforce_sids pinned empty: the production .env arms EXEC_GUARDS_ENFORCE_SIDS
    # (the live A/B), which would otherwise leak in and enforce this sid
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="shadow", enforce_sids=set())
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.66, max_ask=0.88))
    assert len(clob.chase_calls) == 1                    # legacy path still executes
    recs = _read_fills(fp)
    assert recs[0]["ok"] is True
    assert recs[0]["guard"]["verdict"] == "abort_floor"  # the would-verdict is recorded
    assert recs[0]["guard"]["mode"] == "shadow"


async def test_enforce_sids_scopes_enforcement(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    agg = SimpleNamespace(total_shares=7.0, total_usdc=2.8, attempts=1, stopped_reason="filled")
    collapsed = _book_dict([(0.40, 50)])
    clob = _FakeClob2(agg, books=[collapsed])
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="shadow", enforce_sids={"A"})
    await ex.handle(_full_intent("A", _slug("btc"), entry_ask=0.66, max_ask=0.88))
    assert clob.chase_calls == []                        # A enforced -> floor abort
    await ex.handle(_full_intent("B", _slug("eth"), entry_ask=0.66, max_ask=0.88))
    assert len(clob.chase_calls) == 1                    # B shadow -> places


async def test_post_fill_knife_catch_flagged_even_guards_off(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    # avg fill 0.30 vs quoted 0.66 -> knife (book collapsed through the order)
    agg = SimpleNamespace(total_shares=10.0, total_usdc=3.0, attempts=1, stopped_reason="filled")
    clob = _FakeClob2(agg)
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="off")
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.66, max_ask=0.88))
    recs = _read_fills(fp)
    assert recs[0]["knife_catch"] is True


async def test_guards_off_is_legacy_no_book_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    agg = SimpleNamespace(total_shares=6.5, total_usdc=4.9, attempts=2, stopped_reason="filled")
    clob = _FakeClob2(agg, books=[_book_dict([(0.40, 50)])])
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="off")
    await ex.handle(_full_intent("det_d12_wide_live", _slug(), entry_ask=0.70, max_ask=0.85))
    assert clob.book_calls == 0                          # zero pre-flight in off mode
    assert len(clob.chase_calls) == 1
    recs = _read_fills(fp)
    assert "guard" not in recs[0]
    # legacy ladder semantics preserved
    assert clob.chase_calls[0]["target_price"] == 0.70
    assert clob.chase_calls[0]["price_ceiling"] == 0.85


async def test_preflight_error_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    agg = SimpleNamespace(total_shares=7.0, total_usdc=4.9, attempts=1, stopped_reason="filled")
    clob = _FakeClob2(agg, book_exc=RuntimeError("REST hiccup"))
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="on")
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.70, max_ask=0.88))
    assert len(clob.chase_calls) == 1                    # a flaky book check must not zero the probe
    recs = _read_fills(fp)
    assert recs[0]["guard"]["verdict"] == "preflight_error"


async def test_state_schema_unchanged_after_guarded_fill(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    agg = SimpleNamespace(total_shares=7.2, total_usdc=5.18, attempts=1, stopped_reason="filled")
    clob = _FakeClob2(agg, books=[_book_dict([(0.72, 50)])])
    sp = tmp_path / "s.json"
    ex = le.Executor(clob=clob, mode="live", state_path=sp,
                     fills_path=tmp_path / "fills.jsonl", guards="on")
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.70, max_ask=0.88))
    raw = json.loads(sp.read_text())
    assert set(raw.keys()) == {"version", "deployed_total", "strategies"}
    book_keys = set(raw["strategies"]["det_lwd_live"].keys())
    assert book_keys == {"done_slugs", "deployed", "realized_total", "realized_by_day",
                         "pending", "bankroll_usd", "max_daily_loss_usd"}


# --- multi-coin burst cap (BC2, docs/research/BURST_CAPACITY_2026-06-11.md) ----------------
#
# One macro move fires the disagree signal on several coins at the same window_start_ts and
# the members win/lose together — a burst is ONE leveraged macro bet, not diversification.
# Cap = max intents a strategy may CONSUME per window-ts, arrival order (keep-first is the
# backtest's validated tie-break). EXEC_BURST_CAP=0 default keeps behavior byte-identical.

def _sibling_slugs(symbols=("btc", "eth", "sol")):
    """Same window_start_ts across coins (the burst signature)."""
    ws = int(time.time()) + 600
    return [f"{s}-updown-15m-{ws}" for s in symbols]


def _agg_filled():
    return SimpleNamespace(total_shares=6.0, total_usdc=4.8, attempts=1, stopped_reason="filled")


async def test_burst_cap_blocks_second_sibling_same_window(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    clob = _FakeClob(_agg_filled())
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl",
                     burst_cap=1, burst_cap_sids={"fav_disagree_live"})
    btc, eth, _ = _sibling_slugs()
    await ex.handle(_full_intent("fav_disagree_live", btc))
    await ex.handle(_full_intent("fav_disagree_live", eth))
    b = ex.books["fav_disagree_live"]
    assert btc in b.done_slugs                    # first arrival consumed
    assert eth not in b.done_slugs                # sibling blocked, NOT consumed
    assert len(clob.chase_calls) == 1             # no order ever placed for the sibling
    reason = ex._blocked(_full_intent("fav_disagree_live", eth))
    assert reason is not None and "burst cap" in reason


async def test_burst_cap_allows_next_window(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    clob = _FakeClob(_agg_filled())
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl",
                     burst_cap=1, burst_cap_sids={"fav_disagree_live"})
    btc, _, _ = _sibling_slugs()
    ws2 = int(time.time()) + 600 + 900            # the NEXT window: different window-ts
    await ex.handle(_full_intent("fav_disagree_live", btc))
    assert ex._blocked(_full_intent("fav_disagree_live", f"eth-updown-15m-{ws2}")) is None


async def test_burst_cap_counts_inflight_sibling(tmp_path):
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl",
                     burst_cap=1, burst_cap_sids={"fav_disagree_live"})
    btc, eth, _ = _sibling_slugs()
    b = ex._book("fav_disagree_live", bankroll=100, daily_cap=25)
    b.inflight_slugs.add(btc)                     # concurrent sibling mid-fill
    reason = ex._blocked(_full_intent("fav_disagree_live", eth))
    assert reason is not None and "burst cap" in reason


async def test_burst_cap_off_by_default_and_sid_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    clob = _FakeClob(_agg_filled())
    # cap off (default 0): the burst gate itself never fires. Since C2 the GLOBAL
    # (window, direction) macro-correlation cap still holds a filled same-direction
    # sibling — the opposite direction is what flows freely.
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl",
                     burst_cap=0, burst_cap_sids=set())
    btc, eth, _ = _sibling_slugs()
    await ex.handle(_full_intent("fav_disagree_live", btc, side="UP"))
    same_dir = ex._blocked(_full_intent("fav_disagree_live", eth, side="UP"))
    assert same_dir is not None and "macro-correlation" in same_dir
    assert ex._blocked(_full_intent("fav_disagree_live", eth, side="DOWN")) is None
    # cap on but scoped to ANOTHER sid: the burst gate does not fire for this strategy
    # (the macro cap still does, so probe the opposite direction)
    ex2 = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s2.json",
                      fills_path=tmp_path / "fills2.jsonl",
                      burst_cap=1, burst_cap_sids={"fav_disagree_live"})
    await ex2.handle(_full_intent("early_disagree_live", btc, side="UP"))
    assert ex2._blocked(_full_intent("early_disagree_live", eth, side="DOWN")) is None


# --- C2 (2026-08-08): slug reservation before the first await + macro-correlation cap ------

class _SlowClob(_FakeClob):
    """fill_or_chase parks on an event, so a sibling intent can arrive mid-ladder."""
    def __init__(self, agg, gate):
        super().__init__(agg)
        self._gate = gate

    async def fill_or_chase(self, **kw):
        await self._gate.wait()
        return await super().fill_or_chase(**kw)


async def test_same_slug_sibling_blocked_during_awaits(tmp_path, monkeypatch):
    """THE C2 race: a duplicate same-slug intent dispatched while the first is mid-await
    must be rejected by the reservation, not double-traded."""
    import asyncio
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    gate = asyncio.Event()
    clob = _SlowClob(_agg_filled(), gate)
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl")
    slug = _slug()
    t1 = asyncio.create_task(ex.handle(_full_intent("A", slug)))
    await asyncio.sleep(0.05)                     # t1 is parked inside fill_or_chase
    t2 = asyncio.create_task(ex.handle(_full_intent("A", slug)))
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(t1, t2)
    assert len(clob.chase_calls) == 1             # exactly one order placed
    assert ex.books["A"].inflight_slugs == set()  # reservation released


async def test_reservation_released_on_early_abort(tmp_path, monkeypatch):
    """A handle that dies before trading (token resolve fails) must free both the slug
    reservation and the (window, direction) slot — a miss is not a position."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: None)
    ex = le.Executor(clob=None, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl")
    btc, eth, _ = _sibling_slugs()
    await ex.handle(_full_intent("A", btc, side="UP"))
    assert ex.books["A"].inflight_slugs == set()
    assert ex.window_side == {}
    assert ex._blocked(_full_intent("B", eth, side="UP")) is None


async def test_window_direction_cap_is_cross_book(tmp_path, monkeypatch):
    """After book A fills UP in a window, book B may not open UP in the SAME window on
    any coin (one leveraged macro bet), but DOWN and the next window both flow."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    clob = _FakeClob(_agg_filled())
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl")
    btc, eth, _ = _sibling_slugs()
    await ex.handle(_full_intent("A", btc, side="UP"))
    blocked = ex._blocked(_full_intent("B", eth, side="UP"))
    assert blocked is not None and "macro-correlation" in blocked
    assert ex._blocked(_full_intent("B", eth, side="DOWN")) is None
    ws2 = int(time.time()) + 600 + 900
    assert ex._blocked(_full_intent("B", f"eth-updown-15m-{ws2}", side="UP")) is None


async def test_burst_cap_restart_safe_via_done_slugs(tmp_path, monkeypatch):
    """Defect-3 lesson: the count derives from PERSISTED done_slugs, so a mid-window
    restart cannot re-open a capped window."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    clob = _FakeClob(_agg_filled())
    sp = tmp_path / "s.json"
    ex = le.Executor(clob=clob, mode="live", state_path=sp,
                     fills_path=tmp_path / "fills.jsonl",
                     burst_cap=1, burst_cap_sids={"fav_disagree_live"})
    btc, eth, _ = _sibling_slugs()
    await ex.handle(_full_intent("fav_disagree_live", btc))
    ex2 = le.Executor(clob=clob, mode="live", state_path=sp,        # fresh process, same state
                      fills_path=tmp_path / "fills.jsonl",
                      burst_cap=1, burst_cap_sids={"fav_disagree_live"})
    reason = ex2._blocked(_full_intent("fav_disagree_live", eth))
    assert reason is not None and "burst cap" in reason


# --- volume-harvest round 2026-07-25 ------------------------------------------------------
# Two measured leaks, both in the ladder, both fixed here:
#   * 36 of 115 ladder rounds died on `400 no orders found to match` because a zero-fill round
#     re-fired the SAME price 4s later — the pre-round-1 touch-bump was never redone;
#   * the single dry re-check rescued 7 of 17 skips, and all 17 were "touch above our ceiling"
#     (a moved price, not an empty book), so more looks should rescue more.
# Plus the per-strategy symbol allowlist: a coin graduates for the strategy that earned on it.

class _FakeClobSeq(_FakeClob2):
    """_FakeClob2 + a SEQUENCE of aggregated fills, one per fill_or_chase round."""
    def __init__(self, aggs, books=None, tick="0.01"):
        super().__init__(aggs[0], books=books, tick=tick)
        self._aggs = list(aggs)

    async def fill_or_chase(self, **kw):
        self.chase_calls.append(kw)
        return self._aggs.pop(0) if len(self._aggs) > 1 else self._aggs[0]


def _agg(shares, usdc, reason):
    return SimpleNamespace(total_shares=shares, total_usdc=usdc, attempts=1,
                           stopped_reason=reason)


async def test_zero_fill_round_requotes_from_the_book(tmp_path, monkeypatch):
    """THE FIX: round 1 gets an API-400 because the touch moved above our limit; round 2 must
    be sent at the NEW touch, not the old price. Previously it re-fired the known-bad price."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    monkeypatch.setattr(le, "LADDER_RETRY_SLEEP_S", 0.01)
    at_quote = _book_dict([(0.70, 50)])      # pre-flight: touch == our quote
    moved_up = _book_dict([(0.74, 50)])      # by round 2 the touch has walked up
    clob = _FakeClobSeq([_agg(0.0, 0.0, "IOC error: 400 no orders found to match"),
                         _agg(7.0, 5.18, "filled")],
                        books=[at_quote, moved_up])
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl", guards="on")
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.70, max_ask=0.88))
    assert len(clob.chase_calls) == 2, "should have spent its second round"
    assert clob.chase_calls[0]["target_price"] == 0.70          # round 1 at the original touch
    assert clob.chase_calls[1]["target_price"] == 0.74, \
        "round 2 must re-quote to the new touch, not re-fire the 400'd price"
    assert ex.books["det_lwd_live"].pending[0]["shares"] == 7.0  # and it actually fills


async def test_requote_aborts_when_the_book_collapses_between_rounds(tmp_path, monkeypatch):
    """A collapsed book between rounds is the -EV knife cohort — stop, don't chase it down."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    monkeypatch.setattr(le, "LADDER_RETRY_SLEEP_S", 0.01)
    clob = _FakeClobSeq([_agg(0.0, 0.0, "IOC error: 400 no orders found to match"),
                         _agg(7.0, 5.18, "filled")],
                        books=[_book_dict([(0.70, 50)]), _book_dict([(0.40, 50)])])
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl", guards="on")
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.70, max_ask=0.88))
    assert len(clob.chase_calls) == 1, "must not place a second order into a collapsed book"


async def test_requote_is_a_noop_when_guards_are_off(tmp_path, monkeypatch):
    """Legacy path stays byte-identical: no pre-flight, so no re-quote get_book either."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    monkeypatch.setattr(le, "LADDER_RETRY_SLEEP_S", 0.01)
    clob = _FakeClobSeq([_agg(0.0, 0.0, "IOC error: 400"), _agg(0.0, 0.0, "IOC error: 400")],
                        books=[_book_dict([(0.74, 50)])])
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl", guards="off")
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.70, max_ask=0.88))
    assert clob.book_calls == 0                                  # never looks at the book
    assert all(c["target_price"] == 0.70 for c in clob.chase_calls)


async def test_dry_retry_n_gives_the_book_more_looks(tmp_path, monkeypatch):
    """EXEC_DRY_RETRY_N=3: the touch sits above our ceiling for two looks, then comes back
    into the band — the 0.45-max_ask cohort's dominant miss shape."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    monkeypatch.setattr(le, "EXEC_DRY_RETRY_S", 0.01)
    monkeypatch.setattr(le, "EXEC_DRY_RETRY_N", 3)
    above = _book_dict([(0.59, 50)])        # touch above the 0.45 ceiling -> band_depth 0 -> dry
    back = _book_dict([(0.44, 50)])         # reprices back into the band
    clob = _FakeClobSeq([_agg(22.0, 9.7, "filled")], books=[above, above, back])
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=tmp_path / "fills.jsonl", guards="on")
    await ex.handle(_full_intent("fav_disagree_hi_live", _slug(), entry_ask=0.42,
                                 bet=10.0, max_ask=0.45))
    assert clob.book_calls == 3                                  # pre-flight + 2 re-checks
    assert len(clob.chase_calls) == 1, "rescued instead of skipped"
    assert clob.chase_calls[0]["target_price"] == 0.44            # sent at the recovered touch


async def test_dry_retry_default_is_one_look(tmp_path, monkeypatch):
    """Regression pin: the shipped default (N=1) is the ORIGINAL single re-check.

    Asserted against the module CONSTANT's fallback, not `le.EXEC_DRY_RETRY_N` — the module
    calls load_dotenv() at import, so the live value reflects whatever .env currently arms
    (N=3 since 2026-07-25). Pinning the ambient value made this test permanently red, which
    is worse than useless: a red suite hides the next real regression."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    monkeypatch.setattr(le, "EXEC_DRY_RETRY_S", 0.01)
    monkeypatch.setattr(le, "EXEC_DRY_RETRY_N", _shipped_default("EXEC_DRY_RETRY_N", int))
    assert le.EXEC_DRY_RETRY_N == 1, "default must stay 1 so deploys are opt-in"
    thin = _book_dict([(0.72, 1)])
    clob = _FakeClob2(_agg(0.0, 0.0, "n/a"), books=[thin, thin, _book_dict([(0.72, 50)])])
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="on")
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.70, max_ask=0.88))
    assert clob.book_calls == 2                                  # NOT 3 — one re-check only
    assert _read_fills(fp)[0]["note"].startswith("guard:dry_after_retry")


# --- per-strategy symbol allowlist (arms the 07-31 capacity gate safely) -------------------

async def test_symbols_extra_grants_a_coin_to_one_strategy_only(tmp_path, monkeypatch):
    """hype's edge lives in the disagree family (+$3.60/fill official); 70 of the last 75 hype
    intents came from det_lwd_live. A global allowlist add would trade the wrong one."""
    monkeypatch.setattr(le, "EXEC_SYMBOLS_EXTRA", {"fav_disagree_hi_live": {"hype"}})
    ex = le.Executor(clob=None, mode="dry_run", state_path=tmp_path / "s.json")
    hype = _slug("hype")
    granted = ex._blocked(_intent("fav_disagree_hi_live", hype))
    assert granted is None, "the granted strategy may trade hype"
    denied = ex._blocked(_intent("det_lwd_live", hype))
    assert denied is not None and "not in live allowlist" in denied
    # the global allowlist is untouched for everyone
    assert ex._blocked(_intent("det_lwd_live", _slug("btc"))) is None
    assert ex._blocked(_intent("fav_disagree_hi_live", _slug("doge"))) is not None


def test_symbols_extra_defaults_empty_and_parses_pairs():
    """Empty default => byte-identical to the global-only behaviour (ships unarmed).
    Malformed entries drop silently: a typo must never widen the allowlist or crash the
    executor at import (it respawns from cron — an import error is a silent outage).

    Checked against the shipped fallback rather than the live `le.EXEC_SYMBOLS_EXTRA`, which
    reflects .env (fav_disagree_hi_live:hype since 2026-07-26). The safety property is that
    an ABSENT or malformed setting never widens the allowlist."""
    assert le._parse_sid_symbols(_shipped_default("EXEC_SYMBOLS_EXTRA", str)) == {}, \
        "must ship UNARMED; arming is a separate signed-off step"
    assert le._parse_sid_symbols("") == {}
    assert le._parse_sid_symbols("a:hype, b:hype , b:DOGE ,junk,,c:") == {
        "a": {"hype"}, "b": {"hype", "doge"}}                    # junk and "c:" dropped


# --- preflight verdict split: above_band vs dry (2026-08-03) --------------------------------

async def test_above_band_is_labelled_but_still_retries_like_dry(tmp_path, monkeypatch):
    """A book priced ABOVE our ceiling is not an empty book. Both still get the delayed
    re-checks (waiting for the reprice INTO the band is the bet), but they must be tellable
    apart in the ledger — sharing the `dry` label already cost us one misdiagnosis."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    monkeypatch.setattr(le, "EXEC_DRY_RETRY_S", 0.01)
    monkeypatch.setattr(le, "EXEC_DRY_RETRY_N", 2)
    above = _book_dict([(0.59, 50)])          # deep book, but the touch clears the 0.45 ceiling
    clob = _FakeClob2(_agg(0.0, 0.0, "n/a"), books=[above, above, above])
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="on")
    await ex.handle(_full_intent("fav_disagree_hi_live", _slug(), entry_ask=0.42,
                                 bet=10.0, max_ask=0.45))
    assert clob.book_calls == 3, "above_band must get the SAME re-checks a dry book gets"
    assert clob.chase_calls == [], "never send an order while the touch is above the ceiling"
    rec = _read_fills(fp)[0]
    assert rec["ok"] is False
    assert rec["guard"]["verdict"] == "above_band"      # not "dry" — the whole point
    assert rec["note"] == "guard:above_band_after_retry"
    assert rec["guard"]["best_ask"] == 0.59             # healthy book, just out of band


async def test_thin_in_band_book_is_still_dry(tmp_path, monkeypatch):
    """Regression pin for the other side of the split: a genuinely thin book in-band keeps
    the `dry` label, so the historical `guard:dry_after_retry` series stays comparable."""
    monkeypatch.setattr(le, "gamma_tokens", lambda slug: ("UPTOK", "DOWNTOK"))
    monkeypatch.setattr(le, "EXEC_DRY_RETRY_S", 0.01)
    thin = _book_dict([(0.72, 1)])            # in band (ceiling 0.88) but 1 share
    clob = _FakeClob2(_agg(0.0, 0.0, "n/a"), books=[thin, thin])
    fp = tmp_path / "fills.jsonl"
    ex = le.Executor(clob=clob, mode="live", state_path=tmp_path / "s.json",
                     fills_path=fp, guards="on")
    await ex.handle(_full_intent("det_lwd_live", _slug(), entry_ask=0.70, max_ask=0.88))
    rec = _read_fills(fp)[0]
    assert rec["guard"]["verdict"] == "dry"
    assert rec["note"] == "guard:dry_after_retry"


# --- C1: a restart must REPLAY the intent backlog, not silently discard it (2026-08-03) -----

async def test_restart_replays_backlog_instead_of_starting_at_eof(tmp_path, monkeypatch):
    """The executor used to start reading at EOF, so every restart threw away whatever was
    already queued — no log line, no fill record, no counter, invisible everywhere. The
    hourly cron is its only supervisor, so an unnoticed crash could drop an hour of intents.
    Replay is safe because the normal gates reject stale lines for free."""
    intents = tmp_path / "intents.jsonl"
    backlog = [_full_intent("det_lwd_live", _slug("btc")),
               _full_intent("fav_disagree_live", _slug("eth"))]
    intents.write_text("".join(json.dumps(i) + "\n" for i in backlog))
    monkeypatch.setattr(le, "INTENTS", intents)
    monkeypatch.setattr(le, "LIVE_DIR", tmp_path)

    seen = []

    class _SpyExecutor:                       # records what the loop feeds it
        guards, enforce_sids, burst_cap, burst_cap_sids, books = "on", set(), 1, set(), {}
        mode = "dry_run"
        async def handle(self, intent):
            seen.append(intent["slug"])
        def settle_pending(self):
            pass

    monkeypatch.setattr(le, "Executor", lambda *a, **kw: _SpyExecutor())
    calls = {"n": 0}

    def _kill_after_one_pass(mode):           # let exactly one pass run, then halt
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(le, "_killed", _kill_after_one_pass)
    await le.run_loop("dry_run")
    assert seen == [i["slug"] for i in backlog], "backlogged intents must be replayed"


async def test_backlog_replay_skips_provably_stale_lines_regardless_of_guards(tmp_path, monkeypatch):
    """The replay must not depend on EXEC_GUARDS=on to stay safe. A line older than the
    staleness bound is dropped at boot by timestamp; a fresh one is replayed; a legacy line
    with no ts_ms fails OPEN (the remaining gates reject it for free). Both counts logged."""
    intents = tmp_path / "intents.jsonl"
    now_ms = time.time() * 1000
    old = {**_full_intent("det_lwd_live", _slug("btc")), "ts_ms": now_ms - 600_000}   # 10 min
    fresh = {**_full_intent("det_lwd_live", _slug("eth")), "ts_ms": now_ms - 1_000}   # 1 s
    intents.write_text("".join(json.dumps(i) + "\n" for i in (old, fresh)))
    monkeypatch.setattr(le, "INTENTS", intents)
    monkeypatch.setattr(le, "LIVE_DIR", tmp_path)

    seen = []

    class _SpyExecutor:
        guards, enforce_sids, burst_cap, burst_cap_sids, books = "off", set(), 1, set(), {}
        mode = "dry_run"
        async def handle(self, intent):
            seen.append(intent["slug"])
        def settle_pending(self):
            pass

    monkeypatch.setattr(le, "Executor", lambda *a, **kw: _SpyExecutor())
    calls = {"n": 0}

    def _kill_after_one_pass(mode):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(le, "_killed", _kill_after_one_pass)
    await le.run_loop("dry_run")
    # guards are OFF here: the 10-minute-old line must still never reach handle()
    assert seen == [fresh["slug"]], "stale backlog line must be dropped at boot, not handled"
