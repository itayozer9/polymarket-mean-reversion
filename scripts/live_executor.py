"""Standalone live executor for the determinism probe.

Runs on Python 3.11 (py-clob-client-v2 needs >=3.9.10), in a SEPARATE process from
the paper bot (3.9.6). It consumes the entry intents the engine writes for
`live: true` strategies (data/live/intents.jsonl) and places real FAK orders.

  uv run --python 3.11 --no-project \
    --with py-clob-client-v2 --with python-dotenv --with structlog --with requests \
    scripts/live_executor.py --dry-run

Modes:
  --dry-run            (default) resolve token_ids + compute the exact order, place NOTHING.
  --place-cancel-test  place ONE real, NON-MARKETABLE FAK ($0.01 buy, min size) on a live
                       token to validate the signing/POST path; it cannot fill, so it
                       costs nothing. Proves auth+order plumbing end-to-end.
  --live               place real marketable FAK BUY orders (size+caps per strategy). REAL MONEY.

Safety (all modes that touch money): KILL switch (data/KILL), per-slug dedup,
min time_left, and a loss-based bankroll BALANCE. Caps are PER STRATEGY — each live
strategy is an independent "book" with its own bankroll (stop for good at its -$ cumulative
realized loss) and its own per-UTC-day loss cap (pause for the day), so two strategies never
interrupt each other. Wins credit a strategy's balance back, so there is no lifetime spend
ceiling. The per-strategy caps arrive on each intent (bankroll_usd / max_daily_loss_usd,
from strategies.yaml). TAKE-liquidity-only (cross the spread; never rest — A2 showed a
resting maker bleeds -$1.99/tr to adverse selection).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

REPO = Path(__file__).resolve().parents[1]
# Load .env if python-dotenv is available (it's a --with dep for the live run, not a test dep).
try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except Exception:
    pass
# NOTE: `clob_trade` (the py-clob-client-v2 SDK) AND `requests` (network calls only) are
# imported LAZILY (in make_client / the gamma_* functions) so this module — and the Executor
# state/cap logic — stays importable for unit tests with no SDK, no requests/dotenv, no network.

log = structlog.get_logger("live_exec")

GAMMA = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
LIVE_DIR = REPO / "data" / "live"
INTENTS = LIVE_DIR / "intents.jsonl"
FILLS = LIVE_DIR / "fills.jsonl"
STATE = LIVE_DIR / "executor_state.json"
SETTLEMENTS = LIVE_DIR / "settlements.jsonl"  # append-only per-settlement ledger (status reads it)
KILL = REPO / "data" / "KILL"                # global halt (also stops the paper bot)
EXEC_KILL = LIVE_DIR / "EXEC_KILL"           # executor-only halt (leaves the bot running)


def _killed(mode: str = "live") -> bool:
    """data/KILL = full halt (always). EXEC_KILL = executor-only 'no real orders'
    switch: it blocks LIVE placement but NOT a dry-run (which places nothing), so a
    live-mode dry-run can run with EXEC_KILL present — and the monitor cron, which
    only auto-starts the LIVE executor when EXEC_KILL is ABSENT, stays safe."""
    if KILL.exists():
        return True
    if mode == "live" and EXEC_KILL.exists():
        return True
    return False

# --- safety knobs ----------------------------------------------------------
TIME_LEFT_MIN = 20        # s — need room to fill before the window resolves
# Caps are PER STRATEGY: each live strategy is an independent "book" with its own bankroll
# BALANCE and its own per-UTC-day loss cap, so the strategies never interrupt each other.
# Bankroll is a balance, not a spend cap: wins credit back, losses debit; a strategy stops
# only when its cumulative realized loss eats its whole bankroll, or its per-day realized
# loss hits its daily cap (then it waits for the next UTC day). The values below are
# DEFAULTS; the live ones arrive on each intent (bankroll_usd / max_daily_loss_usd, from
# strategies.yaml — the single source of truth).
DEFAULT_BANKROLL_USD = 100.0   # default max cumulative realized LOSS before a strategy stops
DEFAULT_DAILY_CAP_USD = 25.0   # default max realized LOSS per UTC day; resumes at 00:00 UTC
PER_STRAT_MAX_CONCURRENT = 2   # open live positions per strategy
GLOBAL_MAX_CONCURRENT = 4      # hard ceiling on in-flight orders across ALL strategies
                               # (shared-wallet guard: bounds worst-case collateral drain)
STATE_VERSION = 2
SLIP_TICKS = 2            # (legacy) superseded by the FAK-at-edge-max fill
PRICE_CEIL_OVER = 0.05    # marketable FAK limit = quoted ask + this (fills at best real ask <= limit)
ABS_MAX_PRICE = 0.92      # ... hard-capped here — never pay above this for the favourite
NOTIONAL_GUARD = 1.15     # worst-case spend (shares × limit) may exceed bet by at most this
SETTLE_BUFFER_S = 45      # wait this long past a window's end before trying to settle it
SETTLE_POLL_S = 30        # min seconds between settlement sweeps
# Laddered fill (2026-06-09): walk the ask ladder WITHIN [entry_ask, max_ask] (max_ask carried on
# the intent, per-strategy) instead of one FAK that overpays up to 0.92 and abandons on the first
# thin-book shot. Re-attempt across the entry-window budget when the in-band book is momentarily
# dry; accept a clean miss when the ask is above the validated band (a fill there is -EV).
LADDER_MAX_RETRIES = 3       # outer re-attempts after the first laddered try
LADDER_RETRY_SLEEP_S = 4.0   # wait between re-attempts (lets the in-band book refill)

# --- execution-integrity guards (2026-06-09) --------------------------------
# Diagnosed from 4.5 days of live fills (research/analysis/live_gap_attribution.py):
# knife-catch fills (book collapsed below the quote between signal and order) are a -EV
# cohort; 62 API-400 misses were IOCs priced below the real touch (fill_or_chase breaks on
# the IOC error and never advances); dry in-band books sometimes refill a few seconds later;
# intents older than the 10s latency-survival bound were never validated by any backtest.
# Modes: off = legacy path, byte-identical; shadow = run the checks, RECORD the verdict on
# the fill, place anyway; on = enforce. EXEC_GUARDS_ENFORCE_SIDS upgrades named strategies
# to enforcement while the rest stay shadow (the A/B arm). All knobs ride .env so the
# hourly-monitor respawn inherits them.
EXEC_GUARDS_DEFAULT = os.getenv("EXEC_GUARDS", "off").strip().lower()
EXEC_GUARDS_ENFORCE_SIDS = {s.strip() for s in
                            os.getenv("EXEC_GUARDS_ENFORCE_SIDS", "").split(",") if s.strip()}
EXEC_FLOOR_DROP = float(os.getenv("EXEC_FLOOR_DROP", "0.04"))           # best_ask < entry-X => signal dead
EXEC_MAX_INTENT_AGE_S = float(os.getenv("EXEC_MAX_INTENT_AGE_S", "10")) # validated latency bound
EXEC_PREFLIGHT_MIN_DEPTH_FRAC = float(os.getenv("EXEC_PREFLIGHT_MIN_DEPTH_FRAC", "0.5"))
EXEC_DRY_RETRY_S = float(os.getenv("EXEC_DRY_RETRY_S", "3.0"))          # delay between re-checks
# How many delayed re-checks a `dry` verdict gets (1 = the original single re-check, i.e.
# byte-identical legacy behaviour). Measured 2026-07-25 over 14d: the ONE re-check rescued
# 7 of 17 dry verdicts, and ALL 17 were best_ask > ceiling — the price had moved ABOVE
# max_ask, not a thin book (band_depth is 0 whenever the touch is above the ceiling, and
# _preflight labels that `dry`). Waiting for the book to come back INTO the band is exactly
# the bet this edge makes, so give it a few more looks — bounded by time_left, and still
# taker-only (a resting maker bled -$1.99/tr, see the module header).
EXEC_DRY_RETRY_N = int(os.getenv("EXEC_DRY_RETRY_N", "1"))
# --- multi-coin burst cap (BC2, docs/research/BURST_CAPACITY_2026-06-11.md) ----------------
# One macro move fires the disagree signal on several coins at the same window_start_ts and
# the members win/lose together (pair agreement ~85% vs ~50% independence null) — a burst is
# ONE leveraged macro bet. Cap the intents a strategy may CONSUME per window-ts (arrival
# order = the validated keep-first tie-break). 0 = off (byte-identical default). Empty sids
# list = applies to all strategies when the cap is > 0.
EXEC_BURST_CAP = int(os.getenv("EXEC_BURST_CAP", "0"))
EXEC_BURST_CAP_SIDS = {s.strip() for s in
                       os.getenv("EXEC_BURST_CAP_SIDS", "").split(",") if s.strip()}
# --- live symbol allowlist (capacity expansion 2026-07-17) ---------------------------------
# The paper engine now discovers bnb/doge/hype, so live:true strategies emit intents for
# coins with ZERO live-fill validation. Real money only trades symbols validated live;
# new coins graduate here only after their paper forward-test gate passes + user sign-off.
EXEC_SYMBOLS = {s.strip().lower() for s in
                os.getenv("EXEC_SYMBOLS", "btc,eth,sol,xrp").split(",") if s.strip()}
# PER-STRATEGY extension of that allowlist: "sid:sym,sid:sym". A coin graduates for the
# strategy whose paper twin actually earned on it — NOT globally. 2026-07-25: 70 of the last
# 75 hype intents came from det_lwd_live (the break-even probe) and only 5 from the disagree
# family, whose hype cohort is the one with the edge (+$3.60/fill official, CI [+1.81,+5.29]),
# so a global EXEC_SYMBOLS += hype would put the wrong strategy on the new coin at 14x the
# volume of the right one. Empty default => byte-identical to the global-only behaviour.
def _parse_sid_symbols(raw: str) -> dict[str, set[str]]:
    """"sid:sym,sid:sym" -> {sid: {sym}}. Malformed entries are dropped, never fatal:
    a typo here must not widen the allowlist, and must not take the executor down."""
    out: dict[str, set[str]] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        sid, sym = pair.split(":", 1)
        if sid.strip() and sym.strip():
            out.setdefault(sid.strip(), set()).add(sym.strip().lower())
    return out


EXEC_SYMBOLS_EXTRA = _parse_sid_symbols(os.getenv("EXEC_SYMBOLS_EXTRA", ""))


def parse_book(book):
    """(asks, bids) as [(price, size), ...] — asks ascending, bids descending.
    Tolerates the py-clob OrderBookSummary object shape AND plain dicts; string or
    numeric price/size; missing/None book. Unparseable levels are skipped."""
    def _levels(obj, name):
        if obj is None:
            return []
        raw = getattr(obj, name, None)
        if raw is None and isinstance(obj, dict):
            raw = obj.get(name)
        out = []
        for lv in raw or []:
            px = getattr(lv, "price", None)
            sz = getattr(lv, "size", None)
            if px is None and isinstance(lv, dict):
                px, sz = lv.get("price"), lv.get("size")
            try:
                out.append((float(px), float(sz)))
            except (TypeError, ValueError):
                continue
        return out
    asks = sorted(_levels(book, "asks"), key=lambda t: t[0])
    bids = sorted(_levels(book, "bids"), key=lambda t: -t[0])
    return asks, bids


def band_depth(levels, lo, hi):
    """Total shares quoted at prices within [lo, hi]."""
    eps = 1e-9
    return sum(sz for px, sz in levels if lo - eps <= px <= hi + eps)


def _utc_day(ts=None):
    return dt.datetime.fromtimestamp(ts or time.time(), tz=dt.timezone.utc).strftime("%Y-%m-%d")


def _slug_window_end(slug: str) -> int:
    """btc-updown-15m-<window_start_ts(s)> -> window_end epoch seconds."""
    try:
        ws = int(slug.rsplit("-", 1)[1])
        dur = 300 if "-5m-" in slug else 900
        return ws + dur
    except Exception:
        return 0


def _slug_window_ts(slug: str) -> str:
    """btc-updown-15m-1781113500 -> '1781113500' (the cross-coin shared window id)."""
    return slug.rsplit("-", 1)[-1]


_token_cache: dict = {}


def gamma_tokens(slug: str):
    """Resolve slug -> (up_token_id, down_token_id) via Gamma (cached), matching
    the engine's clients/gamma.py mapping (outcomes Up/Yes -> yes, Down/No -> no)."""
    import requests  # lazy: network only
    if slug in _token_cache:
        return _token_cache[slug]
    r = requests.get(f"{GAMMA}/markets", params={"slug": slug}, timeout=8)
    r.raise_for_status()
    data = r.json()
    doc = (data[0] if isinstance(data, list) else data) if data else None
    if not doc:
        return None
    raw = doc.get("clobTokenIds")
    tok = json.loads(raw) if isinstance(raw, str) else raw
    if not tok or len(tok) < 2:
        return None
    outs = doc.get("outcomes")
    outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
    yes_idx, no_idx = 0, 1
    for i, o in enumerate(outs):
        if str(o).lower() in ("up", "yes"):
            yes_idx = i
        elif str(o).lower() in ("down", "no"):
            no_idx = i
    res = (str(tok[yes_idx]), str(tok[no_idx]))
    _token_cache[slug] = res
    return res


