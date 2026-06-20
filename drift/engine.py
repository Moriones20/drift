"""Generic multi-strategy engine (Step 32b, D050-D058).

The engine is the "dumb engine, smart strategy" heart of the platform (D051):
it knows nothing about session windows, the daily lull, skip-Friday or rollover.
It only knows when a candle closes.  Each strategy declares the timeframes it
cares about; the engine wakes on the union of those candle-close boundaries
(driven by each strategy's ``next_wake``, D058), serves market data through a
per-tick deduplicated :class:`EngineMarketData`, builds a per-strategy
:class:`StrategyContext`, calls ``on_bar`` and obeys the returned
:class:`Decision`.

All risk, sizing, attribution-by-magic, execution, logging, notification and the
post-close broker delay live here (D051/D052/D053).  The strategy only expresses
intent.

This module orchestrates the existing hardened building blocks — ``executor``,
``db``, ``risk``, ``mt5_client`` — it never reimplements sizing/execution/close.
The behaviour preserved 1:1 from the old ``main.py`` loop: closed-bar filtering
(D045), the rollover-settle delay (D044), the reward-after-spread guard and order
retries (D046/D040), settled balance at close (D048), the shared ``_trade_lock``
with the monitoring thread (D031), per-pair/per-position isolated exception
handling, and the start-of-session server-offset re-derivation (D041/D047).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from drift.config import DriftConfig, StrategyRiskConfig
from drift.db import (
    close_trade_record,
    evaluate_strategy_drawdown,
    get_connection,
    get_open_trades,
    get_realized_pnl_since,
    get_strategy_state,
    get_trade_by_ticket,
    log_event,
    log_peak_balance,
    log_signal,
    log_trade,
    set_strategy_paused,
)
from drift.executor import close_trade, get_open_positions, open_trade
from drift.mt5_client import (
    get_balance,
    get_candles,
    get_equity,
    get_server_utc_offset,
    server_now,
)
from drift.risk import (
    calculate_position_size_allocated,
    check_period_pnl,
    check_strategy_risk,
)
from drift.strategies.base import Decision, Signal, StrategyContext
from drift.strategies.base import get_strategy as _get_strategy

logger = logging.getLogger(__name__)

# Normal post-close delay before evaluating/executing a candle (D044/D045).
CANDLE_CLOSE_DELAY_SECONDS = 5

# A dispatch whose boundary is more than this far after the strategy's previous
# dispatch follows a sleep gap (the engine ticks every candle interval inside a
# session, so consecutive boundaries are minutes apart).  The first dispatch after
# such a gap is the start of a new session window — used to send the calm
# session-start notification (D057).  One hour comfortably exceeds any single
# timeframe step while staying well below the multi-hour idle between sessions.
_SESSION_GAP = timedelta(hours=1)


# Stop-aware sleep slice: the engine never blocks longer than this between
# checks of state.stop_requested / the shutdown event.
_SLEEP_SLICE_SECONDS = 10.0


# ---------------------------------------------------------------------------
# Market data accessor (D045, D051) — per-tick deduplicated candle fetch
# ---------------------------------------------------------------------------


class EngineMarketData:
    """Per-tick candle accessor implementing the ``MarketData`` protocol.

    Serves only CLOSED candles (D045): rows strictly before the current tick's
    ``bar_close_time`` boundary, so the still-forming bar is never handed to a
    strategy.  Fetches are deduplicated per ``(pair, timeframe)`` for the life of
    one tick: multiple strategies or multiple ``candles`` calls in the same tick
    reuse a single MT5 fetch.  Construct a fresh instance per tick (or call
    :meth:`reset`) so the cache does not leak across boundaries.
    """

    def __init__(self, bar_close_time: datetime) -> None:
        self._bar_close_time = bar_close_time
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    def reset(self) -> None:
        """Clear the per-tick cache (e.g. to reuse the instance for a new tick)."""
        self._cache.clear()

    def candles(self, pair: str, timeframe: str, count: int) -> pd.DataFrame:
        """Return the last *count* CLOSED candles for ``(pair, timeframe)``.

        The closed-bar filter (``index < bar_close_time``) drops the still-forming
        bar (D045).  Results are cached by ``(pair, timeframe)`` for this tick; the
        cached frame is the raw fetch, so the filter is applied on every call cheaply.
        """
        key = (pair, timeframe)
        df = self._cache.get(key)
        if df is None:
            df = get_candles(pair, timeframe, count=count)
            self._cache[key] = df
        return df[df.index < self._bar_close_time]


# ---------------------------------------------------------------------------
# Hosted strategy record
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class _HostedStrategy:
    """A strategy instance plus the engine-side state the engine tracks for it.

    ``eq=False`` keeps identity-based equality/hashing so instances can be used
    as dict keys (the scheduler maps each hosted strategy to its wake time) even
    though the record is mutable (``paused`` flips at runtime).
    """

    instance: object  # the Strategy implementation
    name: str
    magic: int  # effective magic = system.magic_number + magic_offset
    allocation_pct: float
    risk: StrategyRiskConfig
    paused: bool = False  # engine-side pause flag (own brake or /pause <name>)
    last_dispatch_boundary: datetime | None = None  # boundary of the previous tick


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class Engine:
    """Hosts N strategies on one MT5 account and drives the main trading loop.

    The engine is constructed once in ``main()`` with the shared process state
    (config, bot state, peak-equity reference, Telegram app, server offset,
    notification/fire-and-forget hooks, the trade lock shared with the monitoring
    thread, and the shutdown event) and then ``run()`` blocks until stop.
    """

    def __init__(
        self,
        config: DriftConfig,
        state,
        peak_balance_ref: list[float],
        bot_app,
        server_offset: timedelta,
        trade_lock: threading.Lock,
        shutdown_event: threading.Event,
        fire_and_forget,
        notify_trade_opened,
        notify_trade_closed,
        notify_bot_status,
        notify_error,
    ) -> None:
        self.config = config
        self.state = state
        self.peak_balance_ref = peak_balance_ref
        self.bot_app = bot_app
        self.server_offset = server_offset
        self._trade_lock = trade_lock
        self.shutdown_event = shutdown_event
        self._fire_and_forget = fire_and_forget
        self._notify_trade_opened = notify_trade_opened
        self._notify_trade_closed = notify_trade_closed
        self._notify_bot_status = notify_bot_status
        self._notify_error = notify_error

        self.strategies: list[_HostedStrategy] = self._build_strategies(config)

    # -- construction ------------------------------------------------------

    def _build_strategies(self, config: DriftConfig) -> list[_HostedStrategy]:
        """Instantiate every ENABLED strategy from config (D054).

        Resolves each strategy name against the registry — this is the deferred
        name validation (C2/D054): an unknown name raises a clear error at
        startup.  Each strategy is built with its own pairs and parsed params,
        and the engine records its effective magic, allocation, risk budget and
        a pause flag.
        """
        hosted: list[_HostedStrategy] = []
        base_magic = config.system.magic_number
        for name, instance_cfg in config.strategies.items():
            if not instance_cfg.enabled:
                continue
            strategy_cls = _get_strategy(name)  # raises ValueError on unknown name
            instance = self._instantiate(name, strategy_cls, instance_cfg)
            magic = base_magic + instance_cfg.magic_offset
            hosted.append(
                _HostedStrategy(
                    instance=instance,
                    name=name,
                    magic=magic,
                    allocation_pct=instance_cfg.allocation_pct,
                    risk=instance_cfg.risk,
                )
            )
            logger.info(
                "Hosting strategy %r: magic=%d allocation=%.0f%% pairs=%s",
                name,
                magic,
                instance_cfg.allocation_pct,
                instance_cfg.pairs,
            )
        return hosted

    @staticmethod
    def _instantiate(name: str, strategy_cls, instance_cfg):
        """Build one strategy instance, parsing its opaque params (D054).

        The engine does not understand a strategy's params; each strategy class
        exposes a ``Params.from_dict`` parser.  Known strategies (the Daily Lull)
        take ``(pairs, params)``; the params object is built from the strategy's
        own dataclass.
        """
        params_dict = instance_cfg.params
        params_factory = getattr(strategy_cls, "params_cls", None)
        if params_factory is not None:
            params = params_factory.from_dict(params_dict)
            return strategy_cls(pairs=instance_cfg.pairs, params=params)

        # Daily Lull (and strategies following its pattern) parse params via a
        # module-level dataclass; resolve it from the class module.
        from drift.strategies.daily_lull import DailyLullParams, DailyLullStrategy

        if strategy_cls is DailyLullStrategy:
            params = DailyLullParams.from_dict(params_dict)
            return strategy_cls(pairs=instance_cfg.pairs, params=params)

        raise ValueError(
            f"Strategy {name!r} ({strategy_cls!r}) has no known params parser; "
            f"add a 'params_cls' attribute exposing 'from_dict'."
        )

    # -- scheduling (D058) -------------------------------------------------

    def _refresh_pause_flags(self) -> None:
        """Sync each hosted strategy's ``paused`` flag from ``strategy_state``.

        The DB is the source of truth for per-strategy pause (D053/D056): the
        monitoring thread sets it on a per-strategy drawdown brake, and Telegram
        /pause /resume will set it (Step 34).  Reading it once per cycle (before
        scheduling) lets a pause set in another context exclude the strategy from
        wakes, and a resume re-include it.  A missing row means "never paused".
        The read is taken under ``_trade_lock`` to avoid racing the monitoring
        thread's write of the same table.
        """
        with self._trade_lock, get_connection() as db_conn:
            for hosted in self.strategies:
                row = get_strategy_state(db_conn, hosted.name)
                hosted.paused = bool(row["paused"]) if row else False

    def _active_strategies(self) -> list[_HostedStrategy]:
        """All hosted strategies, including paused ones.

        Paused strategies remain in the wake schedule so that time-stops
        (close / close_all decisions) fire even during a kill switch (D064).
        Only ``open`` decisions are blocked — see ``_handle_decision``.
        """
        return self.strategies

    def _next_boundary(self, now: datetime) -> datetime | None:
        """Return the minimum ``next_wake`` across all strategies, or None.

        None means no strategy has a scheduled wake right now (all sleeping
        indefinitely); the caller sleeps a slice and re-asks.
        """
        boundary, _ = self._due_strategies(now)
        return boundary

    def _due_strategies(self, now: datetime) -> tuple[datetime | None, list[_HostedStrategy]]:
        """Return the next boundary and the strategies due exactly at it.

        The next boundary is the minimum ``next_wake`` across all strategies;
        the due list is every strategy whose ``next_wake`` equals that
        boundary.  Computing the due set HERE — from a ``now`` strictly before the
        boundary — is what makes dispatch robust: after the engine sleeps past the
        boundary, ``next_wake`` would already point at the FOLLOWING boundary, so
        a post-sleep re-match would skip the strategy.  The caller captures this
        set before sleeping.
        """
        wakes: dict[_HostedStrategy, datetime] = {
            h: w for h in self._active_strategies() if (w := h.instance.next_wake(now)) is not None
        }
        if not wakes:
            return None, []
        boundary = min(wakes.values())
        due = [h for h, w in wakes.items() if w == boundary]
        return boundary, due

    def _post_close_delay_seconds(self, boundary: datetime) -> int:
        """Broker post-close delay for a boundary (ported from main, D044).

        The candle closing at 00:00 server time lands on ICMarkets' daily
        rollover halt, so that one boundary waits ``rollover_settle_seconds``;
        every other boundary uses the normal short delay.  This is broker
        behaviour (applies to any strategy waking at 00:00), so it lives in the
        engine, not in ``next_wake`` (D058).
        """
        if boundary.hour == 0 and boundary.minute == 0:
            return self.config.system.rollover_settle_seconds
        return CANDLE_CLOSE_DELAY_SECONDS

    def _seconds_until(self, target: datetime) -> float:
        """Seconds from now until *target* (both MT5 server time)."""
        return max(0.0, (target - server_now(self.server_offset)).total_seconds())

    def _stop_requested(self) -> bool:
        return self.state.stop_requested or self.shutdown_event.is_set()

    def _sleep_until(self, deadline_monotonic: float) -> None:
        """Stop-aware sleep in slices until *deadline_monotonic* or stop."""
        while time.monotonic() < deadline_monotonic and not self._stop_requested():
            remaining = deadline_monotonic - time.monotonic()
            time.sleep(min(_SLEEP_SLICE_SECONDS, max(0.0, remaining)))

    def _refresh_server_offset(self) -> None:
        """Re-derive the server offset after a long sleep (D041/D047).

        A process running continuously across a US DST change keeps a stale
        offset; re-deriving when the engine wakes (the equivalent of the old
        start-of-session re-derivation) realigns the scheduler to the candle
        timestamps.  Cheap and safe — the stale-tick guard lives in
        ``get_server_utc_offset`` (D047).
        """
        pairs = self._all_pairs()
        symbol = pairs[0] if pairs else "EURUSD"
        refreshed = get_server_utc_offset(symbol)
        if refreshed != self.server_offset:
            logger.warning(
                "MT5 server offset changed UTC%+d -> UTC%+d (DST?) — realigning scheduler",
                round(self.server_offset.total_seconds() / 3600),
                round(refreshed.total_seconds() / 3600),
            )
            self.server_offset = refreshed

    def _all_pairs(self) -> list[str]:
        """Union of pairs across all hosted strategies (order-preserving)."""
        seen: dict[str, None] = {}
        for h in self.strategies:
            for p in getattr(h.instance, "pairs", []):
                seen.setdefault(p, None)
        return list(seen)

    # -- main loop ---------------------------------------------------------

    def run(self) -> None:
        """Block until stop, waking each strategy at its scheduled boundaries."""
        logger.info("Engine running — %d strategy(ies) hosted", len(self.strategies))

        while not self._stop_requested():
            try:
                # The source of truth for per-strategy pause is strategy_state in
                # the DB (persistent, survives restart).  The monitoring thread
                # and Telegram (/pause, /resume — Step 34) write it from other
                # process contexts; refresh the in-memory flag here, BEFORE
                # scheduling, so a pause/resume set elsewhere takes effect on the
                # next cycle (a paused strategy is excluded from scheduling; a
                # resumed one re-enters it).  _pause_strategy still sets both the
                # flag and the DB for immediate, same-context consistency.
                self._refresh_pause_flags()

                # Re-derive the offset BEFORE computing this cycle's wake, so the
                # boundary and sleep duration always use the freshest offset.
                # Doing it here (rather than after wait_secs was computed) is what
                # fixes the cold-start bug (D065): a bad offset derived at startup
                # from a cold MT5 feed (e.g. UTC-5 instead of UTC+3) is corrected
                # on this first cycle BEFORE the sleep is computed, instead of one
                # cycle too late — which previously let the poisoned first sleep
                # overshoot and skip an entire session.  It also keeps the
                # scheduler aligned across a DST transition while idle.
                self._refresh_server_offset()

                now = server_now(self.server_offset)
                boundary, due = self._due_strategies(now)

                if boundary is None:
                    # No active strategy has a scheduled wake — sleep a slice.
                    self._sleep_until(time.monotonic() + _SLEEP_SLICE_SECONDS)
                    continue

                delay = self._post_close_delay_seconds(boundary)
                wait_secs = self._seconds_until(boundary) + delay
                logger.info(
                    "Next wake at %s server time — sleeping %.0fs%s",
                    boundary.strftime("%Y-%m-%d %H:%M"),
                    wait_secs,
                    " (rollover settle)" if delay != CANDLE_CLOSE_DELAY_SECONDS else "",
                )

                self._sleep_until(time.monotonic() + wait_secs)
                if self._stop_requested():
                    break

                self._dispatch(boundary, due)

            except Exception:
                logger.exception("Unexpected error in engine loop — pausing bot and retrying")
                self.state.paused = True
                self._fire_and_forget(
                    self._notify_error(
                        self.bot_app.bot,
                        self.config.telegram.chat_id,
                        "Unexpected error in engine loop — bot paused. "
                        "Use /resume after investigation.",
                    )
                )
                self._sleep_until(time.monotonic() + 60.0)

    # -- dispatch ----------------------------------------------------------

    def _dispatch(self, boundary: datetime, due: list[_HostedStrategy]) -> None:
        """Run ``on_bar`` for the strategies due at *boundary*.

        *due* is the set captured at scheduling time (before the sleep) — see
        :meth:`_due_strategies` for why it cannot be recomputed here.  A fresh
        :class:`EngineMarketData` is created for the tick so candle fetches are
        deduplicated across strategies/pairs.  Balance and equity are read once
        and reused.  Per-pair evaluation is isolated so one pair's failure does
        not abort the rest.
        """
        market = EngineMarketData(boundary)

        try:
            balance = get_balance()
            equity = get_equity()
        except RuntimeError:
            logger.exception("Cannot get balance/equity — skipping tick")
            return

        # Maintain the global equity peak (kill-switch denominator), as before.
        if equity > self.peak_balance_ref[0]:
            self.peak_balance_ref[0] = equity
            logger.info("Peak equity updated: %.2f", self.peak_balance_ref[0])
            with get_connection() as db_conn:
                log_peak_balance(db_conn, self.peak_balance_ref[0])

        for hosted in due:
            self._dispatch_strategy(hosted, boundary, market, balance, equity)

    def _dispatch_strategy(
        self,
        hosted: _HostedStrategy,
        boundary: datetime,
        market: EngineMarketData,
        balance: float,
        equity: float,
    ) -> None:
        """Run ``on_bar`` for every pair of one strategy and route the Decision.

        The strategy's declared timeframes are the candle closes it ticks on; the
        Daily Lull ticks only on "M15".  For each timeframe and pair we call
        ``on_bar`` and handle the returned :class:`Decision`.

        ``close_all`` is a strategy-wide decision (it closes every position of the
        strategy, not just the current pair), so the Daily Lull returns it from
        EVERY pair at the 02:00 time stop.  Executing and — worse — notifying it
        once per pair produced N identical close/notify rounds (the duplicate
        "session closed" alerts, D057).  It is handled at most once per strategy
        per tick: the first pair runs the close and sends the single
        session-closed notification, the rest are skipped.
        """
        instance = hosted.instance

        # First dispatch after a multi-hour idle gap = the start of a new session
        # window: send the calm session-start notification before evaluating.  The
        # engine stays session-agnostic (D051) — it only observes that this
        # strategy resumed ticking after a sleep, never the window semantics.
        self._maybe_notify_session_start(hosted, boundary)
        hosted.last_dispatch_boundary = boundary

        allocated_capital = balance * hosted.allocation_pct / 100.0
        strategy_positions = get_open_positions(hosted.magic, self.server_offset)

        ctx = StrategyContext(
            account_balance=balance,
            account_equity=equity,
            allocated_capital=allocated_capital,
            open_positions=strategy_positions,
            paused=hosted.paused,
        )

        # Log the pause state once per strategy per tick, not once per pair (D064 #9).
        if self.state.paused or hosted.paused:
            source = "global" if self.state.paused else hosted.name
            logger.info(
                "%s | paused (%s) — open decisions will be dropped, closes will execute",
                hosted.name,
                source,
            )

        close_all_done = False
        for timeframe in sorted(instance.timeframes):
            for pair in instance.pairs:
                try:
                    decision = instance.on_bar(pair, timeframe, boundary, market, ctx)
                    if decision.kind == "close_all":
                        if close_all_done:
                            continue
                        close_all_done = True
                    self._handle_decision(hosted, pair, decision, balance, equity)
                except Exception:
                    logger.exception(
                        "Unhandled error evaluating %s/%s on %s", hosted.name, pair, timeframe
                    )

    def _maybe_notify_session_start(self, hosted: _HostedStrategy, boundary: datetime) -> None:
        """Send the calm session-start notification on the first wake of a session.

        Detected purely from scheduling (D051): the engine ticks every candle
        interval inside a session, so a dispatch boundary more than ``_SESSION_GAP``
        after the previous one means the strategy just woke from the between-session
        sleep.  The very first dispatch of the process (no recorded previous
        boundary) is treated as a start too.
        """
        previous = hosted.last_dispatch_boundary
        if previous is not None and boundary - previous <= _SESSION_GAP:
            return
        logger.info("%s | session start at %s server time", hosted.name, boundary.strftime("%H:%M"))
        self._fire_and_forget(
            self._notify_bot_status(
                self.bot_app.bot,
                self.config.telegram.chat_id,
                "session_started",
                f"{hosted.name} — session open, watching for setups",
            )
        )

    # -- decision handling -------------------------------------------------

    def _handle_decision(
        self,
        hosted: _HostedStrategy,
        pair: str,
        decision: Decision,
        balance: float,
        equity: float,
    ) -> None:
        if decision.kind == "noop":
            self._handle_noop(hosted, decision)
        elif decision.kind == "open":
            # Pause blocks only opens (D064): close and close_all always execute.
            if self.state.paused or hosted.paused:
                signal = decision.signal
                if signal is not None:
                    with self._trade_lock, get_connection() as db_conn:
                        self._reject(db_conn, hosted, signal, "paused")
                    hosted.instance.on_order_rejected(pair, signal, "paused")
                else:
                    logger.info(
                        "%s/%s | open decision dropped (paused, no signal)", hosted.name, pair
                    )
            else:
                self._handle_open(hosted, pair, decision, balance, equity)
        elif decision.kind == "close_all":
            self._handle_close_all(hosted, decision)
        elif decision.kind == "close":
            self._handle_close(hosted, decision)
        else:  # pragma: no cover - defensive
            logger.warning("Unknown decision kind %r from %s", decision.kind, hosted.name)

    def _handle_noop(self, hosted: _HostedStrategy, decision: Decision) -> None:
        """A noop with a signal is logged for transparency; otherwise nothing."""
        if decision.signal is None:
            return
        with self._trade_lock, get_connection() as db_conn:
            log_signal(
                db_conn,
                decision.signal,
                trade_id=None,
                server_offset=self.server_offset,
                strategy=hosted.name,
            )

    def _handle_open(
        self,
        hosted: _HostedStrategy,
        pair: str,
        decision: Decision,
        balance: float,
        equity: float,
    ) -> None:
        """Risk-gate, size and execute an open request (ports _analyse_pair_m15).

        Per-strategy risk (D072): per-strategy drawdown brake, per-strategy
        daily/weekly P&L caps, then the limit gates (per-strategy max trades +
        global correlation).  There is no global kill switch and no global trade
        cap.  On any rejection the signal is logged as rejected and
        ``on_order_rejected`` notifies the strategy.  On success the trade is
        logged, the signal is linked, the open is notified and ``on_fill`` fires.

        The method runs in three phases so the broker round-trip does NOT hold the
        shared ``_trade_lock`` (D031, audit #8).  ``open_trade`` retries transient
        rejections with sleeps (up to ~75s during the 00:00 rollover halt, D040);
        holding the lock across that window stalls the monitoring thread's
        close-detection, per-strategy drawdown brake and trailing pass for the
        whole time.  The lock only serializes position/DB access between the engine
        and the monitor — it does not need to cover the broker call:

          - Phase 1 (gate + size): under ``_trade_lock`` + a DB connection.
          - Phase 2 (execute): NO lock, NO DB connection held — this is where the
            retries/sleeps happen.
          - Phase 3 (record): under ``_trade_lock`` + a fresh DB connection.

        The engine opens trades from a single thread (the dispatch loop) and the
        only other lock user is the monitor (which never opens), so releasing the
        lock between gating and recording cannot race a concurrent open.
        """
        signal = decision.signal
        if signal is None:  # pragma: no cover - defensive
            logger.warning("%s | open decision without signal — skipping", hosted.name)
            return

        instance = hosted.instance

        # --- Phase 1: gate + size (under the lock) ---
        lot_size = self._gate_and_size(hosted, pair, signal, balance, equity)
        if lot_size is None:
            return  # rejection already logged/notified inside _gate_and_size

        # --- Phase 2: execute (NO lock, NO DB connection) ---
        # The retries/sleeps (D040) run here, free of the lock, so the monitoring
        # thread keeps detecting closes and running the drawdown brake.
        ticket = open_trade(
            pair=pair,
            direction=signal.action,
            lot_size=lot_size,
            stop_loss=signal.sl,
            take_profit=signal.tp,
            magic=hosted.magic,
            max_retries=self.config.system.order_retry_attempts,
            retry_delay_seconds=self.config.system.order_retry_delay_seconds,
            guard_boundary=(signal.range_low if signal.action == "buy" else signal.range_high),
            entry_reference=signal.entry_price,
            min_reward_fraction=self.config.system.min_reward_fraction,
        )

        # --- Phase 3: record (under the lock, fresh DB connection) ---
        if ticket is None:
            # open_trade returned no ticket. This covers both expected skips
            # (the D046 spread guard / price-reverted guard, which executor
            # logs at WARNING) and genuine broker failures (which executor
            # logs at ERROR). The executor already logged the specific cause
            # at the right level, so this summary stays at WARNING — a spread
            # guard skip is not an engine error.
            logger.warning(
                "%s/%s | open_trade returned no ticket — see executor log", hosted.name, pair
            )
            with self._trade_lock, get_connection() as db_conn:
                self._reject(db_conn, hosted, signal, "open_trade_no_ticket")
            instance.on_order_rejected(pair, signal, "open_trade_no_ticket")
            return

        with self._trade_lock, get_connection() as db_conn:
            self._record_open(db_conn, hosted, pair, signal, lot_size, ticket, balance)
        instance.on_fill(pair, signal, ticket)

    def _gate_and_size(
        self,
        hosted: _HostedStrategy,
        pair: str,
        signal: Signal,
        balance: float,
        equity: float,
    ) -> float | None:
        """Phase 1: run the risk gates and sizing under ``_trade_lock``.

        Returns the resolved, volume-adjusted ``lot_size`` on success (the rest of
        the order is derived from ``signal``/``hosted`` in phase 2), or ``None`` on
        any rejection — in which case the signal has already been logged as
        rejected and ``on_order_rejected`` has fired, matching the pre-refactor
        behaviour exactly.  The broker call is deliberately NOT made here so the
        lock is released before it (audit #8).
        """
        instance = hosted.instance
        with self._trade_lock, get_connection() as db_conn:
            # Snapshot this strategy's open positions ONCE under the lock; the list
            # is stable for the lock's duration, so the period caps and the limit
            # gate reuse it instead of re-querying the broker (D072 efficiency).
            strategy_positions = get_open_positions(hosted.magic, self.server_offset)
            floating = sum(p.get("profit", 0.0) for p in strategy_positions)

            # --- Per-strategy drawdown brake (pauses only this strategy) ---
            ok, reason, strategy_equity_value, _peak = self._check_strategy_drawdown(
                db_conn, hosted, balance
            )
            if not ok:
                self._pause_strategy(db_conn, hosted, reason, strategy_equity_value)
                self._reject(db_conn, hosted, signal, reason)
                instance.on_order_rejected(pair, signal, reason)
                return None

            # --- Per-strategy daily/weekly P&L caps (windowed, auto-resetting) ---
            # NOT a persistent pause: recomputed every open attempt so it clears at
            # the next day/week rollover without a manual /resume (D072).
            ok, reason = self._check_period_caps(db_conn, hosted, balance, floating)
            if not ok:
                logger.info("%s/%s | %s — open blocked", hosted.name, pair, reason)
                self._reject(db_conn, hosted, signal, reason)
                instance.on_order_rejected(pair, signal, reason)
                return None

            # --- Limit gates: per-strategy max trades + correlation ---
            account_trades = get_open_trades(db_conn)
            ok, reason = check_strategy_risk(
                strategy_open_trades=strategy_positions,
                account_open_trades=account_trades,
                new_pair=pair,
                new_direction=signal.action,
                strategy_risk=hosted.risk,
                risk_global=self.config.risk_global,
            )
            if not ok:
                logger.info("%s/%s | risk check failed: %s", hosted.name, pair, reason)
                self._reject(db_conn, hosted, signal, f"risk: {reason}")
                instance.on_order_rejected(pair, signal, f"risk: {reason}")
                return None

            # --- Sizing + broker volume constraints ---
            lot_size = self._size_position(pair, signal, balance, hosted)
            if lot_size is None or lot_size <= 0:
                self._reject(db_conn, hosted, signal, "lot_size_zero")
                instance.on_order_rejected(pair, signal, "lot_size_zero")
                return None

            lot_size = self._apply_volume_constraints(pair, lot_size, db_conn, hosted, signal)
            if lot_size is None:
                instance.on_order_rejected(pair, signal, "lot_below_min")
                return None

            return lot_size

    def _size_position(
        self, pair: str, signal: Signal, balance: float, hosted: _HostedStrategy
    ) -> float | None:
        """Compute lot size against the strategy's notional allocation (D053)."""
        import MetaTrader5 as mt5

        from drift.pricing import pip_multiplier, pip_value

        sym_info = mt5.symbol_info(pair)
        pip_mult = pip_multiplier(pair)
        pip_val = pip_value(pair, info=sym_info)
        sl_pips = abs(signal.entry_price - signal.sl) * pip_mult

        lot_size = calculate_position_size_allocated(
            balance=balance,
            allocation_pct=hosted.allocation_pct,
            percent_per_trade=hosted.risk.percent_per_trade,
            stop_loss_pips=sl_pips,
            pip_value=pip_val,
        )
        if lot_size <= 0:
            logger.warning("%s/%s | position size is 0 — skipping trade", hosted.name, pair)
            return None
        return lot_size

    def _apply_volume_constraints(
        self, pair: str, lot_size: float, db_conn, hosted: _HostedStrategy, signal: Signal
    ) -> float | None:
        """Apply the broker's volume_step/min/max to a raw lot size (ported).

        Returns the adjusted lot size, or None (and logs a rejection) when the
        size falls below the broker minimum.  ``risk.py`` is deliberately
        broker-agnostic; this adjustment lives where symbol_info is available.
        """
        import MetaTrader5 as mt5

        sym_info = mt5.symbol_info(pair)
        if sym_info is None:
            return lot_size

        vol_step = sym_info.volume_step
        vol_min = sym_info.volume_min
        vol_max = sym_info.volume_max

        if vol_step > 0:
            adjusted = round(math.floor(lot_size / vol_step) * vol_step, 10)
            if adjusted != lot_size:
                logger.info(
                    "%s/%s | lot_size %.4f rounded down to %.4f (volume_step=%.4f)",
                    hosted.name,
                    pair,
                    lot_size,
                    adjusted,
                    vol_step,
                )
            lot_size = adjusted

        if lot_size < vol_min:
            logger.warning(
                "%s/%s | lot_size %.4f is below volume_min %.4f — skipping trade",
                hosted.name,
                pair,
                lot_size,
                vol_min,
            )
            self._reject(db_conn, hosted, signal, "lot_below_min")
            return None

        if lot_size > vol_max:
            logger.warning(
                "%s/%s | lot_size %.4f exceeds volume_max %.4f — capping",
                hosted.name,
                pair,
                lot_size,
                vol_max,
            )
            lot_size = vol_max

        return lot_size

    def _record_open(
        self,
        db_conn,
        hosted: _HostedStrategy,
        pair: str,
        signal: Signal,
        lot_size: float,
        ticket: int,
        balance: float,
    ) -> None:
        """Persist the open trade + accepted signal and notify (ported)."""
        import MetaTrader5 as mt5

        entry_price = signal.entry_price
        positions = mt5.positions_get(ticket=ticket)
        if positions:
            entry_price = positions[0].price_open

        trade_id = log_trade(
            db_conn,
            pair=pair,
            direction=signal.action,
            entry_price=entry_price,
            stop_loss=signal.sl,
            take_profit=signal.tp,
            position_size=lot_size,
            balance_at_open=balance,
            mt5_ticket=ticket,
            strategy=hosted.name,
        )
        log_signal(
            db_conn,
            signal,
            trade_id=trade_id,
            server_offset=self.server_offset,
            strategy=hosted.name,
        )

        risk_usd = balance * hosted.allocation_pct / 100.0 * hosted.risk.percent_per_trade / 100.0
        trade_info = {
            "pair": pair,
            "direction": signal.action,
            "entry_price": entry_price,
            "stop_loss": signal.sl,
            "take_profit": signal.tp,
            "position_size": lot_size,
            "risk_usd": risk_usd,
            "risk_pct": hosted.risk.percent_per_trade,
        }
        self._fire_and_forget(
            self._notify_trade_opened(self.bot_app.bot, self.config.telegram.chat_id, trade_info)
        )
        logger.info(
            "%s/%s | trade opened ticket=%d direction=%s lot=%.2f",
            hosted.name,
            pair,
            ticket,
            signal.action,
            lot_size,
        )

    def _reject(self, db_conn, hosted: _HostedStrategy, signal: Signal, reason: str) -> None:
        """Log a signal as rejected (risk/limit/sizing/execution refusal)."""
        log_signal(
            db_conn,
            signal,
            trade_id=None,
            rejection_reason=reason,
            server_offset=self.server_offset,
            strategy=hosted.name,
        )

    # -- per-strategy drawdown -------------------------------------------------

    def _check_strategy_drawdown(
        self, db_conn, hosted: _HostedStrategy, balance: float
    ) -> tuple[bool, str, float, float]:
        """Evaluate the per-strategy drawdown brake (D062).

        Thin wrapper over the canonical :func:`drift.db.evaluate_strategy_drawdown`
        (the single equity/drawdown implementation shared with the monitor) — it
        only supplies this strategy's floating P&L from its open positions (by its
        magic).  See D062 for the persisted-baseline equity model.

        Returns ``(ok, reason, equity, peak)``.
        """
        positions = get_open_positions(hosted.magic, self.server_offset)
        floating = sum(p.get("profit", 0.0) for p in positions)

        return evaluate_strategy_drawdown(
            db_conn,
            hosted.name,
            hosted.allocation_pct,
            balance,
            floating,
            hosted.risk.max_drawdown_percent,
        )

    def _check_period_caps(
        self, db_conn, hosted: _HostedStrategy, balance: float, floating: float
    ) -> tuple[bool, str]:
        """Evaluate this strategy's daily + weekly P&L caps (D072).

        Windowed gate, NOT a persistent pause: it is recomputed on every open
        attempt, so it auto-resets when the day/week window rolls over (a daily
        cap clears at the next day with no manual /resume).

        The window boundaries are computed in MT5 server time (the day starts at
        00:00 server; the week at the most recent Sunday 00:00 server, today if
        today is Sunday) and converted to real UTC for the DB query via the
        project's ``utc = server - server_offset`` convention (D039) — the same
        conversion used by ``log_signal``.  Window P&L = realized-in-window
        (closed trades) + ``floating`` (the strategy's current open-position P&L,
        snapshotted once by the caller).

        Returns ``(ok, reason)`` — ``ok`` is False once either cap trips.
        """
        risk = hosted.risk
        daily_disabled = risk.max_daily_loss_pct == 0 and risk.max_daily_profit_pct == 0
        weekly_disabled = risk.max_weekly_loss_pct == 0 and risk.max_weekly_profit_pct == 0
        if daily_disabled and weekly_disabled:
            return True, ""

        now_server = server_now(self.server_offset)
        day_start_server = now_server.replace(hour=0, minute=0, second=0, microsecond=0)
        # weekday(): Monday=0 .. Sunday=6; days since the most recent Sunday.
        days_since_sunday = (now_server.weekday() + 1) % 7
        week_start_server = day_start_server - timedelta(days=days_since_sunday)

        # Convert server-time boundaries to real UTC (utc = server - offset, D039).
        day_start_utc = (day_start_server - self.server_offset).isoformat()
        week_start_utc = (week_start_server - self.server_offset).isoformat()

        allocated_capital = balance * hosted.allocation_pct / 100.0

        # Daily window ⊆ weekly window; both share the same floating snapshot.
        windows = (
            ("daily", day_start_utc, risk.max_daily_loss_pct, risk.max_daily_profit_pct),
            ("weekly", week_start_utc, risk.max_weekly_loss_pct, risk.max_weekly_profit_pct),
        )
        for label, since_utc, loss_pct, profit_pct in windows:
            window_pnl = get_realized_pnl_since(db_conn, hosted.name, since_utc) + floating
            ok, reason = check_period_pnl(
                window_pnl, allocated_capital, loss_pct, profit_pct, label
            )
            if not ok:
                return False, reason

        return True, ""

    def _pause_strategy(self, db_conn, hosted: _HostedStrategy, reason: str, equity: float) -> None:
        """Pause one strategy on its own drawdown brake (keeps its positions)."""
        if hosted.paused:
            return
        hosted.paused = True
        set_strategy_paused(db_conn, hosted.name, True)
        logger.warning(
            "%s | per-strategy drawdown reached: %s — strategy paused", hosted.name, reason
        )
        log_event(db_conn, "drawdown_alert", detail=reason, balance=equity, strategy=hosted.name)
        self._fire_and_forget(
            self._notify_bot_status(
                self.bot_app.bot,
                self.config.telegram.chat_id,
                "drawdown_strategy",
                f"<b>{hosted.name}</b>: {reason}\nThis strategy is paused; the rest keep running. "
                f"Open positions stay open — use /resume {hosted.name} when ready.",
            )
        )

    def _trip_global_brake(self, db_conn, reason: str, equity: float) -> None:
        """Pause all strategies on an account-wide brake.

        The global drawdown kill switch was removed (D072): risk is per-strategy,
        so this is no longer called by the per-open gating.  It is retained as the
        single account-wide pause primitive for any future global safety brake.
        """
        if self.state.paused:
            return
        self.state.paused = True
        logger.warning("Global drawdown limit reached: %s — bot paused", reason)
        log_event(db_conn, "drawdown_alert", detail=reason, balance=equity)
        self._fire_and_forget(
            self._notify_bot_status(
                self.bot_app.bot,
                self.config.telegram.chat_id,
                "drawdown_global",
                f"{reason}\nAll strategies paused. Open positions stay open — "
                "review and use /resume when ready.",
            )
        )

    # -- close handling ----------------------------------------------------

    def _handle_close_all(self, hosted: _HostedStrategy, decision: Decision) -> None:
        """Close every position of this strategy (ports _close_session_trades).

        Only this strategy's positions (filtered by its magic) are touched; each
        is closed and recorded under its own try/except so one failure does not
        abort the batch (D031).  The strategy resets its own internal state via
        its lifecycle — the engine does not touch private state.
        """
        with self._trade_lock:
            positions = get_open_positions(hosted.magic, self.server_offset)
            for pos in positions:
                try:
                    self._close_one(hosted, pos, "session_close")
                except Exception:
                    logger.exception(
                        "Error closing %s position ticket=%d %s — continuing",
                        hosted.name,
                        pos["ticket"],
                        pos["pair"],
                    )

        # Log the close_all signal for transparency, if carried.
        if decision.signal is not None:
            with self._trade_lock, get_connection() as db_conn:
                log_signal(
                    db_conn,
                    decision.signal,
                    trade_id=None,
                    server_offset=self.server_offset,
                    strategy=hosted.name,
                )

        # Notify once per close_all (the caller dedups it to one per strategy per
        # tick).  The bot is NOT stopping — it closed the session and will sleep
        # until the next one — so use the calm "session_closed" status, never the
        # alarming "BOT STOPPED" (D057).
        #
        # For the routine session_close at 02:00, always fire the notification —
        # even on a quiet night with no open trades.  The earlier "stay silent when
        # closed == 0" rule (#7) made the 02:00 heartbeat invisible: an operator
        # could not tell a healthy bot with no setups from a hung/dead one, and in
        # production that ambiguity hid days of non-trading (D066).  The wording for
        # a no-trade night is kept calm so it reads as a liveness ping, not noise.
        closed = len(positions)
        if decision.reason == "session_close":
            if closed == 0:
                detail = (
                    f"{hosted.name} — session closed, no trades this session, "
                    f"sleeping until next session"
                )
            else:
                detail = (
                    f"{hosted.name} — session closed, "
                    f"{closed} position(s) closed, sleeping until next session"
                )
        else:
            detail = f"{hosted.name}: {decision.reason or 'positions closed'}"
        self._fire_and_forget(
            self._notify_bot_status(
                self.bot_app.bot,
                self.config.telegram.chat_id,
                "session_closed",
                detail,
            )
        )

    def _handle_close(self, hosted: _HostedStrategy, decision: Decision) -> None:
        """Close one specific ticket of this strategy and record it."""
        if decision.ticket is None:  # pragma: no cover - defensive
            logger.warning("%s | close decision without ticket — skipping", hosted.name)
            return
        with self._trade_lock:
            positions = get_open_positions(hosted.magic, self.server_offset)
            target = next((p for p in positions if p["ticket"] == decision.ticket), None)
            if target is None:
                logger.warning(
                    "%s | close target ticket=%d not found among strategy positions",
                    hosted.name,
                    decision.ticket,
                )
                return
            try:
                self._close_one(hosted, target, decision.reason or "manual")
            except Exception:
                logger.exception(
                    "Error closing %s ticket=%d — continuing", hosted.name, target["ticket"]
                )

    def _close_one(self, hosted: _HostedStrategy, pos: dict, close_reason: str) -> None:
        """Close one position at market, record it (settled balance, D048), notify."""
        import MetaTrader5 as mt5

        logger.info(
            "%s: closing ticket=%d %s (%s)", hosted.name, pos["ticket"], pos["pair"], close_reason
        )
        success = close_trade(
            ticket=pos["ticket"],
            pair=pos["pair"],
            lot_size=pos["volume"],
            direction=pos["direction"],
            magic=hosted.magic,
        )
        if not success:
            return

        exit_price = 0.0
        pnl = pos.get("profit", 0.0)
        deals = mt5.history_deals_get(position=pos["ticket"])
        if deals:
            close_deal = deals[-1]
            exit_price = close_deal.price
            pnl = close_deal.profit

        # Settled balance read fresh from MT5 so it includes this trade's own
        # P&L and commission (D048).
        balance_settled = get_balance()

        duration = 0
        with get_connection() as db_conn:
            trade = get_trade_by_ticket(db_conn, pos["ticket"])
            if trade and trade.get("closed_at") is None:
                close_trade_record(
                    db_conn,
                    trade["id"],
                    exit_price=exit_price,
                    profit_loss=pnl,
                    balance_at_close=balance_settled,
                    close_reason=close_reason,
                )
                updated = get_trade_by_ticket(db_conn, pos["ticket"])
                duration = (updated["duration_minutes"] or 0) if updated else 0

        trade_info = {
            "pair": pos["pair"],
            "direction": pos["direction"],
            "entry_price": pos.get("price_open", 0.0),
            "exit_price": exit_price,
            "profit_loss": pnl,
            "close_reason": close_reason,
            "duration_minutes": duration,
        }
        self._fire_and_forget(
            self._notify_trade_closed(self.bot_app.bot, self.config.telegram.chat_id, trade_info)
        )
