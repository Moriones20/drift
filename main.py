"""Drift — autonomous forex trend-following bot.

Entry point. Orchestrates the M15 Asian session loop, the continuous monitoring
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

from drift.config import DriftConfig, load_config
from drift.db import (
    close_trade_record,
    get_connection,
    get_open_trades,
    get_stats,
    get_trade_by_ticket,
    init_db,
    log_event,
    log_peak_balance,
    log_signal,
    log_trade,
)
from drift.executor import close_trade, get_open_positions, open_trade
from drift.mt5_client import connect, disconnect, get_balance, get_candles, health_check, reconnect
from drift.report import generate_weekly_report
from drift.risk import calculate_position_size, check_all_risk
from drift.strategy import SessionState, Signal, evaluate_pair, should_close_on_time
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

CANDLE_CLOSE_DELAY_SECONDS = 5
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_PROJECT_ROOT = Path(__file__).parent

# Weekly report: sent Monday 01:00 UTC (= Sunday 8pm UTC-5).
# Tracks the date of the last sent report to avoid duplicate sends.
_last_report_date: date | None = None

# Friday close: kept for reference; call removed from _monitoring_tick (Step 6).
_friday_closed: bool = False

# Prevents race conditions between the main analysis cycle and the monitoring thread.
_trade_lock = threading.Lock()

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


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# M15 session timing helpers
# ---------------------------------------------------------------------------


def _next_m15_close(now: datetime) -> datetime:
    """Return the next M15 boundary strictly after *now* (HH:00, HH:15, HH:30, HH:45). UTC."""
    minute = now.minute
    # Compute next 15-minute slot strictly after current minute
    next_slot = ((minute // 15) + 1) * 15
    if next_slot < 60:
        candidate = now.replace(minute=next_slot, second=0, microsecond=0)
    else:
        # Roll over to next hour
        next_hour = now + timedelta(hours=1)
        candidate = next_hour.replace(minute=0, second=0, microsecond=0)
    return candidate


def _in_session_window(now: datetime, config: DriftConfig) -> bool:
    """Return True iff the current UTC time is inside the Asian session trading window.

    Session runs from session_start_hour (21) through session_end_hour (2), wrapping
    midnight.  Friday is always excluded: ICMarkets closes around 22:00 UTC Friday
    (Sydney close), leaving less than the 2-hour range-definition window.
    """
    if now.weekday() == 4:  # Friday
        return False
    h = now.hour
    start = config.strategy.session_start_hour
    end = config.strategy.session_end_hour
    # Wraps midnight: active if hour >= start OR hour < end
    return h >= start or h < end


def _next_session_start(now: datetime, config: DriftConfig) -> datetime:
    """Return the next session_start_hour:00 UTC strictly after *now*, skipping Friday.

    A call on Friday 22:00 UTC returns Monday 21:00 UTC (skips Saturday/Sunday too
    since the session does not exist on weekends; Monday is the first valid slot).
    """
    start_hour = config.strategy.session_start_hour
    # Start from current day's session_start_hour candidate
    candidate = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)

    # Advance past Friday (weekday 4) — Friday sessions are skipped entirely.
    # Also skip Saturday (5) and Sunday (6) as there is no market.
    while candidate.weekday() in (4, 5, 6):
        candidate += timedelta(days=1)

    return candidate


def _seconds_until(target: datetime) -> float:
    return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())


# ---------------------------------------------------------------------------
# Pip value / position sizing helpers
# ---------------------------------------------------------------------------


def _pip_multiplier(pair: str) -> float:
    """Return the multiplier to convert price distance to pips."""
    return 100.0 if "JPY" in pair.upper() else 10000.0


def _pip_value(pair: str) -> float:
    """Return USD pip value per standard lot using MT5 symbol info.

    Uses `trade_tick_value` (USD per tick on one standard lot) and converts to
    pips: 1 pip = 10 ticks on both 5-digit (most pairs) and 3-digit (JPY) brokers.

    Falls back to $10 if MT5 has no info for the pair, but this is a safety net,
    not a default — every configured pair should resolve through MT5 in practice.
    """
    import MetaTrader5 as mt5

    info = mt5.symbol_info(pair)
    if info is None or info.trade_tick_value <= 0:
        logger.warning("No tick_value for %s — falling back to $10/pip/lot", pair)
        return 10.0
    return info.trade_tick_value * 10.0


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------


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
            logger.debug(
                "Pair %s OK (digits=%d, pip_value=$%.2f/lot)",
                pair,
                info.digits,
                info.trade_tick_value * 10,
            )


# ---------------------------------------------------------------------------
# Session close helper (state D)
# ---------------------------------------------------------------------------


def _close_session_trades(
    config: DriftConfig,
    session_states: dict[str, SessionState],
    balance: float,
    bot_app,
) -> None:
    """Force-close all open positions for this bot and reset all session states.

    Called at state D (M15 bar whose close stamp hour == session_end_hour).
    """
    mt5_positions = get_open_positions(config.system.magic_number)
    for pos in mt5_positions:
        logger.info("Session close (02:00): closing ticket=%d %s", pos["ticket"], pos["pair"])
        success = close_trade(
            ticket=pos["ticket"],
            pair=pos["pair"],
            lot_size=pos["volume"],
            direction=pos["direction"],
            magic=config.system.magic_number,
        )
        if success:
            import MetaTrader5 as mt5

            exit_price = 0.0
            pnl = pos.get("profit", 0.0)
            deals = mt5.history_deals_get(position=pos["ticket"])
            if deals:
                close_deal = deals[-1]
                exit_price = close_deal.price
                pnl = close_deal.profit

            with get_connection() as db_conn:
                trade = get_trade_by_ticket(db_conn, pos["ticket"])
                if trade and trade.get("closed_at") is None:
                    close_trade_record(
                        db_conn,
                        trade["id"],
                        exit_price=exit_price,
                        profit_loss=pnl,
                        balance_at_close=balance,
                        close_reason="session_close",
                    )
                    updated = get_trade_by_ticket(db_conn, pos["ticket"])
                    duration = (updated["duration_minutes"] or 0) if updated else 0
                else:
                    duration = 0

            trade_info = {
                "pair": pos["pair"],
                "direction": pos["direction"],
                "entry_price": pos.get("price_open", 0.0),
                "exit_price": exit_price,
                "profit_loss": pnl,
                "close_reason": "session_close",
                "duration_minutes": duration,
            }
            _fire_and_forget(notify_trade_closed(bot_app.bot, config.telegram.chat_id, trade_info))

    # Reset all session states — next session starts fresh at 21:00.
    for pair in list(session_states.keys()):
        session_states[pair] = SessionState()
    logger.info("Session states reset after session close")


# ---------------------------------------------------------------------------
# Per-pair M15 analysis (states B and C)
# ---------------------------------------------------------------------------


def _analyse_pair_m15(
    pair: str,
    balance: float,
    peak_balance: float,
    config: DriftConfig,
    state: BotState,
    session_states: dict[str, SessionState],
    bot_app,
    trading_allowed: bool,
) -> None:
    """Fetch M15+H4 data and evaluate the pair for the current M15 close.

    Parameters
    ----------
    trading_allowed:
        True in state C (23:00-01:59), False in state B (21:00-22:59).
        When False, evaluate_pair is still called so it can update session state
        (range accumulation + locking at 23:00 is handled inside evaluate_pair /
        update_session_state), but any resulting buy/sell signal is suppressed.
    """
    m15_df = get_candles(pair, "M15", count=150)
    h4_df = get_candles(pair, "H4", count=50)

    signal: Signal = evaluate_pair(
        symbol=pair,
        m15_df=m15_df,
        h4_df=h4_df,
        session_state=session_states[pair],
        config=config.strategy,
    )

    with _trade_lock:
        with get_connection() as db_conn:
            if not trading_allowed or signal.action not in ("buy", "sell"):
                log_signal(db_conn, signal, trade_id=None)
                return

            open_trades_db = get_open_trades(db_conn)
            risk_ok, risk_reason = check_all_risk(
                balance=balance,
                peak_balance=peak_balance,
                open_trades=open_trades_db,
                new_pair=pair,
                new_direction=signal.action,
                config=config.risk,
            )
            if not risk_ok:
                logger.info("%s | risk check failed: %s", pair, risk_reason)
                log_signal(db_conn, signal, trade_id=None, rejection_reason=f"risk: {risk_reason}")
                # Roll back traded flag: risk rejected, so we have not really traded.
                session_states[pair].traded = False
                return

            pip_mult = _pip_multiplier(pair)
            pip_val = _pip_value(pair)
            sl_pips = abs(signal.entry_price - signal.sl) * pip_mult

            lot_size = calculate_position_size(
                balance=balance,
                risk_percent=config.risk.percent_per_trade,
                stop_loss_pips=sl_pips,
                pip_value=pip_val,
            )

            if lot_size <= 0:
                logger.warning("%s | position size is 0 — skipping trade", pair)
                log_signal(db_conn, signal, trade_id=None, rejection_reason="lot_size_zero")
                session_states[pair].traded = False
                return

            ticket = open_trade(
                pair=pair,
                direction=signal.action,
                lot_size=lot_size,
                stop_loss=signal.sl,
                take_profit=signal.tp,
                magic=config.system.magic_number,
            )

            if ticket is None:
                logger.error("%s | open_trade failed", pair)
                log_signal(db_conn, signal, trade_id=None)
                session_states[pair].traded = False
                return

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
            )

            log_signal(db_conn, signal, trade_id=trade_id)

            # session_states[pair].traded is already True (set inside evaluate_pair).

            risk_usd = balance * config.risk.percent_per_trade / 100
            trade_info = {
                "pair": pair,
                "direction": signal.action,
                "entry_price": entry_price,
                "stop_loss": signal.sl,
                "take_profit": signal.tp,
                "position_size": lot_size,
                "risk_usd": risk_usd,
                "risk_pct": config.risk.percent_per_trade,
            }
            _fire_and_forget(notify_trade_opened(bot_app.bot, config.telegram.chat_id, trade_info))
            logger.info(
                "%s | trade opened ticket=%d direction=%s lot=%.2f",
                pair,
                ticket,
                signal.action,
                lot_size,
            )


# ---------------------------------------------------------------------------
# M15 session cycle (states B + C + D) — called once per M15 candle close
# ---------------------------------------------------------------------------


def _run_m15_tick(
    config: DriftConfig,
    state: BotState,
    peak_balance_ref: list[float],
    session_states: dict[str, SessionState],
    bot_app,
    bar_close_time: datetime,
) -> None:
    """Process one M15 candle close.

    Dispatches to state B (range definition), C (trading), or D (session close).
    """
    hour = bar_close_time.hour

    # State D — session close at session_end_hour (02:00 UTC)
    if should_close_on_time(bar_close_time, config.strategy):
        logger.info("=== Session close (state D) at %s UTC ===", bar_close_time.strftime("%H:%M"))
        try:
            balance = get_balance()
        except RuntimeError:
            logger.exception("Cannot get balance for session close — using peak as fallback")
            balance = peak_balance_ref[0]

        _close_session_trades(config, session_states, balance, bot_app)
        _fire_and_forget(
            notify_bot_status(
                bot_app.bot,
                config.telegram.chat_id,
                "stopped",
                "Asian session closed (02:00 UTC) — sleeping until next session",
            )
        )
        return

    # States B (21:00-22:59) and C (23:00-01:59)
    # Determine whether entries are allowed (state C only)
    trading_allowed = hour in (23, 0, 1)

    if not (hour in (21, 22) or trading_allowed):
        # Outside the active window — should not normally reach here because the
        # main loop only calls this function during the session window, but guard
        # defensively.
        return

    logger.info(
        "=== M15 tick (state %s) %s UTC ===",
        "C" if trading_allowed else "B",
        bar_close_time.strftime("%H:%M"),
    )

    try:
        balance = get_balance()
    except RuntimeError:
        logger.exception("Cannot get balance — skipping M15 tick")
        return

    if balance > peak_balance_ref[0]:
        peak_balance_ref[0] = balance
        logger.info("Peak balance updated: %.2f", peak_balance_ref[0])
        with get_connection() as db_conn:
            log_peak_balance(db_conn, peak_balance_ref[0])

    for pair in config.pairs:
        if state.paused:
            logger.info("Bot paused — skipping pair %s", pair)
            continue
        try:
            _analyse_pair_m15(
                pair=pair,
                balance=balance,
                peak_balance=peak_balance_ref[0],
                config=config,
                state=state,
                session_states=session_states,
                bot_app=bot_app,
                trading_allowed=trading_allowed,
            )
        except Exception:
            logger.exception("Unhandled error analysing %s", pair)


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


def _check_weekly_report(
    config: DriftConfig,
    balance: float,
    peak_balance: float,
    bot_app,
) -> None:
    """Send the weekly report if it's Monday 01:xx UTC and not yet sent today."""
    global _last_report_date

    now = datetime.now(timezone.utc)
    today = now.date()

    if now.weekday() != 0 or now.hour != 1:
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
        ok = reconnect(config.broker, shutdown_event=shutdown_event)
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
    except RuntimeError:
        logger.exception("Cannot get balance in monitoring tick")
        return

    with _trade_lock:
        if balance > peak_balance_ref[0]:
            peak_balance_ref[0] = balance
            with get_connection() as db_conn:
                log_peak_balance(db_conn, peak_balance_ref[0])

        drawdown_ok, dd_reason = _check_drawdown_pause(
            balance, peak_balance_ref[0], config, state, bot_app
        )
        if not drawdown_ok:
            return

        mt5_positions = get_open_positions(config.system.magic_number)
        mt5_tickets = {p["ticket"] for p in mt5_positions}

        _detect_closed_trades(known_tickets, mt5_tickets, config, balance, bot_app)

        known_tickets.clear()
        known_tickets.update(mt5_tickets)

        if mt5_positions:
            process_open_trades(mt5_positions, config.risk)

    _check_weekly_report(config, balance, peak_balance_ref[0], bot_app)