def gamma_resolution(slug: str):
    """Official Polymarket resolution for a closed 15m market, via Gamma.

    Returns "UP" or "DOWN" (the winning side) once the market is resolved, or None
    if it isn't resolved yet / can't be read. Reads the market's own outcomePrices
    (1.0 / 0.0) — claim-race-proof: it does NOT depend on whether we've redeemed the
    position, so the hourly claim loop can't make a settled window look unsettled.
    """
    import requests  # lazy: network only
    try:
        # NOTE: resolved markets are only returned with closed=true — the default
        # /markets?slug= filters them out (that's why gamma_tokens, used pre-resolution,
        # omits it but this must include it).
        r = requests.get(f"{GAMMA}/markets", params={"slug": slug, "closed": "true"}, timeout=8)
        r.raise_for_status()
        data = r.json()
        doc = (data[0] if isinstance(data, list) else data) if data else None
    except Exception:
        return None
    if not doc or not doc.get("closed"):
        return None
    prices = doc.get("outcomePrices")
    prices = json.loads(prices) if isinstance(prices, str) else (prices or [])
    outs = doc.get("outcomes")
    outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
    if len(prices) < 2 or len(outs) < 2:
        return None
    try:
        fp = [float(p) for p in prices]
    except (TypeError, ValueError):
        return None
    win_idx = next((i for i, p in enumerate(fp) if p >= 0.99), None)
    if win_idx is None:                       # not a clean 1/0 split yet — retry later
        return None
    o = str(outs[win_idx]).lower()
    if o in ("up", "yes"):
        return "UP"
    if o in ("down", "no"):
        return "DOWN"
    return None


