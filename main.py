"""Drift — autonomous forex trend-following bot.

Entry point. Orchestrates the M15 Daily Lull session loop, the continuous monitoring
thread, and the Telegram bot thread.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import signal
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from drift.config import DriftConfig, RiskConfig, load_config
from drift.db import (
    close_trade_record,
    get_connection,
    get_stats,
    get_strategy_state,
    get_trade_by_ticket,
    init_db,
    log_event,
    log_peak_balance,
    set_strategy_paused,
    upsert_strategy_peak,
)
from drift.engine import Engine
from drift.executor import close_trade, get_open_positions
from drift.formatting import parse_utc_offset, set_display_tz
from drift.mt5_client import (
    connect,
    disconnect,
    get_balance,
    get_equity,
    get_server_utc_offset,
    health_check,
    reconnect,
    server_now,
)
from drift.report import generate_weekly_report
from drift.risk import (
    check_drawdown,
    check_strategy_drawdown,
    strategy_equity,
)
from drift.telegram_bot import (
    BotState,
    notify_bot_status,
    notify_error,
    notify_trade_closed,
    notify_trade_opened,
    send_notification,
    setup_bot,
)
from drift.trailing import process_open_trades

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_PROJECT_ROOT = Path(__file__).parent

# Telegram setup is the control plane; a transient network/DNS failure at
# startup must not kill an autonomous bot. Retry with capped backoff before
# giving up. See D049.
_TELEGRAM_SETUP_MAX_ATTEMPTS = 10
_TELEGRAM_SETUP_BASE_DELAY_SECONDS = 15.0
_TELEGRAM_SETUP_MAX_DELAY_SECONDS = 60.0

# Weekly report trigger is derived from config.reports at runtime.
# Tracks the date of the last sent report to avoid duplicate sends.
_last_report_date: date | None = None

# Prevents race conditions between the main analysis cycle and the monitoring thread.
_trade_lock = threading.Lock()

# The monitoring thread's trailing-stop pass is broker housekeeping that only ever
# reads use_trailing_stop (False for every current strategy — Daily Lull does not
# trail, D029).  A bare RiskConfig() carries that default, so the monitoring thread
# no longer depends on the transitional config.risk shim (C10) for trailing.  When
# a trailing strategy is added, trailing moves into that strategy via the engine.
_MONITOR_TRAILING_CONFIG = RiskConfig()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    log_dir = _PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "drift.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Suppress INFO-level output from httpx and httpcore.  Without this, every
    # Telegram API request (e.g. getUpdates every 10 s) is logged at INFO with
    # the full URL, which embeds the bot token — an active credential leak.
    # WARNING level keeps genuine errors (timeouts, 4xx/5xx) visible.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


def _seconds_until(target: datetime, server_offset: timedelta) -> float:
    """Return seconds until *target* (in MT5 server time) from now.

    The sleep duration is a pure delta — server-to-server subtraction equals
    the real wall-clock duration because server time = UTC + fixed offset and
    the offset cancels out.
    """
    return max(0.0, (target - server_now(server_offset)).total_seconds())


# ---------------------------------------------------------------------------
# Pip value / position sizing helpers
# ---------------------------------------------------------------------------


def _pip_multiplier(pair: str) -> float:
    """Return the multiplier to convert price distance to pips."""
    return 100.0 if "JPY" in pair.upper() else 10000.0


def _pip_value(pair: str, info=None) -> float:
    """Return USD pip value per standard lot using MT5 symbol info.

    Uses `trade_tick_value` (USD per tick on one standard lot) and converts to
    pips: 1 pip = 10 ticks on both 5-digit (most pairs) and 3-digit (JPY) brokers.

    Falls back to $10 if MT5 has no info for the pair, but this is a safety net,
    not a default — every configured pair should resolve through MT5 in practice.

    Parameters
    ----------
    info:
        Pre-fetched ``mt5.symbol_info`` result.  When provided the function skips
        the MT5 call so callers that already hold the object avoid a second IPC round-trip.
    """
    if info is None:
        import MetaTrader5 as mt5

        info = mt5.symbol_info(pair)
    if info is None or info.trade_tick_value <= 0:
        logger.warning("No tick_value for %s — falling back to $10/pip/lot", pair)
        return 10.0
    return info.trade_tick_value * 10.0


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------


def _enabled_pairs(config: DriftConfig) -> list[str]:
    """Return the order-preserving union of pairs across enabled strategies (D054)."""
    seen: dict[str, None] = {}
    for instance in config.strategies.values():
        if not instance.enabled:
            continue
        for pair in instance.pairs:
            seen.setdefault(pair, None)
    return list(seen)


def _enabled_strategy_magics(config: DriftConfig) -> dict[str, int]:
    """Return ``{strategy_name: effective_magic}`` for every enabled strategy.

    The effective magic is ``system.magic_number + magic_offset`` — the same
    attribution the engine uses (D052).  The monitoring thread iterates this map
    to compute per-strategy drawdown and to build the union of magics for
    attributed close detection.
    """
    base_magic = config.system.magic_number
    return {
        name: base_magic + instance.magic_offset
        for name, instance in config.strategies.items()
        if instance.enabled
    }


def _validate_pairs(pairs: list[str]) -> None:
    """Activate configured pairs in Market Watch and warn about missing ones.

    Activation (`symbol_select`) is required for `symbol_info.trade_tick_value`
    to return a real value — without it MT5 reports $0 for inactive pairs, which
    poisons position sizing.
    """
    import MetaTrader5 as mt5

    for pair in pairs:
        info = mt5.symbol_info(pair)
        if info is None:
            logger.warning("Pair %s not found in MT5 — it will be skipped", pair)
            continue
        if not info.visible:
            if mt5.symbol_select(pair, True):
                logger.info("Pair %s activated in Market Watch", pair)
            else:
                logger.warning("Pair %s could not be activated in Market Watch", pair)
                continue
        info = mt5.symbol_info(pair)
        if info.trade_tick_value <= 0:
            logger.warning("Pair %s has no tick_value — sizing will fall back to $10/pip", pair)
        else:
            logger.info(
                "Pair %s OK (digits=%d, pip_value=$%.2f/lot)",
                pair,
                info.digits,
                info.trade_tick_value * 10,
            )


# ---------------------------------------------------------------------------
# Monitoring thread (daemon, every N seconds)
# ---------------------------------------------------------------------------


def _monitoring_loop(
    config: DriftConfig,
    state: BotState,
    peak_balance_ref: list[float],
    shutdown_event: threading.Event,
    bot_app,
) -> None:
    """Runs in a daemon thread. Updates trailing stops, checks drawdown, detects closed trades."""
    interval = config.system.loop_check_interval_seconds
    known_tickets: set[int] = set()

    while not shutdown_event.is_set():
        try:
            _monitoring_tick(
                config, state, peak_balance_ref, known_tickets, bot_app, shutdown_event
            )
        except Exception:
            logger.exception("Unhandled error in monitoring loop")
        shutdown_event.wait(timeout=interval)


_DAY_NAME_TO_WEEKDAY: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _weekly_trigger_utc(reports_config) -> tuple[int, int]:
    """Return (weekday_utc, hour_utc) for the weekly report trigger.

    Converts the local trigger (weekly_report_day + weekly_report_hour interpreted
    in reports_config.timezone) to UTC, handling day rollover.

    Example: sunday 20:00 UTC-5 → monday 01:00 UTC → (0, 1).
    """
    day_name = reports_config.weekly_report_day.strip().lower()
    local_weekday = _DAY_NAME_TO_WEEKDAY.get(day_name, 6)  # default sunday
    local_hour = int(reports_config.weekly_report_hour)

    # Parse offset from strings like "UTC-5", "UTC+3", "UTC-5:30" (hours only used here).
    tz_str = reports_config.timezone.strip().upper()
    offset_hours = 0
    if tz_str.startswith("UTC"):
        remainder = tz_str[3:]
        if remainder:
            try:
                offset_hours = int(remainder.split(":")[0])
            except ValueError:
                offset_hours = 0

    # Convert local hour to UTC hour: UTC = local - offset
    utc_hour = local_hour - offset_hours
    day_shift = 0
    if utc_hour >= 24:
        utc_hour -= 24
        day_shift = 1
    elif utc_hour < 0:
        utc_hour += 24
        day_shift = -1

    utc_weekday = (local_weekday + day_shift) % 7
    return utc_weekday, utc_hour


def _check_weekly_report(
    config: DriftConfig,
    balance: float,
    peak_balance: float,
    bot_app,
) -> None:
    """Send the weekly report when the UTC time matches the configured trigger."""
    global _last_report_date

    now = datetime.now(timezone.utc)
    today = now.date()

    trigger_weekday, trigger_hour = _weekly_trigger_utc(config.reports)
    if now.weekday() != trigger_weekday or now.hour != trigger_hour:
        return
    if _last_report_date == today:
        return

    try:
        with get_connection() as db_conn:
            report_text = generate_weekly_report(db_conn, balance, peak_balance)
        _fire_and_forget(send_notification(bot_app.bot, config.telegram.chat_id, report_text))
        _last_report_date = today
        logger.info("Weekly report sent")
    except Exception:
        logger.exception("Failed to generate or send weekly report")


def _monitoring_tick(
    config: DriftConfig,
    state: BotState,
    peak_balance_ref: list[float],
    known_tickets: set[int],
    bot_app,
    shutdown_event: threading.Event | None = None,
) -> None:
    if not health_check():
        logger.warning("MT5 health check failed — attempting reconnect")
        ok = reconnect(
            config.broker,
            interval=config.system.mt5_reconnect_interval_seconds,
            shutdown_event=shutdown_event,
        )
        if ok:
            with get_connection() as db_conn:
                log_event(db_conn, "reconnect", detail="MT5 reconnected")
            _fire_and_forget(
                notify_bot_status(
                    bot_app.bot, config.telegram.chat_id, "started", "MT5 reconnected"
                )
            )
        else:
            with get_connection() as db_conn:
                log_event(db_conn, "error", detail="MT5 reconnect failed")
            _fire_and_forget(
                notify_error(bot_app.bot, config.telegram.chat_id, "MT5 reconnect failed")
            )
        return

    try:
        balance = get_balance()
        equity = get_equity()
    except RuntimeError:
        logger.exception("Cannot get balance/equity in monitoring tick")
        return

    with _trade_lock:
        if equity > peak_balance_ref[0]:
            peak_balance_ref[0] = equity
            with get_connection() as db_conn:
                log_peak_balance(db_conn, peak_balance_ref[0])

        # Global brake (kill switch) — pauses EVERYTHING (state.paused).  Measured
        # on account equity vs the global equity peak using risk_global (D053);
        # this no longer reads the config.risk compat shim (C10).
        drawdown_ok, _ = _check_drawdown_pause(equity, peak_balance_ref[0], config, state, bot_app)
        if not drawdown_ok:
            return

        # Per-strategy positions (by effective magic) drive both the per-strategy
        # drawdown brake and the union of tickets used for attributed close
        # detection (D052/D053).
        strategy_magics = _enabled_strategy_magics(config)
        positions_by_strategy: dict[str, list[dict]] = {
            name: get_open_positions(magic) for name, magic in strategy_magics.items()
        }

        # Per-strategy drawdown brake — pauses ONLY the breaching strategy
        # (strategy_state.paused), distinct from the global kill switch above.
        for name, positions in positions_by_strategy.items():
            _check_strategy_drawdown_pause(name, positions, balance, config, bot_app)

        # Union of all enabled strategies' open positions for close detection.
        # A ticket disappearing from any strategy's magic is a real close (D031).
        all_positions = [p for positions in positions_by_strategy.values() for p in positions]
        mt5_tickets = {p["ticket"] for p in all_positions}

        pending_tickets = _detect_closed_trades(known_tickets, mt5_tickets, config, bot_app)

        # Re-add tickets whose close deal was not yet available so they are
        # retried in the next monitoring cycle (~30s).
        known_tickets.clear()
        known_tickets |= mt5_tickets | pending_tickets

        if all_positions:
            process_open_trades(all_positions, _MONITOR_TRAILING_CONFIG)

    _check_weekly_report(config, balance, peak_balance_ref[0], bot_app)


def _check_drawdown_pause(
    equity: float,
    peak_equity: float,
    config: DriftConfig,
    state: BotState,
    bot_app,
) -> tuple[bool, str]:
    """Global drawdown kill switch — pauses the whole account (state.paused).

    Measured on account equity vs the global equity peak using
    ``risk_global.max_drawdown_percent`` (D053).  Mirrors the engine's
    ``_trip_global_brake`` so a brake tripped by either context pauses everything.
    """
    ok, reason = check_drawdown(equity, peak_equity, config.risk_global.max_drawdown_percent)
    if not ok and not state.paused:
        state.paused = True
        logger.warning("Drawdown limit reached: %s — bot paused", reason)
        with get_connection() as db_conn:
            log_event(db_conn, "drawdown_alert", detail=reason, balance=equity)
        _fire_and_forget(
            notify_bot_status(
                bot_app.bot,
                config.telegram.chat_id,
                "paused",
                f"Drawdown limit reached: {reason}",
            )
        )
        return False, reason
    return True, ""


def _check_strategy_drawdown_pause(
    strategy_name: str,
    positions: list[dict],
    balance: float,
    config: DriftConfig,
    bot_app,
) -> None:
    """Per-strategy drawdown brake — pauses only this strategy (D053, D056).

    Computes the strategy's individual equity curve
    (``allocated_baseline + realized_pnl + floating_pnl``), keeps its peak in
    ``strategy_state``, and when drawdown breaches the strategy's own limit sets
    ``strategy_state.paused`` so the engine stops opening new entries for it on
    its next dispatch.  Open positions are kept (pause = hold, D053) — they stay
    protected by their server-side SL/TP.

    allocated_baseline is computed from the CURRENT account balance
    (``balance * allocation_pct/100``).  A baseline anchored to the balance at
    the moment the strategy was enabled would be a more stable denominator, but
    it is not persisted yet; the current-balance approximation tracks the
    notional allocation the strategy actually sizes against (D053) and is
    consistent with the engine's own per-strategy brake in
    ``Engine._check_strategy_drawdown`` (same formula), so both contexts agree.

    Must be called while holding ``_trade_lock`` (the caller does).
    """
    instance = config.strategies.get(strategy_name)
    if instance is None:  # pragma: no cover - defensive
        return

    allocated_baseline = balance * instance.allocation_pct / 100.0
    floating = sum(p.get("profit", 0.0) for p in positions)

    with get_connection() as db_conn:
        realized = get_stats(db_conn, strategy=strategy_name).get("total_pnl", 0.0) or 0.0
        equity = strategy_equity(allocated_baseline, realized, floating)

        row = get_strategy_state(db_conn, strategy_name)
        stored_peak = (row["peak_equity"] if row else 0.0) or 0.0
        already_paused = bool(row["paused"]) if row else False
        peak = max(stored_peak, equity)
        upsert_strategy_peak(db_conn, strategy_name, equity)

        ok, reason = check_strategy_drawdown(equity, peak, instance.risk.max_drawdown_percent)
        if ok or already_paused:
            return

        set_strategy_paused(db_conn, strategy_name, True)
        logger.warning(
            "%s | per-strategy drawdown reached: %s — strategy paused", strategy_name, reason
        )
        log_event(db_conn, "drawdown_alert", detail=reason, balance=equity, strategy=strategy_name)

    _fire_and_forget(
        notify_bot_status(
            bot_app.bot,
            config.telegram.chat_id,
            "paused",
            f"Strategy {strategy_name} paused — {reason}",
        )
    )


def _detect_closed_trades(
    known_tickets: set[int],
    mt5_tickets: set[int],
    config: DriftConfig,
    bot_app,
) -> set[int]:
    """Find tickets that were open last cycle but are gone now — MT5 closed them (SL/TP).

    Returns the set of tickets whose close deal was not yet available in MT5 history.
    The caller must re-add these to known_tickets so they are retried next cycle.
    """
    import MetaTrader5 as mt5

    closed_tickets = known_tickets - mt5_tickets
    if not closed_tickets:
        return set()

    pending_tickets: set[int] = set()

    for ticket in closed_tickets:
        with get_connection() as db_conn:
            trade = get_trade_by_ticket(db_conn, ticket)
            if trade is None or trade.get("closed_at") is not None:
                continue

            deals = mt5.history_deals_get(position=ticket)
            if not deals:
                logger.info(
                    "Deal not yet available for closed ticket=%d %s — will reconcile next cycle",
                    ticket,
                    trade["pair"],
                )
                pending_tickets.add(ticket)
                continue

            close_deal = deals[-1]
            exit_price = close_deal.price
            pnl = close_deal.profit
            if close_deal.reason == mt5.DEAL_REASON_SL:
                close_reason = "stop_loss"
            elif close_deal.reason == mt5.DEAL_REASON_TP:
                close_reason = "take_profit"
            else:
                close_reason = "manual"

            # Settled balance fresh from MT5, not the pre-close snapshot. D048.
            close_trade_record(db_conn, trade["id"], exit_price, pnl, get_balance(), close_reason)

            updated_trade = get_trade_by_ticket(db_conn, ticket)
            duration = updated_trade["duration_minutes"] if updated_trade else 0

            trade_info = {
                "pair": trade["pair"],
                "direction": trade["direction"],
                "entry_price": trade["entry_price"],
                "exit_price": exit_price,
                "profit_loss": pnl,
                "close_reason": close_reason,
                "duration_minutes": duration or 0,
                # The strategy was attributed at open time by the engine (D056);
                # surface it so multi-strategy close notifications are unambiguous.
                "strategy": trade.get("strategy"),
            }
            _fire_and_forget(notify_trade_closed(bot_app.bot, config.telegram.chat_id, trade_info))
            logger.info(
                "Detected closed trade ticket=%d pair=%s strategy=%s pnl=%.2f reason=%s",
                ticket,
                trade["pair"],
                trade.get("strategy"),
                pnl,
                close_reason,
            )

    return pending_tickets


# ---------------------------------------------------------------------------
# Telegram thread
# ---------------------------------------------------------------------------


def _run_telegram_thread(bot_app, loop: asyncio.AbstractEventLoop) -> None:
    """Run python-telegram-bot polling in a dedicated thread with its own event loop."""
    asyncio.set_event_loop(loop)
    bot_app.run_polling(close_loop=False, stop_signals=None)


# ---------------------------------------------------------------------------
# Async fire-and-forget helper
# ---------------------------------------------------------------------------


_tg_loop: asyncio.AbstractEventLoop | None = None


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine on the Telegram event loop without blocking."""
    if _tg_loop is not None and _tg_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, _tg_loop)
    else:
        logger.debug("Telegram loop not running — notification skipped")


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def _shutdown(
    config: DriftConfig,
    state: BotState,
    bot_app,
    shutdown_event: threading.Event,
) -> None:
    logger.info("Shutting down Drift bot")
    shutdown_event.set()

    if state.stop_requested:
        base_magic = config.system.magic_number
        for instance_cfg in config.strategies.values():
            if not instance_cfg.enabled:
                continue
            effective_magic = base_magic + instance_cfg.magic_offset
            mt5_positions = get_open_positions(effective_magic)
            for pos in mt5_positions:
                logger.info(
                    "Stop requested — closing ticket=%d %s (magic=%d)",
                    pos["ticket"],
                    pos["pair"],
                    effective_magic,
                )
                try:
                    close_trade(
                        ticket=pos["ticket"],
                        pair=pos["pair"],
                        lot_size=pos["volume"],
                        direction=pos["direction"],
                        magic=effective_magic,
                    )
                except Exception:
                    logger.exception(
                        "Error closing ticket=%d %s on shutdown — continuing",
                        pos["ticket"],
                        pos["pair"],
                    )

    try:
        balance = get_balance()
    except RuntimeError:
        balance = None

    with get_connection() as db_conn:
        log_event(db_conn, "stop", detail="clean shutdown", balance=balance)

    _fire_and_forget(notify_bot_status(bot_app.bot, config.telegram.chat_id, "stopped"))
    # Brief pause so the notification has a chance to send before loop closes.
    time.sleep(2)

    disconnect()
    logger.info("Drift bot stopped")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _setup_telegram_resilient(config: DriftConfig, state, tg_loop):
    """Set up and initialise the Telegram bot, retrying transient network errors.

    A DNS/network blip at startup (e.g. router DNS briefly down) raised an
    unhandled httpx.ConnectError that killed the whole process — even though
    trading does not need Telegram up that instant. Retry with capped backoff so
    the bot waits out a transient outage; re-raise only after exhausting all
    attempts. See D049.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _TELEGRAM_SETUP_MAX_ATTEMPTS + 1):
        try:
            bot_app = tg_loop.run_until_complete(
                setup_bot(
                    token=config.telegram.bot_token,
                    chat_id=config.telegram.chat_id,
                    state=state,
                    config=config,
                )
            )
            tg_loop.run_until_complete(bot_app.initialize())
            if attempt > 1:
                logger.info("Telegram setup succeeded on attempt %d", attempt)
            return bot_app
        except Exception as exc:
            last_exc = exc
            if attempt < _TELEGRAM_SETUP_MAX_ATTEMPTS:
                delay = min(
                    _TELEGRAM_SETUP_BASE_DELAY_SECONDS * attempt,
                    _TELEGRAM_SETUP_MAX_DELAY_SECONDS,
                )
                logger.warning(
                    "Telegram setup failed (attempt %d/%d): %r — retrying in %.0fs",
                    attempt,
                    _TELEGRAM_SETUP_MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)
    logger.error(
        "Telegram setup failed after %d attempts — giving up", _TELEGRAM_SETUP_MAX_ATTEMPTS
    )
    raise last_exc


def main() -> None:
    _configure_logging()
    logger.info("Drift bot starting")

    # Last-resort references — populated during setup so the crash handler can
    # attempt notifications even if the crash happens before the main loop.
    _crash_config = None
    _crash_bot_app = None

    try:
        try:
            config = load_config()
        except (FileNotFoundError, ValueError) as exc:
            logger.critical("Config error: %s", exc)
            sys.exit(1)

        _crash_config = config

        # User-facing times are rendered in the configured display timezone
        # (default UTC-5).  DB always stores real UTC; this only affects display.
        try:
            set_display_tz(parse_utc_offset(config.reports.timezone))
        except ValueError:
            logger.warning(
                "Invalid reports.timezone %r — falling back to UTC-5",
                config.reports.timezone,
            )

        init_db()

        if not connect(config.broker):
            logger.critical("Cannot connect to MT5 — aborting")
            sys.exit(1)

        # Derive the MT5 server UTC offset once at startup so every scheduler
        # decision uses server time and matches the candle timestamps returned
        # by MT5 (which are also in server time).  ICMarkets is NY-anchored and
        # DST-aware (GMT+2 winter / GMT+3 summer, see D041); this derivation
        # captures the current offset.  KNOWN LIMITATION: it is derived only
        # once, so a process running continuously across a US DST change
        # (≈Mar/Nov) keeps a stale offset until restarted — restart the bot
        # after each DST transition.
        # Union of pairs across all enabled strategies (D054): this is what gets
        # activated in Market Watch and what the offset derivation samples.
        all_pairs = _enabled_pairs(config)

        _server_offset = get_server_utc_offset(all_pairs[0] if all_pairs else "EURUSD")
        logger.info(
            "MT5 server offset: UTC%+d — scheduler aligned to server time",
            round(_server_offset.total_seconds() / 3600),
        )

        _validate_pairs(all_pairs)

        try:
            balance = get_balance()
            equity = get_equity()
        except RuntimeError:
            logger.critical("Cannot get balance/equity after connect — aborting")
            disconnect()
            sys.exit(1)

        with get_connection() as db_conn:
            stats = get_stats(db_conn)
            peak_from_db = stats.get("peak_balance") or 0.0

        # Peak is equity-based: compare current equity against the stored peak so that
        # the drawdown brake always measures equity vs equity-peak (apples-to-apples).
        peak_balance = max(equity, peak_from_db)
        peak_balance_ref: list[float] = [peak_balance]
        logger.info(
            "Starting balance=%.2f equity=%.2f peak_equity=%.2f",
            balance,
            equity,
            peak_balance,
        )

        state = BotState()
        shutdown_event = threading.Event()

        tg_loop = asyncio.new_event_loop()
        global _tg_loop
        _tg_loop = tg_loop

        bot_app = _setup_telegram_resilient(config, state, tg_loop)

        _crash_bot_app = bot_app

        tg_thread = threading.Thread(
            target=_run_telegram_thread,
            args=(bot_app, tg_loop),
            daemon=True,
            name="telegram",
        )
        tg_thread.start()

        monitor_thread = threading.Thread(
            target=_monitoring_loop,
            args=(config, state, peak_balance_ref, shutdown_event, bot_app),
            daemon=True,
            name="monitor",
        )
        monitor_thread.start()

        with get_connection() as db_conn:
            log_event(db_conn, "start", detail="Drift bot started", balance=balance)

        _fire_and_forget(
            notify_bot_status(
                bot_app.bot,
                config.telegram.chat_id,
                "started",
                f"Balance: ${balance:.2f}",
            )
        )

        def _handle_signal(signum, frame) -> None:
            logger.info("Received signal %d — requesting stop", signum)
            state.stop_requested = True

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        logger.info("Drift bot running — multi-strategy engine active")

        # The engine (Step 32b) hosts every enabled strategy, drives the
        # next_wake scheduler (D058), serves deduplicated market data, gates
        # two-level risk (D053), executes/attributes by magic (D052), and
        # handles each strategy's Decision.  It shares _trade_lock with the
        # monitoring thread and reuses the same notification hooks.  All the
        # session-window/timing/state logic that used to live in this loop is now
        # strategy-private (D051).
        engine = Engine(
            config=config,
            state=state,
            peak_balance_ref=peak_balance_ref,
            bot_app=bot_app,
            server_offset=_server_offset,
            trade_lock=_trade_lock,
            shutdown_event=shutdown_event,
            fire_and_forget=_fire_and_forget,
            notify_trade_opened=notify_trade_opened,
            notify_trade_closed=notify_trade_closed,
            notify_bot_status=notify_bot_status,
            notify_error=notify_error,
        )

        try:
            engine.run()
        finally:
            _shutdown(config, state, bot_app, shutdown_event)

            if tg_loop.is_running():
                tg_loop.call_soon_threadsafe(tg_loop.stop)
            tg_thread.join(timeout=5)

    except SystemExit:
        # Clean exits via sys.exit() (e.g. config errors, failed MT5 connect) are
        # intentional — do not treat them as crashes.
        raise

    except BaseException as exc:
        # Last-resort handler: any Python-level exception that escaped all inner
        # handlers (e.g. MemoryError, KeyboardInterrupt reaching this level,
        # or an unforeseen RuntimeError in a dependency).
        #
        # Strategy:
        #   1. Log the full traceback so the crash leaves evidence in drift.log.
        #   2. Attempt a best-effort Telegram notification (non-blocking).
        #   3. Record a DB event if the DB was already initialised.
        #   4. Exit with a non-zero code so the Task Scheduler "Drift" task (which
        #      must be configured to restart on failure) relaunches it.  Never
        #      swallow the exception here.
        logger.exception(
            "FATAL: unhandled exception in main() — process will exit; "
            "Task Scheduler should restart the Drift task: %s",
            exc,
        )

        if _crash_config is not None and _crash_bot_app is not None:
            _fire_and_forget(
                notify_error(
                    _crash_bot_app.bot,
                    _crash_config.telegram.chat_id,
                    f"FATAL crash — bot exiting for Task Scheduler restart. Error: {exc!r}",
                )
            )
            # Brief pause so the notification has a chance to dispatch.
            time.sleep(3)

        try:
            with get_connection() as db_conn:
                log_event(
                    db_conn,
                    "error",
                    detail=f"fatal crash: {type(exc).__name__}: {exc}",
                )
        except Exception:
            logger.warning("Could not write crash event to DB — DB may not be initialised yet")

        raise


if __name__ == "__main__":
    main()
