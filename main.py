"""Drift — autonomous forex trend-following bot.

Entry point. Orchestrates the 4-hour analysis cycle, the continuous monitoring
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
from datetime import datetime, timedelta, timezone
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
    log_signal,
    log_trade,
)
from drift.executor import close_trade, get_open_positions, open_trade
from drift.mt5_client import connect, disconnect, get_balance, get_candles, health_check, reconnect
from drift.risk import calculate_position_size, calculate_sl_tp, check_all_risk
from drift.strategy import Signal, analyze_pair
from drift.telegram_bot import (
    BotState,
    notify_bot_status,
    notify_error,
    notify_trade_closed,
    notify_trade_opened,
    setup_bot,
)
from drift.trailing import process_open_trades

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

H4_CANDLE_TIMES_UTC = [0, 4, 8, 12, 16, 20]
CANDLE_CLOSE_DELAY_SECONDS = 5
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_PROJECT_ROOT = Path(__file__).parent

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
# H4 candle timing helpers
# ---------------------------------------------------------------------------


def _next_h4_close(now: datetime) -> datetime:
    """Return the next H4 candle close time (UTC) strictly after `now`."""
    for hour in H4_CANDLE_TIMES_UTC:
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate > now:
            return candidate
    # All today's closes are in the past — wrap to 00:00 tomorrow.
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow


def _seconds_until(target: datetime) -> float:
    return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())


# ---------------------------------------------------------------------------
# Pip value / position sizing helpers
# ---------------------------------------------------------------------------


def _pip_multiplier(pair: str) -> float:
    """Return the multiplier to convert price distance to pips."""
    return 100.0 if "JPY" in pair.upper() else 10000.0


def _pip_value(pair: str) -> float:
    """Return approximate pip value in USD per standard lot (MVP: 10.0 for all)."""
    return 10.0


# ---------------------------------------------------------------------------
# Analysis cycle (runs once per H4 candle close)
# ---------------------------------------------------------------------------


def _run_analysis_cycle(
    config: DriftConfig,
    state: BotState,
    peak_balance: float,
    bot_app,
) -> float:
    """Analyse all pairs. Returns updated peak_balance."""
    logger.info("=== Analysis cycle start ===")

    try:
        balance = get_balance()
    except RuntimeError:
        logger.exception("Cannot get balance — skipping cycle")
        return peak_balance

    if balance > peak_balance:
        peak_balance = balance
        logger.info("Peak balance updated: %.2f", peak_balance)

    with get_connection() as db_conn:
        open_trades_db = get_open_trades(db_conn)

    for pair in config.pairs:
        if state.paused:
            logger.info("Bot paused — skipping analysis for %s", pair)
            continue
        try:
            _analyse_pair(pair, balance, peak_balance, open_trades_db, config, state, bot_app)
        except Exception:
            logger.exception("Unhandled error analysing %s", pair)

    logger.info("=== Analysis cycle end ===")
    return peak_balance


def _analyse_pair(
    pair: str,
    balance: float,
    peak_balance: float,
    open_trades_db: list[dict],
    config: DriftConfig,
    state: BotState,
    bot_app,
) -> None:
    df_d1 = get_candles(pair, config.strategy.timeframe_trend, count=250)
    df_h4 = get_candles(pair, config.strategy.timeframe_entry, count=100)

    signal: Signal = analyze_pair(pair, df_d1, df_h4, config.strategy)

    with get_connection() as db_conn:
        if signal.action in ("buy", "sell"):
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
                log_signal(db_conn, signal, trade_id=None)
                return

            stop_loss, take_profit = calculate_sl_tp(
                entry_price=0.0,  # placeholder; actual entry determined at open
                direction=signal.action,
                atr_value=signal.atr_value,
                atr_multiplier=config.risk.trailing_stop_atr_multiplier,
                tp_ratio=config.risk.take_profit_ratio,
            )

            pip_mult = _pip_multiplier(pair)
            pip_val = _pip_value(pair)
            sl_pips = signal.atr_value * config.risk.trailing_stop_atr_multiplier * pip_mult

            lot_size = calculate_position_size(
                balance=balance,
                risk_percent=config.risk.percent_per_trade,
                stop_loss_pips=sl_pips,
                pip_value=pip_val,
            )

            if lot_size <= 0:
                logger.warning("%s | position size is 0 — skipping trade", pair)
                log_signal(db_conn, signal, trade_id=None)
                return

            ticket = open_trade(
                pair=pair,
                direction=signal.action,
                lot_size=lot_size,
                stop_loss=stop_loss,
                take_profit=take_profit,
                magic=config.system.magic_number,
            )

            if ticket is None:
                logger.error("%s | open_trade failed", pair)
                log_signal(db_conn, signal, trade_id=None)
                return

            # Fetch the actual fill price from MT5 so we can store it correctly.
            import MetaTrader5 as mt5  # local import — Windows only dependency

            positions = mt5.positions_get(ticket=ticket)
            entry_price = positions[0].price_open if positions else 0.0

            # Recalculate SL/TP around the actual fill price.
            stop_loss, take_profit = calculate_sl_tp(
                entry_price=entry_price,
                direction=signal.action,
                atr_value=signal.atr_value,
                atr_multiplier=config.risk.trailing_stop_atr_multiplier,
                tp_ratio=config.risk.take_profit_ratio,
            )

            trade_id = log_trade(
                db_conn,
                pair=pair,
                direction=signal.action,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size=lot_size,
                balance_at_open=balance,
                mt5_ticket=ticket,
            )

            log_signal(db_conn, signal, trade_id=trade_id)

            risk_usd = balance * config.risk.percent_per_trade / 100
            trade_info = {
                "pair": pair,
                "direction": signal.action,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
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
        else:
            log_signal(db_conn, signal, trade_id=None)


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
            _monitoring_tick(config, state, peak_balance_ref, known_tickets, bot_app)
        except Exception:
            logger.exception("Unhandled error in monitoring loop")
        shutdown_event.wait(timeout=interval)


def _monitoring_tick(
    config: DriftConfig,
    state: BotState,
    peak_balance_ref: list[float],
    known_tickets: set[int],
    bot_app,
) -> None:
    if not health_check():
        logger.warning("MT5 health check failed — attempting reconnect")
        ok = reconnect(config.broker)
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

    if balance > peak_balance_ref[0]:
        peak_balance_ref[0] = balance

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

    _check_friday_close(config, state, mt5_positions, balance, bot_app)


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
            else:
                exit_price = 0.0
                pnl = 0.0

            close_reason = "stop_loss" if pnl < 0 else "take_profit"
            close_trade_record(db_conn, trade["id"], exit_price, pnl, balance, close_reason)

            trade_info = {
                "pair": trade["pair"],
                "direction": trade["direction"],
                "entry_price": trade["entry_price"],
                "exit_price": exit_price,
                "profit_loss": pnl,
                "close_reason": close_reason,
                "duration_minutes": 0,
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
    now = datetime.now(timezone.utc)
    if now.weekday() != 4 or now.hour < config.system.friday_close_hour_utc:
        return

    losing_positions = [p for p in mt5_positions if p.get("profit", 0) < 0]
    if not losing_positions:
        return

    logger.info("Friday close: closing %d losing trades", len(losing_positions))
    for pos in losing_positions:
        success = close_trade(
            ticket=pos["ticket"],
            pair=pos["pair"],
            lot_size=pos["volume"],
            direction=pos["direction"],
            magic=config.system.magic_number,
        )
        if success:
            with get_connection() as db_conn:
                trade = get_trade_by_ticket(db_conn, pos["ticket"])
                if trade and trade.get("closed_at") is None:
                    close_trade_record(
                        db_conn,
                        trade["id"],
                        exit_price=0.0,
                        profit_loss=pos.get("profit", 0.0),
                        balance_at_close=balance,
                        close_reason="friday_close",
                    )
            trade_info = {
                "pair": pos["pair"],
                "direction": pos["direction"],
                "entry_price": pos["price_open"],
                "exit_price": 0.0,
                "profit_loss": pos.get("profit", 0.0),
                "close_reason": "friday_close",
                "duration_minutes": 0,
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

    logger.info("Drift bot running — waiting for first H4 candle close")

    try:
        while not state.stop_requested:
            now = datetime.now(timezone.utc)
            next_close = _next_h4_close(now)
            wait_secs = _seconds_until(next_close) + CANDLE_CLOSE_DELAY_SECONDS
            logger.info(
                "Next H4 close at %s UTC — sleeping %.0fs",
                next_close.strftime("%H:%M"),
                wait_secs,
            )

            deadline = time.monotonic() + wait_secs
            while time.monotonic() < deadline and not state.stop_requested:
                time.sleep(min(10.0, deadline - time.monotonic()))

            if state.stop_requested:
                break

            peak_balance_ref[0] = _run_analysis_cycle(config, state, peak_balance_ref[0], bot_app)
    finally:
        _shutdown(config, state, bot_app, shutdown_event)

        if tg_loop.is_running():
            tg_loop.call_soon_threadsafe(tg_loop.stop)
        tg_thread.join(timeout=5)


if __name__ == "__main__":
    main()