@dataclass
class StrategyBook:
    """Per-strategy live book: independent bankroll balance, daily cap, dedup, pending."""
    strategy_id: str
    done_slugs: set = field(default_factory=set)
    deployed: float = 0.0                      # cumulative real $ ever spent (reporting only)
    realized_total: float = 0.0                # cumulative realized pnl (balance driver)
    realized_by_day: dict = field(default_factory=dict)  # UTC day -> realized pnl (daily cap)
    pending: list = field(default_factory=list)          # filled windows awaiting settlement
    bankroll_usd: float = DEFAULT_BANKROLL_USD
    max_daily_loss_usd: float = DEFAULT_DAILY_CAP_USD
    open: int = 0  # in-flight live orders (NOT persisted; FAK resolves in <2s, resets on restart)
    inflight_slugs: set = field(default_factory=set)  # NOT persisted (mirrors `open`)

    def to_json(self) -> dict:
        return {"done_slugs": sorted(self.done_slugs),
                "deployed": round(self.deployed, 6),
                "realized_total": round(self.realized_total, 6),
                "realized_by_day": {k: round(v, 6) for k, v in self.realized_by_day.items()},
                "pending": self.pending,
                "bankroll_usd": self.bankroll_usd,
                "max_daily_loss_usd": self.max_daily_loss_usd}

    @classmethod
    def from_json(cls, sid: str, d: dict) -> "StrategyBook":
        return cls(strategy_id=sid,
                   done_slugs=set(d.get("done_slugs", [])),
                   deployed=float(d.get("deployed", 0.0)),
                   realized_total=float(d.get("realized_total", 0.0)),
                   realized_by_day=dict(d.get("realized_by_day", {})),
                   pending=list(d.get("pending", [])),
                   bankroll_usd=float(d.get("bankroll_usd", DEFAULT_BANKROLL_USD)),
                   max_daily_loss_usd=float(d.get("max_daily_loss_usd", DEFAULT_DAILY_CAP_USD)))


LEGACY_OWNER = "det_lwd_live"  # the only live strategy before per-strategy books existed