def _check_drawdown_pause(
    balance: float,
    peak_balance: float,
    config: DriftConfig,
    state: BotState,
    bot_app,
) -> tuple[bool, str]:
    from drift.risk import check_drawdown

    ok, reason = check_drawdown(balance, peak_balance, config.risk.max_drawdown_percent)
    if not ok and not state.paused:
        state.paused = True
        logger.warning("Drawdown limit reached: %s — bot paused", reason)
        with get_connection() as db_conn:
            log_event(db_conn, "drawdown_alert", detail=reason, balance=balance)
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


def _detect_closed_trades(
    known_tickets: set[int],
    mt5_tickets: set[int],
    config: DriftConfig,
    balance: float,
    bot_app,
) -> None:
    """Find tickets that were open last cycle but are gone now — MT5 closed them (SL/TP)."""
    closed_tickets = known_tickets - mt5_tickets
    if not closed_tickets:
        return

    for ticket in closed_tickets:
        with get_connection() as db_conn:
            trade = get_trade_by_ticket(db_conn, ticket)
            if trade is None or trade.get("closed_at") is not None:
                continue

            import MetaTrader5 as mt5

            deals = mt5.history_deals_get(position=ticket)
            if deals:
                close_deal = deals[-1]
                exit_price = close_deal.price
                pnl = close_deal.profit
                if close_deal.reason == mt5.DEAL_REASON_SL:
                    close_reason = "stop_loss"
                elif close_deal.reason == mt5.DEAL_REASON_TP:
                    close_reason = "take_profit"
                else:
                    close_reason = "trailing_stop" if pnl > 0 else "stop_loss"
            else:
                exit_price = 0.0
                pnl = 0.0
                close_reason = "stop_loss"

            close_trade_record(db_conn, trade["id"], exit_price, pnl, balance, close_reason)

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
            }
            _fire_and_forget(notify_trade_closed(bot_app.bot, config.telegram.chat_id, trade_info))
            logger.info(
                "Detected closed trade ticket=%d pair=%s pnl=%.2f reason=%s",
                ticket,
                trade["pair"],
                pnl,
                close_reason,
            )


