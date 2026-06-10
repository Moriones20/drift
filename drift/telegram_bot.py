from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram import Bot, BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from drift.config import DriftConfig
from drift.db import (
    get_connection,
    get_open_trades,
    get_recent_trades,
    get_stats,
    get_strategy_state,
    reset_strategy_peak,
    set_strategy_paused,
)
from drift.executor import get_open_positions
from drift.formatting import format_duration, format_time, pnl_str
from drift.mt5_client import get_balance, health_check
from drift.risk import strategy_drawdown, strategy_equity

logger = logging.getLogger(__name__)


class BotState:
    def __init__(self) -> None:
        self.paused: bool = False
        self.stop_requested: bool = False
        self.start_time: datetime = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SEP = "━" * 28

_REASON_LABELS = {
    "take_profit": "Take profit hit",
    "stop_loss": "Stopped out",
    "trailing_stop": "Trailing stop hit",
    "manual": "Manual close",
    "drawdown_pause": "Drawdown limit reached",
    "session_close": "Session close (02:00)",
}


def _uptime(start_time: datetime) -> str:
    delta = datetime.now(timezone.utc) - start_time
    return format_duration(int(delta.total_seconds() // 60))


def _pip_multiplier(pair: str) -> int:
    """Return the pip multiplier for a pair (100 for JPY crosses, 10000 otherwise)."""
    return 100 if "JPY" in pair.upper() else 10000


def _price_decimals(pair: str) -> int:
    """Return the number of decimals to display prices with (3 for JPY, 5 otherwise)."""
    return 3 if "JPY" in pair.upper() else 5


def _fmt_price(price: float, pair: str) -> str:
    """Format a price using the right decimals for the pair."""
    return f"{price:.{_price_decimals(pair)}f}"


def _pips(entry: float, level: float, pair: str = "", direction: str = "buy") -> str:
    """Pip distance from entry to level, signed by trade direction.

    Positive = favorable to the trade (TP-side or profit), negative = adverse (SL-side or loss).
    """
    mult = _pip_multiplier(pair)
    raw = (level - entry) * mult
    if direction.lower() == "sell":
        raw = -raw
    diff = round(raw)
    return f"+{diff}" if diff >= 0 else str(diff)


def _reason_label(reason: str) -> str:
    """Translate an internal close-reason code into a friendly label."""
    return _REASON_LABELS.get(reason, reason.replace("_", " ").capitalize())


def _effective_magic(config: DriftConfig, strategy_name: str) -> int:
    """Return the effective magic number (base + offset) for a named strategy."""
    strat = config.strategies[strategy_name]
    return config.system.magic_number + strat.magic_offset


def _strategy_equity_info(
    conn,
    config: DriftConfig,
    name: str,
    state_row: dict | None,
    realized: float | None = None,
) -> tuple[float | None, float | None, float | None]:
    """Compute canonical per-strategy equity, peak and drawdown % (D062).

    Uses the persisted baseline model: equity = baseline_capital + realized + floating.
    This matches the brake in the engine so what the user sees equals what trips the
    halt — fixing the incoherent display (audit #4).

    Returns ``(equity, peak, drawdown_pct)`` where all three are None when
    ``baseline_capital`` has not been seeded yet (strategy never evaluated).
    ``drawdown_pct`` is 0.0 when at or above peak (clamped via strategy_drawdown).

    Pass ``realized`` when the caller already has it from ``get_stats`` to avoid a
    redundant DB query.  Omit (or pass None) to let the helper fetch it.

    This is READ-ONLY: it never calls evaluate_strategy_drawdown (which mutates
    baseline / peak).
    """
    if state_row is None:
        return None, None, None
    baseline = state_row.get("baseline_capital")
    if baseline is None:
        return None, None, None
    peak = state_row.get("peak_equity") or 0.0

    if realized is None:
        realized = get_stats(conn, strategy=name).get("total_pnl") or 0.0
    magic = _effective_magic(config, name)
    try:
        positions = get_open_positions(magic)
        floating = sum(p["profit"] for p in positions)
    except Exception:
        floating = 0.0

    equity = strategy_equity(baseline, realized, floating)
    drawdown_pct = strategy_drawdown(equity, peak) * 100
    return equity, peak, drawdown_pct


def _strategy_paused_flag(strategy_state_row: dict | None) -> bool:
    """Return True if the strategy_state row marks the strategy as paused."""
    if strategy_state_row is None:
        return False
    return bool(strategy_state_row.get("paused", 0))


# ---------------------------------------------------------------------------
# Per-strategy breakdown helpers
# ---------------------------------------------------------------------------


def _build_strategy_breakdown_lines(
    config: DriftConfig,
    conn,
    open_trades: list[dict],
) -> list[str]:
    """Return HTML lines for the per-strategy breakdown used in /status.

    One line per enabled strategy: name, state (Running/Paused), open trades.
    """
    lines: list[str] = []
    for name, strat in config.strategies.items():
        if not strat.enabled:
            continue
        state_row = get_strategy_state(conn, name)
        is_paused = _strategy_paused_flag(state_row)
        strat_trades = [t for t in open_trades if t.get("strategy") == name]
        state_label = "⏸️ Paused" if is_paused else "▶️ Running"
        lines.append(f"  <b>{name}</b>  {state_label}  ·  {len(strat_trades)} open")
    return lines


def _build_balance_strategy_lines(
    config: DriftConfig,
    conn,
) -> list[str]:
    """Return HTML lines for the per-strategy breakdown used in /balance."""
    lines: list[str] = []
    for name, strat in config.strategies.items():
        if not strat.enabled:
            continue
        state_row = get_strategy_state(conn, name)
        strat_stats = get_stats(conn, strategy=name)
        allocation = strat.allocation_pct
        strat_pnl = strat_stats.get("total_pnl") or 0.0
        _equity, _peak, dd_pct = _strategy_equity_info(
            conn, config, name, state_row, realized=strat_pnl
        )
        if dd_pct is None:
            dd_label = "no data"
        else:
            dd_label = f"-{dd_pct:.1f}% below peak" if dd_pct > 0.05 else "At peak"
        lines.append(
            f"  <b>{name}</b>  {allocation:.0f}%  ·  P&amp;L {pnl_str(strat_pnl)}  ·  DD {dd_label}"
        )
    return lines


def _build_report_strategy_lines(
    config: DriftConfig,
    conn,
) -> list[str]:
    """Return HTML lines for the per-strategy breakdown used in /report."""
    lines: list[str] = []
    for name, strat in config.strategies.items():
        if not strat.enabled:
            continue
        strat_stats = get_stats(conn, strategy=name)
        total = strat_stats.get("total_trades", 0)
        winning = strat_stats.get("winning_trades", 0)
        losing = strat_stats.get("losing_trades", 0)
        win_rate = strat_stats.get("win_rate", 0.0) * 100
        total_pnl = strat_stats.get("total_pnl") or 0.0

        # Profit factor for strategy
        try:
            rows = conn.execute(
                "SELECT profit_loss FROM trades WHERE closed_at IS NOT NULL AND strategy = ?",
                (name,),
            ).fetchall()
            gross_profit = sum(r[0] for r in rows if (r[0] or 0.0) > 0)
            gross_loss = sum(abs(r[0]) for r in rows if (r[0] or 0.0) <= 0)
            pf = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
        except Exception:
            pf = 0.0

        magic = _effective_magic(config, name)
        lines.append(
            f"  <b>{name}</b>  magic {magic}\n"
            f"    Trades: {total} ({winning}W / {losing}L)"
            f"  Win rate: {win_rate:.1f}%\n"
            f"    P&amp;L: {pnl_str(total_pnl)}  Profit factor: {pf:.2f}"
        )
    return lines


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------


async def send_notification(bot: Bot, chat_id: str, message: str) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to send Telegram notification")


async def notify_trade_opened(bot: Bot, chat_id: str, trade_info: dict) -> None:
    pair = trade_info.get("pair", "?")
    direction = trade_info.get("direction", "?").upper()
    entry = trade_info.get("entry_price", 0.0)
    sl = trade_info.get("stop_loss", 0.0)
    tp = trade_info.get("take_profit", 0.0)
    size = trade_info.get("position_size", 0.0)
    risk_usd = trade_info.get("risk_usd", 0.0)
    risk_pct = trade_info.get("risk_pct", 0.0)
    now = format_time(datetime.now(timezone.utc))

    text = (
        f"🟢 <b>TRADE OPENED</b>  ·  {pair} {direction}\n"
        f"{_SEP}\n"
        f"Entry        {_fmt_price(entry, pair)}\n"
        f"Stop loss    {_fmt_price(sl, pair)}  ({_pips(entry, sl, pair, direction)} pips)\n"
        f"Take profit  {_fmt_price(tp, pair)}  ({_pips(entry, tp, pair, direction)} pips)\n"
        f"\n"
        f"Size         {size:.2f} lots\n"
        f"Risk         ${risk_usd:.2f}  ·  {risk_pct:.1f}%\n"
        f"\n"
        f"{now} UTC-5"
    )
    await send_notification(bot, chat_id, text)


async def notify_trade_closed(bot: Bot, chat_id: str, trade_info: dict) -> None:
    pair = trade_info.get("pair", "?")
    direction = trade_info.get("direction", "?").upper()
    entry = trade_info.get("entry_price", 0.0)
    exit_price = trade_info.get("exit_price", 0.0)
    pnl = trade_info.get("profit_loss", 0.0) or 0.0
    reason = trade_info.get("close_reason", "?")
    duration_minutes = trade_info.get("duration_minutes", 0)
    now = format_time(datetime.now(timezone.utc))

    icon = "💰" if pnl > 0 else "📉" if pnl < 0 else "⚪"

    text = (
        f"{icon} <b>TRADE CLOSED</b>  ·  {pair} {direction}  ·  {pnl_str(pnl)}\n"
        f"{_SEP}\n"
        f"Entry        {_fmt_price(entry, pair)}\n"
        f"Exit         {_fmt_price(exit_price, pair)}  "
        f"({_pips(entry, exit_price, pair, direction)} pips)\n"
        f"\n"
        f"Reason       {_reason_label(reason)}\n"
        f"Duration     {format_duration(duration_minutes)}\n"
        f"\n"
        f"{now} UTC-5"
    )
    await send_notification(bot, chat_id, text)


async def notify_error(bot: Bot, chat_id: str, error_msg: str) -> None:
    now = format_time(datetime.now(timezone.utc))
    text = f"⚠️ <b>ERROR</b>  ·  {now} UTC-5\n{_SEP}\n{error_msg}"
    await send_notification(bot, chat_id, text)


# Each status maps to (icon, title) so the message reflects what actually happened.
# Distinctions that matter to the reader (D057):
#   - Booting the process ("BOT ONLINE") is NOT the same as a trading session
#     opening ("SESSION OPEN") — different icon and title so a 21:00 session wake
#     never reads like the bot just restarted.
#   - Recovering the MT5 link ("MT5 RECONNECTED") is NOT a fresh boot.
#   - A protective drawdown halt is an alarm (🚨), visually distinct from a routine
#     manual /pause (⏸️), and it says whether one strategy or the whole account stopped.
#   - Session open/close are calm lifecycle events, never an alarming "BOT STOPPED".
_STATUS_STYLES: dict[str, tuple[str, str]] = {
    "started": ("🚀", "BOT ONLINE"),
    "stopped": ("🛑", "BOT STOPPED"),
    "paused": ("⏸️", "PAUSED"),
    "resumed": ("▶️", "RESUMED"),
    "session_started": ("🌙", "SESSION OPEN"),
    "session_closed": ("😴", "SESSION CLOSED"),
    "reconnected": ("🔌", "MT5 RECONNECTED"),
    "drawdown_strategy": ("🚨", "STRATEGY HALTED — DRAWDOWN"),
    "drawdown_global": ("🚨", "ACCOUNT HALTED — DRAWDOWN"),
}


async def notify_bot_status(bot: Bot, chat_id: str, status: str, detail: str = "") -> None:
    icon, title = _STATUS_STYLES.get(status, ("ℹ️", f"BOT {status.upper()}"))
    now = format_time(datetime.now(timezone.utc))
    text = f"{icon} <b>{title}</b>  ·  {now} UTC-5"
    if detail:
        text += f"\n{_SEP}\n{detail}"
    await send_notification(bot, chat_id, text)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _make_handlers(
    chat_id: str,
    state: BotState,
    db_path: str | Path | None,
    config: DriftConfig | None = None,
):
    """Return a dict of command name → async handler, closed over shared state."""

    def _authorized(update: Update) -> bool:
        return str(update.effective_chat.id) == chat_id

    def _valid_strategy_names() -> list[str]:
        if config is None:
            return []
        return list(config.strategies.keys())

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        mt5_ok = health_check()
        mt5_label = "✅ Connected" if mt5_ok else "❌ Disconnected"
        bot_label = "⏸️ Paused" if state.paused else "▶️ Running"
        uptime = _uptime(state.start_time)

        try:
            with get_connection(db_path) as conn:
                open_trades = get_open_trades(conn)
                open_count = len(open_trades)
                strategy_lines = (
                    _build_strategy_breakdown_lines(config, conn, open_trades)
                    if config is not None
                    else []
                )
        except Exception:
            open_count = 0
            strategy_lines = []

        text = (
            f"📊 <b>BOT STATUS</b>\n"
            f"{_SEP}\n"
            f"State        {bot_label}\n"
            f"MT5          {mt5_label}\n"
            f"Uptime       {uptime}\n"
            f"Open trades  {open_count}"
        )
        if strategy_lines:
            text += "\n\n<b>BY STRATEGY</b>\n" + "\n".join(strategy_lines)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        try:
            with get_connection(db_path) as conn:
                trades = get_open_trades(conn)
        except Exception as e:
            await update.message.reply_text(f"⚠️ DB error: {e}")
            return

        if not trades:
            await update.message.reply_text("📂 No open trades right now.")
            return

        def _trade_lines(trade_list: list[dict]) -> list[str]:
            lines = []
            for t in trade_list:
                direction = t["direction"].upper()
                pnl = t.get("profit_loss")
                pnl_label = pnl_str(pnl) if pnl is not None else "—"
                opened_dt = datetime.fromisoformat(t["opened_at"])
                pair = t["pair"]
                entry_s = _fmt_price(t["entry_price"], pair)
                sl_s = _fmt_price(t["stop_loss"], pair)
                tp_s = _fmt_price(t["take_profit"], pair)
                lines.append(
                    f"\n<b>{pair} {direction}</b>  ·  P&amp;L {pnl_label}\n"
                    f"  Entry  {entry_s}  ·  Size  {t['position_size']:.2f} lots\n"
                    f"  SL  {sl_s}  ·  TP  {tp_s}\n"
                    f"  Opened  {format_time(opened_dt)} UTC-5"
                )
            return lines

        if config is not None:
            # Group by strategy
            lines = [f"📂 <b>OPEN TRADES</b>  ·  {len(trades)} active", _SEP]
            for name, strat in config.strategies.items():
                if not strat.enabled:
                    continue
                strat_trades = [t for t in trades if t.get("strategy") == name]
                if strat_trades:
                    lines.append(f"\n<b>{name}</b>")
                    lines.extend(_trade_lines(strat_trades))
            unattributed = [t for t in trades if t.get("strategy") not in config.strategies]
            if unattributed:
                lines.append("\n<b>other</b>")
                lines.extend(_trade_lines(unattributed))
        else:
            lines = [f"📂 <b>OPEN TRADES</b>  ·  {len(trades)} active", _SEP]
            lines.extend(_trade_lines(trades))

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        try:
            with get_connection(db_path) as conn:
                trades = get_recent_trades(conn, limit=5)
        except Exception as e:
            await update.message.reply_text(f"⚠️ DB error: {e}")
            return

        if not trades:
            await update.message.reply_text("📜 No closed trades yet.")
            return

        lines = ["📜 <b>RECENT TRADES</b>  ·  Last 5", _SEP]
        for t in trades:
            direction = t["direction"].upper()
            pair = t["pair"]
            pnl = t.get("profit_loss", 0.0) or 0.0
            reason = _reason_label(t.get("close_reason", "?"))
            closed_dt = datetime.fromisoformat(t["closed_at"])
            pip_diff = _pips(t["entry_price"], t["exit_price"], pair, t["direction"])
            lines.append(
                f"\n<b>{pair} {direction}</b>  ·  {pnl_str(pnl)}\n"
                f"  {t['entry_price']:.5f} → {t['exit_price']:.5f}  ({pip_diff} pips)\n"
                f"  {reason}  ·  {format_duration(t['duration_minutes'] or 0)}\n"
                f"  Closed  {format_time(closed_dt)} UTC-5"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        try:
            balance = get_balance()
        except Exception:
            balance = None

        try:
            with get_connection(db_path) as conn:
                stats = get_stats(conn)
                strategy_lines = (
                    _build_balance_strategy_lines(config, conn) if config is not None else []
                )
        except Exception:
            stats = {}
            strategy_lines = []

        peak = stats.get("peak_balance") or 0.0
        total_pnl = stats.get("total_pnl") or 0.0
        drawdown_pct = ((peak - balance) / peak * 100) if peak and balance is not None else 0.0

        balance_str = f"${balance:.2f}" if balance is not None else "N/A"
        dd_label = f"-{drawdown_pct:.1f}% below peak" if drawdown_pct > 0.05 else "At peak"
        text = (
            f"💰 <b>ACCOUNT</b>\n"
            f"{_SEP}\n"
            f"Balance      {balance_str}\n"
            f"Peak         ${peak:.2f}  ({dd_label})\n"
            f"Total P&amp;L    {pnl_str(total_pnl)}"
        )
        if strategy_lines:
            text += "\n\n<b>BY STRATEGY</b>\n" + "\n".join(strategy_lines)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        args = context.args or []
        if args:
            strategy_name = args[0]
            valid_names = _valid_strategy_names()
            if strategy_name not in valid_names:
                names_str = ", ".join(valid_names) if valid_names else "(none configured)"
                await update.message.reply_text(
                    f"⚠️ Unknown strategy <b>{strategy_name}</b>.\nValid names: {names_str}",
                    parse_mode="HTML",
                )
                return
            try:
                with get_connection(db_path) as conn:
                    current = get_strategy_state(conn, strategy_name)
                    if _strategy_paused_flag(current):
                        await update.message.reply_text(
                            f"⏸️ Strategy <b>{strategy_name}</b> is already paused.",
                            parse_mode="HTML",
                        )
                        return
                    set_strategy_paused(conn, strategy_name, True)
            except Exception as e:
                await update.message.reply_text(f"⚠️ DB error: {e}")
                return
            logger.info("Strategy %s paused via Telegram command", strategy_name)
            await update.message.reply_text(
                f"⏸️ <b>{strategy_name}</b> paused.  "
                f"No new trades for this strategy. Open positions stay open.",
                parse_mode="HTML",
            )
        else:
            if state.paused:
                await update.message.reply_text("⏸️ Already paused.")
                return
            state.paused = True
            logger.info("Bot paused via Telegram command")
            await update.message.reply_text(
                "⏸️ <b>Paused.</b>  No new trades will be opened. Open positions stay open.",
                parse_mode="HTML",
            )

    async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        args = context.args or []
        if args:
            strategy_name = args[0]
            valid_names = _valid_strategy_names()
            if strategy_name not in valid_names:
                names_str = ", ".join(valid_names) if valid_names else "(none configured)"
                await update.message.reply_text(
                    f"⚠️ Unknown strategy <b>{strategy_name}</b>.\nValid names: {names_str}",
                    parse_mode="HTML",
                )
                return
            try:
                with get_connection(db_path) as conn:
                    current = get_strategy_state(conn, strategy_name)
                    if not _strategy_paused_flag(current):
                        await update.message.reply_text(
                            f"▶️ Strategy <b>{strategy_name}</b> is already running.",
                            parse_mode="HTML",
                        )
                        return

                    # Compute current equity without bumping the stored peak
                    # (evaluate_strategy_drawdown would raise it via MAX — D063).
                    equity, _peak, _dd = _strategy_equity_info(conn, config, strategy_name, current)
                    if equity is not None:
                        reset_strategy_peak(conn, strategy_name, equity)

                    set_strategy_paused(conn, strategy_name, False)
            except Exception as e:
                await update.message.reply_text(f"⚠️ DB error: {e}")
                return
            logger.info("Strategy %s resumed via Telegram command", strategy_name)
            await update.message.reply_text(
                f"▶️ <b>{strategy_name}</b> resumed.  "
                f"Drawdown window reset — watching pairs for new signals.",
                parse_mode="HTML",
            )
        else:
            if not state.paused:
                await update.message.reply_text("▶️ Already running.")
                return
            state.paused = False
            logger.info("Bot resumed via Telegram command")
            await update.message.reply_text(
                "▶️ <b>Resumed.</b>  Watching pairs for new signals.", parse_mode="HTML"
            )

    async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        state.stop_requested = True
        logger.info("Stop requested via Telegram command")
        await update.message.reply_text(
            "🛑 <b>Stop requested.</b>  Closing all trades and shutting down…",
            parse_mode="HTML",
        )

    async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        gross_profit = 0.0
        gross_loss = 0.0
        try:
            with get_connection(db_path) as conn:
                stats = get_stats(conn)
                open_trades = get_open_trades(conn)
                strategy_lines = (
                    _build_report_strategy_lines(config, conn) if config is not None else []
                )
                for row in conn.execute(
                    "SELECT profit_loss FROM trades WHERE closed_at IS NOT NULL"
                ):
                    pnl = row[0] or 0.0
                    if pnl > 0:
                        gross_profit += pnl
                    else:
                        gross_loss += abs(pnl)
        except Exception as e:
            await update.message.reply_text(f"⚠️ DB error: {e}")
            return

        total = stats.get("total_trades", 0)
        winning = stats.get("winning_trades", 0)
        losing = stats.get("losing_trades", 0)
        win_rate = stats.get("win_rate", 0.0) * 100
        total_pnl = stats.get("total_pnl", 0.0) or 0.0
        avg_dur = int(stats.get("avg_trade_duration_minutes", 0) or 0)

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

        text = (
            f"📊 <b>PERFORMANCE REPORT</b>\n"
            f"{_SEP}\n"
            f"Open          {len(open_trades)} trades\n"
            f"Closed        {total} trades\n"
            f"\n"
            f"Winners       {winning}  ·  {win_rate:.1f}%\n"
            f"Losers        {losing}\n"
            f"Profit factor {profit_factor:.2f}\n"
            f"\n"
            f"Total P&amp;L     {pnl_str(total_pnl)}\n"
            f"Avg duration  {format_duration(avg_dur)}"
        )
        if strategy_lines:
            text += "\n\n<b>BY STRATEGY</b>\n" + "\n".join(strategy_lines)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_strategies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        if config is None:
            await update.message.reply_text("⚠️ No strategy config available.")
            return

        try:
            with get_connection(db_path) as conn:
                open_trades = get_open_trades(conn)
                lines = [f"🗂️ <b>STRATEGIES</b>  ·  {len(config.strategies)} configured", _SEP]
                for name, strat in config.strategies.items():
                    state_row = get_strategy_state(conn, name)
                    is_paused = _strategy_paused_flag(state_row)
                    enabled_label = "enabled" if strat.enabled else "disabled"
                    paused_label = "⏸️ paused" if is_paused else "▶️ running"
                    magic = _effective_magic(config, name)
                    strat_trades = [t for t in open_trades if t.get("strategy") == name]
                    equity, peak_eq, dd_pct = _strategy_equity_info(conn, config, name, state_row)
                    if dd_pct is None:
                        dd_label = "no data"
                    elif dd_pct > 0.05:
                        dd_label = f"-{dd_pct:.1f}% DD  equity ${equity:.2f}"
                    else:
                        dd_label = f"At peak  equity ${equity:.2f}"
                    lines.append(
                        f"\n<b>{name}</b>  [{enabled_label}]  {paused_label}\n"
                        f"  Allocation  {strat.allocation_pct:.0f}%"
                        f"  ·  Magic  {magic}\n"
                        f"  Open trades  {len(strat_trades)}"
                        f"  ·  {dd_label}"
                    )
        except Exception as e:
            await update.message.reply_text(f"⚠️ DB error: {e}")
            return

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    return {
        "status": cmd_status,
        "trades": cmd_trades,
        "history": cmd_history,
        "balance": cmd_balance,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "stop": cmd_stop,
        "report": cmd_report,
        "strategies": cmd_strategies,
    }


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


_COMMAND_MENU: list[tuple[str, str]] = [
    ("status", "Bot state, MT5 connection, uptime, open trades"),
    ("trades", "List currently open trades"),
    ("history", "Show the last 5 closed trades"),
    ("balance", "Account balance, peak and total P&L"),
    ("report", "Full performance report"),
    ("strategies", "List all strategies with status and drawdown"),
    ("pause", "Stop opening new trades (positions stay open)"),
    ("resume", "Resume taking new signals"),
    ("stop", "Close all trades and shut the bot down"),
]


async def _register_command_menu(bot: Bot) -> None:
    """Publish the command menu so it appears in Telegram's / autocomplete."""
    commands = [BotCommand(name, description) for name, description in _COMMAND_MENU]
    try:
        await bot.set_my_commands(commands)
        logger.info("Registered %d Telegram commands in the menu", len(commands))
    except Exception:
        logger.exception("Failed to register Telegram command menu")


async def _on_telegram_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log transient Telegram network errors as a single-line warning."""
    logger.warning("Telegram update error: %s", context.error)


async def setup_bot(
    token: str,
    chat_id: str,
    state: BotState,
    db_path: str | Path | None = None,
    config: DriftConfig | None = None,
) -> Application:
    """Build the Application, register all command handlers, and return it."""
    app = Application.builder().token(token).build()

    handlers = _make_handlers(chat_id, state, db_path, config)
    for command, handler in handlers.items():
        app.add_handler(CommandHandler(command, handler))

    app.add_error_handler(_on_telegram_error)

    await _register_command_menu(app.bot)

    logger.info("Telegram bot configured with %d command handlers", len(handlers))
    return app