class Executor:
    def __init__(self, clob, mode, state_path=None, fills_path=None, settlements_path=None,
                 guards=None, enforce_sids=None, burst_cap=None, burst_cap_sids=None):
        self.clob = clob
        self.mode = mode                       # dry_run | live
        self.books: dict = {}                  # strategy_id -> StrategyBook (isolated)
        self._last_settle = 0.0
        self._load_failed = False              # refuse to trade if the state file is corrupt
        # Execution-integrity guards: off | shadow | on (None -> .env EXEC_GUARDS).
        g = (guards if guards is not None else EXEC_GUARDS_DEFAULT).strip().lower()
        self.guards = g if g in ("off", "shadow", "on") else "off"
        self.enforce_sids = (set(enforce_sids) if enforce_sids is not None
                             else set(EXEC_GUARDS_ENFORCE_SIDS))
        # Multi-coin burst cap (None -> .env knobs; tests pin explicitly).
        self.burst_cap = int(burst_cap if burst_cap is not None else EXEC_BURST_CAP)
        self.burst_cap_sids = (set(burst_cap_sids) if burst_cap_sids is not None
                               else set(EXEC_BURST_CAP_SIDS))
        # Paths default to the module globals; tests override them with tmp files.
        self._state_path = Path(state_path) if state_path else STATE
        self._fills_path = Path(fills_path) if fills_path else FILLS
        self._settlements_path = Path(settlements_path) if settlements_path else SETTLEMENTS
        self._load_state()

    def _guard_mode(self, sid) -> str:
        """off | shadow | enforce for THIS strategy. Global 'on' enforces everywhere;
        'shadow' enforces only the sids named in EXEC_GUARDS_ENFORCE_SIDS (the A/B arm)."""
        if self.guards == "on":
            return "enforce"
        if self.guards == "shadow":
            return "enforce" if sid in self.enforce_sids else "shadow"
        return "off"

    async def _preflight(self, token_id, entry_ask, ceiling, target_shares):
        """One get_book look before risking an order. Verdicts:
        abort_floor — best ask collapsed below entry_ask - EXEC_FLOOR_DROP: the favourite
                      flipped; a 'cheap' fill here is a knife-catch (-EV cohort, measured);
        dry         — < EXEC_PREFLIGHT_MIN_DEPTH_FRAC of target_shares within [.., ceiling];
        ok          — tradeable; also returns best_ask so the ladder starts at the REAL
                      touch (an IOC below the touch is an API-400, not a fill);
        preflight_error — book unreadable: FAIL OPEN (missed trades are the bigger leak)."""
        try:
            book = await self.clob.get_book(token_id)
            asks, _bids = parse_book(book)
            if not asks:
                return {"verdict": "dry", "best_ask": None, "depth_band": 0.0}
            best_ask = asks[0][0]
            depth = band_depth(asks, 0.0, ceiling)
            if best_ask < entry_ask - EXEC_FLOOR_DROP:
                return {"verdict": "abort_floor", "best_ask": best_ask, "depth_band": depth}
            if depth < target_shares * EXEC_PREFLIGHT_MIN_DEPTH_FRAC:
                return {"verdict": "dry", "best_ask": best_ask, "depth_band": depth}
            return {"verdict": "ok", "best_ask": best_ask, "depth_band": depth}
        except Exception as e:  # guards must never raise — fail open, record why
            return {"verdict": "preflight_error", "best_ask": None, "depth_band": None,
                    "error": str(e)[:80]}

    def _book(self, sid, *, bankroll=None, daily_cap=None) -> "StrategyBook":
        """Fetch (or lazily create) a strategy's book. Caps are refreshed from the intent
        (strategies.yaml is the source of truth); an unknown sid gets a fresh, isolated book
        with fallback caps rather than erroring or polluting another strategy."""
        b = self.books.get(sid)
        if b is None:
            b = StrategyBook(
                strategy_id=sid,
                bankroll_usd=float(bankroll) if bankroll is not None else DEFAULT_BANKROLL_USD,
                max_daily_loss_usd=float(daily_cap) if daily_cap is not None else DEFAULT_DAILY_CAP_USD)
            self.books[sid] = b
            log.info("strategy_book_created", strategy_id=sid,
                     bankroll=b.bankroll_usd, daily_cap=b.max_daily_loss_usd)
        else:
            if bankroll is not None:
                b.bankroll_usd = float(bankroll)
            if daily_cap is not None:
                b.max_daily_loss_usd = float(daily_cap)
        return b

    def global_open(self) -> int:
        return sum(b.open for b in self.books.values())

    def _load_state(self):
        if not self._state_path.exists():
            return
        try:
            s = json.loads(self._state_path.read_text())
        except Exception as e:
            # Refuse to trade on an unreadable state file: an empty in-memory state would
            # re-trade already-settled windows and reset the loss caps. Hard-block instead.
            self._load_failed = True
            log.error("state_unreadable_refusing_to_trade", err=str(e), path=str(self._state_path))
            return
        if isinstance(s, dict) and s.get("version") == STATE_VERSION and "strategies" in s:
            self.books = {sid: StrategyBook.from_json(sid, d) for sid, d in s["strategies"].items()}
            return
        # Legacy flat schema (no "version", top-level done_slugs/realized_total) -> migrate.
        self._migrate_flat_v1(s)

    def _migrate_flat_v1(self, s: dict):
        """Fold the pre-per-strategy flat state into the det_lwd_live book (the only live
        strategy that existed then). Backs up the original first; writes v2 (idempotent)."""
        import shutil
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self._state_path.with_name(f"executor_state.flatv1.{stamp}.bak")
        if not backup.exists():
            shutil.copy2(self._state_path, backup)
        log.warning("migrating_flat_state_to_v2", owner=LEGACY_OWNER, backup=str(backup),
                    done_slugs=len(s.get("done_slugs", [])), realized_total=s.get("realized_total"))
        b = StrategyBook.from_json(LEGACY_OWNER, {
            "done_slugs": s.get("done_slugs", []),
            "deployed": s.get("deployed", 0.0),
            "realized_total": s.get("realized_total", 0.0),
            "realized_by_day": s.get("realized_by_day", {}),
            "pending": [{**p, "strategy_id": LEGACY_OWNER} for p in s.get("pending", [])],
            "bankroll_usd": DEFAULT_BANKROLL_USD,
            "max_daily_loss_usd": DEFAULT_DAILY_CAP_USD,
        })
        self.books = {LEGACY_OWNER: b}
        self._save_state()  # commit v2; next load sees version==2 and skips migration
        log.info("migration_complete", strategies=list(self.books),
                 realized_total=round(b.realized_total, 6))

    def _save_state(self):
        payload = {"version": STATE_VERSION,
                   "deployed_total": round(sum(b.deployed for b in self.books.values()), 6),
                   "strategies": {sid: b.to_json() for sid, b in self.books.items()}}
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(self._state_path)

    def _unsettled_ended(self, b: "StrategyBook") -> int:
        """Count THIS strategy's pending windows that have ENDED but we couldn't settle yet.
        While >0 the strategy's realized P&L is understated, so we pause its new orders until
        they clear — a stuck loss can't sneak it past its loss caps."""
        now = time.time()
        return sum(1 for p in b.pending
                   if now - _slug_window_end(p["slug"]) > SETTLE_BUFFER_S)

    def _blocked(self, intent) -> str | None:
        # ---- GLOBAL gates (shared by all strategies) ----
        if self._load_failed:
            return "state load failed; refusing to trade (manual check required)"
        if _killed(self.mode):
            return "KILL/EXEC_KILL switch present"
        slug = intent["slug"]
        sym = slug.split("-", 1)[0].lower()
        if (sym not in EXEC_SYMBOLS
                and sym not in EXEC_SYMBOLS_EXTRA.get(intent.get("strategy_id") or "", ())):
            return f"symbol {sym} not in live allowlist (paper-validation only)"
        tl = _slug_window_end(slug) - int(time.time())
        if tl < TIME_LEFT_MIN:
            return f"time_left {tl}s < {TIME_LEFT_MIN}s (no room to fill)"
        # ---- resolve this intent's strategy book (caps from the intent, with fallbacks) ----
        sid = intent.get("strategy_id") or "unknown"
        bankroll = float(intent.get("bankroll_usd", DEFAULT_BANKROLL_USD))
        daily_cap = float(intent.get("max_daily_loss_usd", DEFAULT_DAILY_CAP_USD))
        b = self._book(sid, bankroll=bankroll, daily_cap=daily_cap)
        # ---- guard: stale-intent latency gate (10s is the validated survival bound;
        #      anything older was never blessed by a backtest) ----
        ts_ms = intent.get("ts_ms")
        if ts_ms:
            age = time.time() - float(ts_ms) / 1000.0
            if age > EXEC_MAX_INTENT_AGE_S:
                gm = self._guard_mode(sid)
                if gm == "enforce":
                    return (f"[{sid}] intent age {age:.1f}s > {EXEC_MAX_INTENT_AGE_S:.0f}s "
                            f"(stale signal)")
                if gm == "shadow":
                    log.info("guard_would_drop_stale", strategy_id=sid, slug=slug,
                             intent_age_s=round(age, 1))
        # ---- PER-STRATEGY gates (isolated: one strategy never blocks another) ----
        if slug in b.done_slugs:
            return f"[{sid}] already traded this window"
        # ---- guard: multi-coin burst cap (same strategy, same window_start_ts) ----
        # Counts CONSUMED intents (done_slugs is appended on ANY attempt, fill or clean
        # miss) + in-flight siblings — exactly the intent-level cap the backtest scored.
        if self.burst_cap > 0 and (not self.burst_cap_sids or sid in self.burst_cap_sids):
            wts = _slug_window_ts(slug)
            consumed = (sum(1 for s in b.done_slugs if _slug_window_ts(s) == wts)
                        + sum(1 for s in b.inflight_slugs if _slug_window_ts(s) == wts))
            if consumed >= self.burst_cap:
                return (f"[{sid}] burst cap: {consumed} sibling intent(s) already consumed "
                        f"for window-ts {wts} (EXEC_BURST_CAP={self.burst_cap})")
        # Bankroll = balance / max-loss. Stop this strategy for good once its cumulative
        # realized loss eats its whole bankroll (its wins have credited back along the way).
        if b.realized_total <= -b.bankroll_usd + 1e-9:
            return (f"[{sid}] bankroll exhausted "
                    f"(realized ${b.realized_total:+.2f} <= -${b.bankroll_usd:.0f})")
        # Per-UTC-day loss cap: pause this strategy until the next 00:00 UTC.
        today = _utc_day()
        day_pnl = b.realized_by_day.get(today, 0.0)
        if day_pnl <= -b.max_daily_loss_usd + 1e-9:
            return (f"[{sid}] daily loss cap hit (today ${day_pnl:+.2f} "
                    f"<= -${b.max_daily_loss_usd:.0f}); resumes 00:00 UTC")
        # Fail-safe: don't open new risk for this strategy while its realized P&L is
        # understated by an ended-but-unsettled window (could be a loss that breaches a cap).
        ue = self._unsettled_ended(b)
        if ue:
            return f"[{sid}] awaiting settlement of {ue} ended window(s) before risking more"
        if b.open >= PER_STRAT_MAX_CONCURRENT:
            return f"[{sid}] max concurrent {PER_STRAT_MAX_CONCURRENT}"
        # ---- GLOBAL concurrency ceiling (shared-wallet collateral guard) ----
        if self.global_open() >= GLOBAL_MAX_CONCURRENT:
            return f"global max concurrent {GLOBAL_MAX_CONCURRENT} (shared-wallet ceiling)"
        return None

    async def handle(self, intent):
        slug, side = intent["slug"], intent.get("side")
        why = self._blocked(intent)
        if why:
            log.info("intent_skipped", slug=slug, side=side, reason=why)
            return
        sid = intent.get("strategy_id") or "unknown"
        b = self._book(sid,
                       bankroll=float(intent.get("bankroll_usd", DEFAULT_BANKROLL_USD)),
                       daily_cap=float(intent.get("max_daily_loss_usd", DEFAULT_DAILY_CAP_USD)))
        toks = gamma_tokens(slug)
        if not toks:
            log.warning("token_resolve_failed", slug=slug)
            return
        up_tok, down_tok = toks
        token_id = up_tok if side == "UP" else down_tok
        ask = float(intent["entry_ask"])
        bet = float(intent.get("bet_usd", 5.0))
        if not (0.01 <= ask <= ABS_MAX_PRICE):
            log.warning("intent_skipped", slug=slug, side=side,
                        reason=f"implausible entry_ask {ask}")
            return
        # Laddered fill capped at the strategy's OWN validated band ceiling (max_ask from the
        # intent), never the 0.92 hardcode: filling a favourite above max_ask is -EV. If the
        # quoted ask is already above the band, skip cleanly (correctly-skip-above-band).
        ceiling = round(min(float(intent.get("max_ask", ABS_MAX_PRICE)), ABS_MAX_PRICE), 2)
        if ask > ceiling + 1e-9:
            log.info("intent_skipped", slug=slug, side=side,
                     reason=f"quoted ask {ask:.2f} > max_ask {ceiling:.2f} (-EV band)")
            return
        target_shares = round(bet / ask, 2)
        # Hard notional guard against the CEILING (worst-case spend), belt-and-suspenders on the cap.
        max_shares = (bet * NOTIONAL_GUARD) / ceiling
        if target_shares > max_shares:
            target_shares = round(max_shares, 2)
        rec = {"ts": time.time(), "strategy_id": sid, "slug": slug, "side": side,
               "token_id": token_id, "quoted_ask": ask, "max_ask": ceiling,
               "target_shares": target_shares, "bet_usd": bet, "mode": self.mode}

        if self.mode == "dry_run":
            log.info("DRY_RUN_would_place", strategy_id=sid, **{k: rec[k] for k in
                     ("slug", "side", "token_id", "quoted_ask", "max_ask", "target_shares")})
            b.done_slugs.add(slug)
            self._append_fill({**rec, "dry_run": True})
            self._save_state()
            return

        # ---- LIVE: laddered fill within [entry_ask, max_ask], retry across the entry budget ----
        window_end = _slug_window_end(slug)
        t0 = time.time()
        gm = self._guard_mode(sid)
        guard = None
        cur_ask = round(ask, 2)
        if gm != "off":
            guard = await self._preflight(token_id, ask, ceiling, target_shares)
            guard["mode"] = gm
            if gm == "enforce":
                # the in-band book often comes BACK within seconds (the 17/17 dry verdicts
                # were all "touch above our ceiling", not an empty book) — re-check on a
                # delay instead of IOC-spamming a dry ladder. N=1 is the legacy behaviour.
                retries = 0
                while (guard["verdict"] == "dry" and retries < EXEC_DRY_RETRY_N
                       and window_end - time.time() - EXEC_DRY_RETRY_S > TIME_LEFT_MIN):
                    await asyncio.sleep(EXEC_DRY_RETRY_S)
                    guard = await self._preflight(token_id, ask, ceiling, target_shares)
                    guard["mode"] = gm
                    retries += 1
                    guard["retried"] = True
                    guard["retries"] = retries
                if guard["verdict"] in ("abort_floor", "dry"):
                    note = ("guard:floor_abort" if guard["verdict"] == "abort_floor"
                            else "guard:dry_after_retry")
                    b.done_slugs.add(slug)            # clean miss — intent consumed
                    log.warning("guard_skip", strategy_id=sid, slug=slug, side=side,
                                note=note, **{k: guard.get(k) for k in
                                              ("verdict", "best_ask", "depth_band")})
                    self._append_fill({**rec, "dry_run": False, "ok": False,
                                       "filled_shares": 0.0, "usdc_paid": 0.0,
                                       "avg_price": 0.0, "slippage_vs_quote": None,
                                       "fill_ratio": 0.0, "attempts": 0, "rounds": 0,
                                       "latency_ms": int((time.time() - t0) * 1000),
                                       "note": note, "guard": guard})
                    self._save_state()
                    return
                if guard["verdict"] == "ok" and guard.get("best_ask"):
                    # start the ladder at the REAL touch: an IOC below the touch is an
                    # API-400 ("no orders found to match"), not a fill — fill_or_chase
                    # breaks on the error and never advances (the $69 missed-EV bucket)
                    cur_ask = min(ceiling, max(cur_ask, round(guard["best_ask"], 2)))
            else:
                if guard["verdict"] in ("abort_floor", "dry"):
                    log.info("guard_shadow_verdict", strategy_id=sid, slug=slug, side=side,
                             **{k: guard.get(k) for k in ("verdict", "best_ask", "depth_band")})
        try:
            tick = await self.clob.get_tick_size(token_id)
        except Exception:
            tick = "0.01"
        tickf = float(tick) or 0.01
        agg_shares = 0.0
        agg_usdc = 0.0
        attempts = 0          # internal IOCs (each fill_or_chase may place several)
        rounds = 0            # outer re-attempts across the budget
        empties = 0           # consecutive rounds that filled nothing (book dry in-band)
        stop_note = ""
        # enforced: first attempt + ONE delayed retry max (the pre-flight already vetted
        # the book; more rounds only chase a deteriorating window)
        max_rounds = 2 if gm == "enforce" else 1 + LADDER_MAX_RETRIES
        b.open += 1
        b.inflight_slugs.add(slug)
        try:
            while True:
                remaining = target_shares - agg_shares
                if remaining * max(cur_ask, 0.01) < 1.0:      # < $1 left to deploy → done
                    stop_note = "filled"; break
                max_ticks = max(0, int(round((ceiling - cur_ask) / tickf)))
                af = await self.clob.fill_or_chase(
                    token_id=token_id, side="BUY", target_price=cur_ask,
                    target_size=remaining, price_ceiling=ceiling,
                    max_chase_ticks=max_ticks, tick_size=tick)
                rounds += 1
                attempts += int(af.attempts)
                agg_shares += float(af.total_shares)
                agg_usdc += float(af.total_usdc)
                stop_note = af.stopped_reason or ""
                if agg_shares >= target_shares * 0.95:
                    stop_note = "filled"; break
                empties = empties + 1 if af.total_shares <= 0 else 0
                if empties >= 2:                              # book persistently dry in-band
                    stop_note = "book dry in-band"; break
                tl = window_end - time.time()
                if tl <= TIME_LEFT_MIN or rounds >= max_rounds:
                    stop_note = f"budget end (tl={tl:.0f}s, rounds={rounds})"; break
                await asyncio.sleep(LADDER_RETRY_SLEEP_S)
                if af.total_shares <= 0 and gm != "off":
                    # RE-QUOTE before spending another round. The pre-round-1 touch-bump above
                    # exists because an IOC below the touch is an API-400 ("no orders found to
                    # match"), not a fill — and clob_trade breaks the inner ladder on that error
                    # WITHOUT advancing a tick. That bump was never redone for later rounds, so a
                    # zero-fill round re-fired the same known-bad price 4s later: 36 of 115 rounds
                    # died this way over the 14d to 2026-07-25. Same formula as round 1.
                    rq = await self._preflight(token_id, ask, ceiling, target_shares)
                    if rq["verdict"] == "abort_floor":
                        stop_note = "requote:floor_abort"; break   # book collapsed = knife cohort
                    if rq.get("best_ask"):
                        cur_ask = min(ceiling, max(cur_ask, round(rq["best_ask"], 2)))
        finally:
            b.open -= 1
            b.inflight_slugs.discard(slug)
        latency_ms = int((time.time() - t0) * 1000)
        b.done_slugs.add(slug)                                 # one intent per window — consumed
        filled = agg_shares                                    # shares received (balance-poll truth)
        usdc = agg_usdc                                        # usdc paid (aggregate across rounds)
        avg = (usdc / filled) if filled > 0 else 0.0
        ok = bool(filled > 0)
        # post-fill observability: a fill far below the quoted ask means the book collapsed
        # THROUGH the order — the measured -EV knife-catch cohort (no auto-liquidation;
        # selling a binary minutes from resolution just pays the spread again)
        knife = bool(ok and avg < ask - EXEC_FLOOR_DROP)
        if ok:
            b.deployed += usdc
            # Track this window for settlement: when it resolves, its pnl credits
            # (win) or debits (loss) THIS strategy's balance + that UTC day's bucket.
            b.pending.append({"strategy_id": sid, "slug": slug, "side": side,
                              "usdc": round(usdc, 6), "shares": round(filled, 6)})
        fill = {**rec, "dry_run": False, "ok": ok, "filled_shares": round(filled, 3),
                "usdc_paid": round(usdc, 4), "avg_price": round(avg, 4),
                "slippage_vs_quote": (round(avg - ask, 4) if filled > 0 else None),
                "fill_ratio": round(filled / max(target_shares, 1e-9), 3),
                "attempts": attempts, "rounds": rounds,
                "latency_ms": latency_ms, "note": stop_note[:90],
                "knife_catch": knife}
        if guard is not None:
            fill["guard"] = guard
        if knife:
            log.warning("knife_catch_fill", strategy_id=sid, slug=slug, side=side,
                        quoted_ask=ask, avg_price=round(avg, 4), guards=gm)
        log.info("LIVE_fill", strategy_id=sid, slug=slug, side=side, ok=ok, avg_price=round(avg, 4),
                 filled=round(filled, 3), usdc=round(usdc, 4), attempts=attempts, rounds=rounds,
                 slippage=(round(avg - ask, 4) if filled > 0 else None),
                 latency_ms=latency_ms, note=stop_note[:60])
        self._append_fill(fill)
        self._save_state()

    def _append_fill(self, rec):
        self._fills_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._fills_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _append_settlement(self, sid, p, outcome, won, pnl, realized_after, window_end):
        """Append-only per-settlement ledger (the status skill reads it for Israel-local-day
        P&L). NEVER read back by the executor, so a corrupt line can't affect trading."""
        self._settlements_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(), "window_end": window_end, "strategy_id": sid,
               "slug": p["slug"], "side": p["side"], "outcome": outcome, "won": won,
               "usdc": p["usdc"], "shares": p["shares"], "pnl": round(pnl, 6),
               "realized_total_after": round(realized_after, 6)}
        with open(self._settlements_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def settle_pending(self):
        """Resolve ended pending windows across ALL strategy books and fold each window's
        realized pnl into ITS strategy's balance + UTC-day bucket (keyed by the window's END
        day, so a loss is booked to the day it actually happened). Also appends a
        settlements.jsonl ledger line. Throttled by SETTLE_POLL_S; windows not yet resolved
        on Gamma stay pending and retry next sweep."""
        now = time.time()
        has_pending = any(b.pending for b in self.books.values())
        if now - self._last_settle < SETTLE_POLL_S or not has_pending:
            return
        self._last_settle = now
        changed = False
        for sid, b in self.books.items():
            still = []
            for p in b.pending:
                wend = _slug_window_end(p["slug"])
                if now - wend <= SETTLE_BUFFER_S:
                    still.append(p); continue       # too soon to read resolution
                res = gamma_resolution(p["slug"])
                if res is None:
                    still.append(p); continue       # unresolved/unreadable — retry later
                self._apply_settlement(sid, b, p, res, wend)
                changed = True
            b.pending = still
        if changed:
            self._save_state()

    def _apply_settlement(self, sid, b, p, res, wend):
        """Pure booking: fold one resolved window's pnl into ITS strategy's balance + UTC-day
        bucket + the settlements ledger. `res` is the winning side ('UP'/'DOWN'). Separated
        from the Gamma fetch so the booking math is unit-testable without network."""
        won = (res == p["side"])
        pnl = (p["shares"] - p["usdc"]) if won else (-p["usdc"])
        day = _utc_day(wend)
        b.realized_total += pnl
        b.realized_by_day[day] = b.realized_by_day.get(day, 0.0) + pnl
        self._append_settlement(sid, p, res, won, pnl, b.realized_total, wend)
        log.info("settled", strategy_id=sid, slug=p["slug"], side=p["side"], outcome=res,
                 won=won, pnl=round(pnl, 4), realized_total=round(b.realized_total, 2),
                 day=day, day_pnl=round(b.realized_by_day[day], 2))
        return won, pnl, day


async def place_cancel_test(clob):
    """Validate signing/POST end-to-end with a NON-MARKETABLE FAK that cannot fill."""
    import requests  # lazy: network only
    # find one live 15m market to get a real token id (discovery uses /events)
    r = requests.get(f"{GAMMA}/events",
                     params={"closed": "false", "active": "true", "order": "startDate",
                             "ascending": "false", "limit": 100}, timeout=10)
    r.raise_for_status()
    slug = None
    for ev in r.json():
        s = ev.get("slug", "")
        mk = (ev.get("markets") or [{}])[0]
        if "-updown-15m-" in s and mk.get("clobTokenIds"):
            slug = s
            break
    if not slug:
        # fall back: ask the user / engine intents for a slug
        log.error("no_live_15m_market_found_for_test")
        return
    toks = gamma_tokens(slug)
    log.info("place_cancel_test_market", slug=slug, up_token=toks[0][:12] + "...")
    fill = await clob.place_ioc(token_id=toks[0], side="BUY", price=0.01, size=5.0,
                                settlement_wait_seconds=3.0)
    log.info("place_cancel_test_result", success=fill.success, status=fill.status,
             order_id=fill.order_id, filled=fill.making_amount, error=fill.error,
             note="price 0.01 is non-marketable; making_amount should be 0 (no fill, $0 spent)")


def make_client():
    # Lazy import: the SDK (py-clob-client-v2, Python 3.9.10+) is only needed for the
    # order-placing paths, so importing it here keeps the module unit-testable without it.
    sys.path.insert(0, str(REPO / "src" / "mean_reversion_live" / "live"))
    import clob_trade  # noqa: E402  (vendored, standalone)
    pk = os.environ["POLYMARKET_PRIVATE_KEY"]
    proxy = os.environ["POLYMARKET_PROXY_ADDRESS"]
    host = os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com")
    sig = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "2"))
    return clob_trade.ClobTradeClient(private_key=pk, proxy_address=proxy, host=host,
                                      signature_type=sig)


