"""Drift — autonomous forex trend-following bot.

Entry point. Orchestrates the M15 Daily Lull session loop, the continuous monitoring
thread, and the Telegram bot thread.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import math
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
from drift.formatting import parse_utc_offset, set_display_tz
from drift.mt5_client import (
    connect,
    disconnect,
    get_balance,
    get_candles,
    get_equity,
    get_server_utc_offset,
    health_check,
    reconnect,
    server_now,
)
from drift.report import generate_weekly_report
from drift.risk import calculate_position_size, check_all_risk
from drift.strategy import SessionState, Signal, closed_bars, evaluate_pair, should_close_on_time
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

# ---------------------------------------------------------------------------
# M15 session timing helpers
# ---------------------------------------------------------------------------


def _next_m15_close(now: datetime) -> datetime:
    """Return the next M15 boundary strictly after *now* (HH:00, HH:15, HH:30, HH:45).

    *now* must be in MT5 server time so the result aligns with candle close timestamps.
    """
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
    """Return True iff the current MT5 server time is inside an active Daily Lull session.

    *now* must be in MT5 server time (GMT+3 for ICMarkets).  The session hours
    (session_start_hour=21, session_end_hour=2) are defined in server time to match
    candle timestamps and the validated backtest window.

    A session spans `session_start_hour` (21) on day N to `session_end_hour` (2) on
    day N+1.  The session is identified by its start day:

      - Sessions starting Mon-Thu and Sun are valid.
      - Sessions starting Fri are skipped (ICMarkets Sydney close around 22:00 server
        time leaves less than the 2-hour range-definition window).
      - Sessions starting Sat are skipped (market closed).

    So Friday 00:00-01:59 server time is allowed because it is the tail of
    Thursday's session.
    """
    h = now.hour
    start = config.strategy.session_start_hour
    end = config.strategy.session_end_hour

    if h >= start:
        # Tonight is the start of "today's" session — valid unless today is Fri or Sat.
        return now.weekday() not in (4, 5)
    if h < end:
        # We are in the tail of "yesterday's" session — valid unless yesterday was Fri or Sat.
        yesterday_weekday = (now - timedelta(days=1)).weekday()
        return yesterday_weekday not in (4, 5)
    return False


def _next_session_start(now: datetime, config: DriftConfig) -> datetime:
    """Return the next session_start_hour:00 server time strictly after *now*.

    *now* must be in MT5 server time.  The returned datetime is also in server
    time so callers can compute a consistent delta without timezone conversion.

    Skips Friday and Saturday session starts (no market / not enough window).
    Sunday 21:00 server time IS a valid session start — it is the Sydney open
    of the new trading week.  A call on Friday 22:00 server time returns Sunday
    21:00 server time.
    """
    start_hour = config.strategy.session_start_hour
    # Start from current day's session_start_hour candidate
    candidate = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)

    # Skip Friday (4) and Saturday (5) — Sunday (6) is the new week's first session.
    while candidate.weekday() in (4, 5):
        candidate += timedelta(days=1)

    return candidate


def _post_close_delay_seconds(next_close: datetime, config: DriftConfig) -> int:
    """Seconds to wait after an M15 close before evaluating/executing it.

    The candle closing at 00:00 server time coincides with ICMarkets' daily
    rollover halt (~1-2 min of "market closed" / wide spreads). Executing then
    fills into the reopen gap and turned a winning signal into a loss on
    2026-06-03 (see D040/D044). That one candle waits `rollover_settle_seconds`
    so the market reopens and the strategy re-evaluates on a fresh price; if the
    price has gapped away from the extreme, no entry fires. All other candles
    use the normal short delay.
    """
    if next_close.hour == 0 and next_close.minute == 0:
        return config.system.rollover_settle_seconds
    return CANDLE_CLOSE_DELAY_SECONDS


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
# Session close helper (state D)
# ---------------------------------------------------------------------------


def _close_session_trades(
    config: DriftConfig,
    session_states: dict[str, SessionState],
    bot_app,
) -> None:
    """Force-close all open positions for this bot and reset all session states.

    Called at state D (M15 bar whose close stamp hour == session_end_hour).
    """
    with _trade_lock:
        mt5_positions = get_open_positions(config.system.magic_number)
        for pos in mt5_positions:
            try:
                logger.info(
                    "Session close (02:00): closing ticket=%d %s", pos["ticket"], pos["pair"]
                )
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

                    # Read the settled balance fresh from MT5 so it includes
                    # this trade's own P&L and commission (a pre-close snapshot
                    # would omit them). See D048.
                    balance_settled = get_balance()

                    with get_connection() as db_conn:
                        trade = get_trade_by_ticket(db_conn, pos["ticket"])
                        if trade and trade.get("closed_at") is None:
                            close_trade_record(
                                db_conn,
                                trade["id"],
                                exit_price=exit_price,
                                profit_loss=pnl,
                                balance_at_close=balance_settled,
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
                    _fire_and_forget(
                        notify_trade_closed(bot_app.bot, config.telegram.chat_id, trade_info)
                    )
            except Exception:
                logger.exception(
                    "Error closing session position ticket=%d %s — continuing",
                    pos["ticket"],
                    pos["pair"],
                )

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
    equity: float,
    peak_equity: float,
    config: DriftConfig,
    state: BotState,
    session_states: dict[str, SessionState],
    bot_app,
    trading_allowed: bool,
    server_offset: timedelta,
    bar_close_time: datetime,
) -> None:
    """Fetch M15+H4 data and evaluate the pair for the current M15 close.

    Parameters
    ----------
    balance:
        Settled account balance (no floating P&L).  Used only for position
        sizing so that risk-per-trade is computed on realised equity.
    peak_balance:
        Kept for backward compatibility but no longer used directly; the
        drawdown entry gate uses peak_equity instead.
    equity:
        Current account equity (balance + floating P&L).  Passed to
        check_all_risk so the drawdown entry gate matches the monitoring-thread
        brake, which also operates on equity after commit 52d43ad.
    peak_equity:
        Running high-water mark of equity (maintained by _run_m15_tick and the
        monitoring thread via peak_balance_ref).
    trading_allowed:
        True in state C (23:00-01:59), False in state B (21:00-22:59).
        When False, evaluate_pair is still called so it can update session state
        (range accumulation + locking at 23:00 is handled inside evaluate_pair /
        update_session_state), but any resulting buy/sell signal is suppressed.
    """
    m15_df = get_candles(pair, "M15", count=150)
    h4_df = get_candles(pair, "H4", count=50)

    # Evaluate only CLOSED M15 bars so the live bot acts on completed-bar closes
    # like the backtest, not the still-forming (rollover-contaminated) bar (D045).
    m15_df = closed_bars(m15_df, bar_close_time)

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
                log_signal(db_conn, signal, trade_id=None, server_offset=server_offset)
                return

            open_trades_db = get_open_trades(db_conn)
            # Drawdown gate uses equity so it matches the monitoring-thread brake.
            risk_ok, risk_reason = check_all_risk(
                balance=equity,
                peak_balance=peak_equity,
                open_trades=open_trades_db,
                new_pair=pair,
                new_direction=signal.action,
                config=config.risk,
            )
            if not risk_ok:
                logger.info("%s | risk check failed: %s", pair, risk_reason)
                log_signal(
                    db_conn,
                    signal,
                    trade_id=None,
                    rejection_reason=f"risk: {risk_reason}",
                    server_offset=server_offset,
                )
                # Roll back traded flag: risk rejected, so we have not really traded.
                session_states[pair].traded = False
                return

            import MetaTrader5 as mt5

            # Fetch symbol_info once; reuse it for both pip value and volume
            # constraint validation to avoid two IPC calls to the MT5 terminal.
            sym_info = mt5.symbol_info(pair)

            pip_mult = _pip_multiplier(pair)
            pip_val = _pip_value(pair, info=sym_info)
            sl_pips = abs(signal.entry_price - signal.sl) * pip_mult

            lot_size = calculate_position_size(
                balance=balance,
                risk_percent=config.risk.percent_per_trade,
                stop_loss_pips=sl_pips,
                pip_value=pip_val,
            )

            if lot_size <= 0:
                logger.warning("%s | position size is 0 — skipping trade", pair)
                log_signal(
                    db_conn,
                    signal,
                    trade_id=None,
                    rejection_reason="lot_size_zero",
                    server_offset=server_offset,
                )
                session_states[pair].traded = False
                return

            # Validate lot_size against the broker's real volume constraints for
            # this symbol.  risk.py returns a mathematically correct raw value but
            # deliberately knows nothing about MT5 — the adjustment lives here
            # where symbol_info is already available.
            if sym_info is not None:
                vol_step = sym_info.volume_step
                vol_min = sym_info.volume_min
                vol_max = sym_info.volume_max

                # Round down to the nearest volume_step.
                if vol_step > 0:
                    adjusted = round(math.floor(lot_size / vol_step) * vol_step, 10)
                    if adjusted != lot_size:
                        logger.info(
                            "%s | lot_size %.4f rounded down to %.4f (volume_step=%.4f)",
                            pair,
                            lot_size,
                            adjusted,
                            vol_step,
                        )
                    lot_size = adjusted

                # Skip if still below volume_min.
                if lot_size < vol_min:
                    logger.warning(
                        "%s | lot_size %.4f is below volume_min %.4f — skipping trade",
                        pair,
                        lot_size,
                        vol_min,
                    )
                    log_signal(
                        db_conn,
                        signal,
                        trade_id=None,
                        rejection_reason="lot_below_min",
                        server_offset=server_offset,
                    )
                    session_states[pair].traded = False
                    return

                # Cap to volume_max.  A capped trade still respects risk on the
                # downside (actual risk is lower than targeted); skipping would be
                # overly conservative for a small max.
                if lot_size > vol_max:
                    logger.warning(
                        "%s | lot_size %.4f exceeds volume_max %.4f — capping",
                        pair,
                        lot_size,
                        vol_max,
                    )
                    lot_size = vol_max

            ticket = open_trade(
                pair=pair,
                direction=signal.action,
                lot_size=lot_size,
                stop_loss=signal.sl,
                take_profit=signal.tp,
                magic=config.system.magic_number,
                max_retries=config.system.order_retry_attempts,
                retry_delay_seconds=config.system.order_retry_delay_seconds,
                guard_boundary=(signal.range_low if signal.action == "buy" else signal.range_high),
                entry_reference=signal.entry_price,
                min_reward_fraction=config.system.min_reward_fraction,
            )

            if ticket is None:
                logger.error("%s | open_trade failed", pair)
                log_signal(db_conn, signal, trade_id=None, server_offset=server_offset)
                session_states[pair].traded = False
                return

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

            log_signal(db_conn, signal, trade_id=trade_id, server_offset=server_offset)

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
    server_offset: timedelta,
) -> None:
    """Process one M15 candle close.

    Dispatches to state B (range definition), C (trading), or D (session close).
    """
    hour = bar_close_time.hour

    # State D — session close at session_end_hour (02:00 server time)
    if should_close_on_time(bar_close_time, config.strategy):
        logger.info(
            "=== Session close (state D) at %s server time ===",
            bar_close_time.strftime("%H:%M"),
        )
        _close_session_trades(config, session_states, bot_app)
        _fire_and_forget(
            notify_bot_status(
                bot_app.bot,
                config.telegram.chat_id,
                "stopped",
                "Daily Lull session closed (02:00 server time) — sleeping until next session",
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
        "=== M15 tick (state %s) %s server time ===",
        "C" if trading_allowed else "B",
        bar_close_time.strftime("%H:%M"),
    )

    try:
        balance = get_balance()
        equity = get_equity()
    except RuntimeError:
        logger.exception("Cannot get balance/equity — skipping M15 tick")
        return

    if equity > peak_balance_ref[0]:
        peak_balance_ref[0] = equity
        logger.info("Peak equity updated: %.2f", peak_balance_ref[0])
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
                equity=equity,
                peak_equity=peak_balance_ref[0],
                config=config,
                state=state,
                session_states=session_states,
                bot_app=bot_app,
                trading_allowed=trading_allowed,
                server_offset=server_offset,
                bar_close_time=bar_close_time,
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

        drawdown_ok, dd_reason = _check_drawdown_pause(
            equity, peak_balance_ref[0], config, state, bot_app
        )
        if not drawdown_ok:
            return

        mt5_positions = get_open_positions(config.system.magic_number)
        mt5_tickets = {p["ticket"] for p in mt5_positions}

        pending_tickets = _detect_closed_trades(known_tickets, mt5_tickets, config, bot_app)

        # Re-add tickets whose close deal was not yet available so they are
        # retried in the next monitoring cycle (~30s).
        known_tickets.clear()
        known_tickets |= mt5_tickets | pending_tickets

        if mt5_positions:
            process_open_trades(mt5_positions, config.risk)

    _check_weekly_report(config, balance, peak_balance_ref[0], bot_app)


def _check_drawdown_pause(
    equity: float,
    peak_equity: float,
    config: DriftConfig,
    state: BotState,
    bot_app,
) -> tuple[bool, str]:
    from drift.risk import check_drawdown

    ok, reason = check_drawdown(equity, peak_equity, config.risk.max_drawdown_percent)
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
            }
            _fire_and_forget(notify_trade_closed(bot_app.bot, config.telegram.chat_id, trade_info))
            logger.info(
                "Detected closed trade ticket=%d pair=%s pnl=%.2f reason=%s",
                ticket,
                trade["pair"],
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
        _server_offset = get_server_utc_offset(config.pairs[0] if config.pairs else "EURUSD")
        logger.info(
            "MT5 server offset: UTC%+d — scheduler aligned to server time",
            round(_server_offset.total_seconds() / 3600),
        )

        _validate_pairs(config.pairs)

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

        # Per-pair session state — reset at the start of each session (21:00 server time).
        session_states: dict[str, SessionState] = {pair: SessionState() for pair in config.pairs}

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

        logger.info("Drift bot running — M15 Daily Lull session scheduler active")

        # Track whether we are currently inside a session to detect the B→A transition.
        _session_active: bool = False

        try:
            while not state.stop_requested:
                try:
                    # All session-window decisions use MT5 server time so they
                    # align with the candle timestamps and the strategy's hour
                    # checks (session_start_hour=21, session_end_hour=2 are
                    # defined in server time).  The weekly-report trigger in
                    # _check_weekly_report continues to use real UTC so it fires
                    # on the user's configured wall-clock schedule.
                    now = server_now(_server_offset)

                    # --- State A: outside window — sleep until next session start ---
                    if not _in_session_window(now, config):
                        if _session_active:
                            # Transitioned from inside → outside — session already closed at 02:00.
                            _session_active = False

                        next_start = _next_session_start(now, config)
                        wait_secs = (
                            _seconds_until(next_start, _server_offset) + CANDLE_CLOSE_DELAY_SECONDS
                        )
                        logger.info(
                            "Outside window (state A) — sleeping until %s server time (%.0fs)",
                            next_start.strftime("%Y-%m-%d %H:%M"),
                            wait_secs,
                        )

                        deadline = time.monotonic() + wait_secs
                        while time.monotonic() < deadline and not state.stop_requested:
                            time.sleep(min(10.0, deadline - time.monotonic()))

                        if state.stop_requested:
                            break

                        # Refresh now; re-check window.
                        continue

                    # --- Entering session for the first time (B starts) ---
                    if not _session_active:
                        # Re-derive the server offset at each session start so a
                        # long-running process picks up US DST transitions
                        # (≈Mar/Nov) without a restart (D041).  Cheap: once per
                        # session.  If it changed, realign by recomputing `now`
                        # before doing anything window-dependent.
                        refreshed = get_server_utc_offset(
                            config.pairs[0] if config.pairs else "EURUSD"
                        )
                        if refreshed != _server_offset:
                            logger.warning(
                                "MT5 server offset changed UTC%+d → UTC%+d (DST?) — "
                                "realigning scheduler",
                                round(_server_offset.total_seconds() / 3600),
                                round(refreshed.total_seconds() / 3600),
                            )
                            _server_offset = refreshed
                            continue

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
                                "Daily Lull session started (21:00 server time)",
                            )
                        )

                    # --- States B + C + D: wait for next M15 close and process tick ---
                    next_close = _next_m15_close(now)
                    post_close_delay = _post_close_delay_seconds(next_close, config)
                    wait_secs = _seconds_until(next_close, _server_offset) + post_close_delay
                    logger.info(
                        "Next M15 close at %s server time — sleeping %.0fs%s",
                        next_close.strftime("%H:%M"),
                        wait_secs,
                        " (rollover settle)"
                        if post_close_delay != CANDLE_CLOSE_DELAY_SECONDS
                        else "",
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
                        server_offset=_server_offset,
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