def _check_friday_close(
    config: DriftConfig,
    state: BotState,
    mt5_positions: list[dict],
    balance: float,
    bot_app,
) -> None:
    """Friday close logic — kept for reference but not called from _monitoring_tick.

    The Asian Session Scalper never opens positions on Friday (skip-Friday rule in
    _in_session_window), so there should be no open trades to close on Friday.
    The call site was removed from _monitoring_tick in Step 6 to avoid a race with
    the 02:00 session-close trigger.  Function retained here in case a future
    strategy needs it.
    """
    global _friday_closed

    now = datetime.now(timezone.utc)

    if now.weekday() == 0 and _friday_closed:
        _friday_closed = False

    if now.weekday() != 4 or now.hour < config.system.friday_close_hour_utc:
        return

    if _friday_closed:
        return

    recent_threshold = now - timedelta(hours=4)
    positions_to_close = [
        p
        for p in mt5_positions
        if p.get("profit", 0) < 0 or p.get("time_open", now) >= recent_threshold
    ]
    if not positions_to_close:
        return

    _friday_closed = True

    logger.info(
        "Friday close: closing %d trades (losing or recently opened)",
        len(positions_to_close),
    )
    for pos in positions_to_close:
        success = close_trade(
            ticket=pos["ticket"],
            pair=pos["pair"],
            lot_size=pos["volume"],
            direction=pos["direction"],
            magic=config.system.magic_number,
        )
        if success:
            import MetaTrader5 as mt5

            exit_price = 0.0
            pnl = pos.get("profit", 0.0)
            deals = mt5.history_deals_get(position=pos["ticket"])
            if deals:
                close_deal = deals[-1]
                exit_price = close_deal.price
                pnl = close_deal.profit

            duration = 0
            with get_connection() as db_conn:
                trade = get_trade_by_ticket(db_conn, pos["ticket"])
                if trade and trade.get("closed_at") is None:
                    close_trade_record(
                        db_conn,
                        trade["id"],
                        exit_price=exit_price,
                        profit_loss=pnl,
                        balance_at_close=balance,
                        close_reason="friday_close",
                    )
                    updated = get_trade_by_ticket(db_conn, pos["ticket"])
                    duration = (updated["duration_minutes"] or 0) if updated else 0
            trade_info = {
                "pair": pos["pair"],
                "direction": pos["direction"],
                "entry_price": pos["price_open"],
                "exit_price": exit_price,
                "profit_loss": pnl,
                "close_reason": "friday_close",
                "duration_minutes": duration,
            }
            _fire_and_forget(notify_trade_closed(bot_app.bot, config.telegram.chat_id, trade_info))