async def run_loop(mode):
    clob = make_client() if mode == "live" else None
    ex = Executor(clob, mode)
    today = _utc_day()
    # Per-book resumed state (restart-safe: realized_by_day is reconstructed from the state
    # file, so a mid-day restart does NOT reset a strategy's daily-loss cap).
    books_summary = {sid: {"realized_total": round(b.realized_total, 2),
                           "today_pnl": round(b.realized_by_day.get(today, 0.0), 2),
                           "bankroll": b.bankroll_usd, "daily_cap": b.max_daily_loss_usd,
                           "pending": len(b.pending)}
                     for sid, b in ex.books.items()}
    log.info("executor_started", mode=mode, time_left_min=TIME_LEFT_MIN,
             default_bankroll=DEFAULT_BANKROLL_USD, default_daily_cap=DEFAULT_DAILY_CAP_USD,
             global_max_concurrent=GLOBAL_MAX_CONCURRENT, books=books_summary,
             guards=ex.guards, guards_enforce_sids=sorted(ex.enforce_sids),
             guard_knobs={"floor_drop": EXEC_FLOOR_DROP,
                          "max_intent_age_s": EXEC_MAX_INTENT_AGE_S,
                          "min_depth_frac": EXEC_PREFLIGHT_MIN_DEPTH_FRAC,
                          "dry_retry_s": EXEC_DRY_RETRY_S,
                          "dry_retry_n": EXEC_DRY_RETRY_N},
             symbols=sorted(EXEC_SYMBOLS),
             symbols_extra={k: sorted(v) for k, v in sorted(EXEC_SYMBOLS_EXTRA.items())},
             burst_cap=ex.burst_cap, burst_cap_sids=sorted(ex.burst_cap_sids),
             intents=str(INTENTS))
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    INTENTS.touch(exist_ok=True)

    def _complete_lines():
        # newline-terminated lines only; a partial last line (mid-write) is
        # excluded and picked up once finished. NOTE: never use f.tell() inside
        # `for line in f` — Python disables it (the bug that crashed v1).
        try:
            return INTENTS.read_text().split("\n")[:-1]
        except OSError:
            return []

    # start at end: only act on intents that arrive AFTER we start
    processed = len(_complete_lines())
    while True:
        if _killed(ex.mode):
            log.warning("kill_switch_halt"); break
        lines = _complete_lines()
        for line in lines[processed:]:
            line = line.strip()
            if not line:
                continue
            try:
                intent = json.loads(line)
            except Exception:
                continue
            try:
                await ex.handle(intent)          # per-intent isolation: one bad
            except Exception as e:                # intent must never kill the loop
                log.error("intent_handle_error", err=str(e), line=line[:140])
        processed = len(lines)
        try:
            ex.settle_pending()                  # fold resolved windows into the balance
        except Exception as e:
            log.error("settle_error", err=str(e))
        await asyncio.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="(default) place nothing")
    g.add_argument("--place-cancel-test", action="store_true",
                   help="one real non-marketable FAK to validate the order path")
    g.add_argument("--live", action="store_true",
                   help="REAL MONEY: place FAK orders (size + caps per strategy, from each intent)")
    args = ap.parse_args()

    if args.place_cancel_test:
        asyncio.run(place_cancel_test(make_client()))
    elif args.live:
        asyncio.run(run_loop("live"))
    else:
        asyncio.run(run_loop("dry_run"))


if __name__ == "__main__":
    main()