# ---------------------------------------------------------------------------
# Telegram thread
# ---------------------------------------------------------------------------


def _run_telegram_thread(bot_app, loop: asyncio.AbstractEventLoop) -> None:
    """Run python-telegram-bot polling in a dedicated thread with its own event loop."""
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_app.run_polling(close_loop=False, stop_signals=None))


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
        mt5_positions = get_open_positions(config.system.magic_number)
        for pos in mt5_positions:
            logger.info("Stop requested — closing ticket=%d %s", pos["ticket"], pos["pair"])
            close_trade(
                ticket=pos["ticket"],
                pair=pos["pair"],
                lot_size=pos["volume"],
                direction=pos["direction"],
                magic=config.system.magic_number,
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


def main() -> None:
    _configure_logging()
    logger.info("Drift bot starting")

    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as exc:
        logger.critical("Config error: %s", exc)
        sys.exit(1)

    init_db()

    if not connect(config.broker):
        logger.critical("Cannot connect to MT5 — aborting")
        sys.exit(1)

    _validate_pairs(config.pairs)

    try:
        balance = get_balance()
    except RuntimeError:
        logger.critical("Cannot get balance after connect — aborting")
        disconnect()
        sys.exit(1)

    with get_connection() as db_conn:
        stats = get_stats(db_conn)
        peak_from_db = stats.get("peak_balance") or 0.0

    peak_balance = max(balance, peak_from_db)
    peak_balance_ref: list[float] = [peak_balance]
    logger.info("Starting balance=%.2f peak_balance=%.2f", balance, peak_balance)

    # Per-pair session state — reset at the start of each session (21:00 UTC).
    session_states: dict[str, SessionState] = {pair: SessionState() for pair in config.pairs}

    state = BotState()
    shutdown_event = threading.Event()

    tg_loop = asyncio.new_event_loop()
    global _tg_loop
    _tg_loop = tg_loop

    bot_app = tg_loop.run_until_complete(
        setup_bot(
            token=config.telegram.bot_token,
            chat_id=config.telegram.chat_id,
            state=state,
        )
    )
    tg_loop.run_until_complete(bot_app.initialize())

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

    logger.info("Drift bot running — M15 Asian session scheduler active")

    # Track whether we are currently inside a session to detect the B→A transition.
    _session_active: bool = False

    try:
        while not state.stop_requested:
            try:
                now = datetime.now(timezone.utc)

                # --- State A: outside window — sleep until next session start ---
                if not _in_session_window(now, config):
                    if _session_active:
                        # Transitioned from inside → outside — session already closed at 02:00.
                        _session_active = False

                    next_start = _next_session_start(now, config)
                    wait_secs = _seconds_until(next_start) + CANDLE_CLOSE_DELAY_SECONDS
                    logger.info(
                        "Outside window (state A) — sleeping until %s UTC (%.0fs)",
                        next_start.strftime("%Y-%m-%d %H:%M"),
                        wait_secs,
                    )

                    deadline = time.monotonic() + wait_secs
                    while time.monotonic() < deadline and not state.stop_requested:
                        time.sleep(min(10.0, deadline - time.monotonic()))

                    if state.stop_requested:
                        break

                    # Refresh now; re-check window (DST edge, etc.)
                    continue

                # --- Entering session for the first time (B starts) ---
                if not _session_active:
                    _session_active = True
                    # Reset all session states at session start.
                    for pair in config.pairs:
                        session_states[pair] = SessionState()
                    logger.info("Session started — all session states reset")
                    _fire_and_forget(
                        notify_bot_status(
                            bot_app.bot,
                            config.telegram.chat_id,
                            "started",
                            "Asian session started (21:00 UTC)",
                        )
                    )

                # --- States B + C + D: wait for next M15 close and process tick ---
                next_close = _next_m15_close(now)
                wait_secs = _seconds_until(next_close) + CANDLE_CLOSE_DELAY_SECONDS
                logger.info(
                    "Next M15 close at %s UTC — sleeping %.0fs",
                    next_close.strftime("%H:%M"),
                    wait_secs,
                )

                deadline = time.monotonic() + wait_secs
                while time.monotonic() < deadline and not state.stop_requested:
                    time.sleep(min(10.0, deadline - time.monotonic()))

                if state.stop_requested:
                    break

                bar_close_time = next_close
                _run_m15_tick(
                    config=config,
                    state=state,
                    peak_balance_ref=peak_balance_ref,
                    session_states=session_states,
                    bot_app=bot_app,
                    bar_close_time=bar_close_time,
                )

                # After state D, the session is over — next iteration will detect
                # outside-window and enter state A.

            except Exception:
                logger.exception("Unexpected error in main loop — pausing bot and retrying")
                state.paused = True
                _fire_and_forget(
                    notify_error(
                        bot_app.bot,
                        config.telegram.chat_id,
                        "Unexpected error in main loop — bot paused. Use /resume after investigation.",  # noqa: E501
                    )
                )
                time.sleep(60)
    finally:
        _shutdown(config, state, bot_app, shutdown_event)

        if tg_loop.is_running():
            tg_loop.call_soon_threadsafe(tg_loop.stop)
        tg_thread.join(timeout=5)


if __name__ == "__main__":
    main()
