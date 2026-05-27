from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from drift.db import get_connection, get_open_trades, get_recent_trades, get_stats
from drift.formatting import format_duration, format_time, pnl_str
from drift.mt5_client import get_balance, health_check

logger = logging.getLogger(__name__)


class BotState:
    def __init__(self) -> None:
        self.paused: bool = False
        self.stop_requested: bool = False
        self.start_time: datetime = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uptime(start_time: datetime) -> str:
    delta = datetime.now(timezone.utc) - start_time
    return format_duration(int(delta.total_seconds() // 60))


def _pips(entry: float, level: float) -> str:
    diff = round((level - entry) * 10000)
    return f"+{diff}" if diff >= 0 else str(diff)


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

    text = (
        "🟢 <b>TRADE OPENED</b>\n"
        f"Pair: {pair}\n"
        f"Direction: {direction}\n"
        f"Entry: {entry:.5f}\n"
        f"SL: {sl:.5f} ({_pips(entry, sl)} pips)\n"
        f"TP: {tp:.5f} ({_pips(entry, tp)} pips)\n"
        f"Size: {size:.2f} lots\n"
        f"Risk: ${risk_usd:.2f} ({risk_pct:.1f}%)"
    )
    await send_notification(bot, chat_id, text)


async def notify_trade_closed(bot: Bot, chat_id: str, trade_info: dict) -> None:
    pair = trade_info.get("pair", "?")
    direction = trade_info.get("direction", "?").upper()
    entry = trade_info.get("entry_price", 0.0)
    exit_price = trade_info.get("exit_price", 0.0)
    pnl = trade_info.get("profit_loss", 0.0)
    reason = trade_info.get("close_reason", "?")
    duration_minutes = trade_info.get("duration_minutes", 0)

    text = (
        "🔴 <b>TRADE CLOSED</b>\n"
        f"Pair: {pair}\n"
        f"Direction: {direction}\n"
        f"Entry: {entry:.5f}\n"
        f"Exit: {exit_price:.5f}\n"
        f"P&amp;L: {pnl_str(pnl)}\n"
        f"Reason: {reason}\n"
        f"Duration: {format_duration(duration_minutes)}"
    )
    await send_notification(bot, chat_id, text)


async def notify_error(bot: Bot, chat_id: str, error_msg: str) -> None:
    now = format_time(datetime.now(timezone.utc))
    text = f"⚠️ <b>ERROR</b>\n{error_msg}\nTime: {now}"
    await send_notification(bot, chat_id, text)


async def notify_bot_status(bot: Bot, chat_id: str, status: str, detail: str = "") -> None:
    icons = {
        "started": "🚀",
        "stopped": "🛑",
        "paused": "⏸️",
        "resumed": "▶️",
    }
    icon = icons.get(status, "ℹ️")
    now = format_time(datetime.now(timezone.utc))
    text = f"{icon} <b>BOT {status.upper()}</b>"
    if detail:
        text += f"\n{detail}"
    text += f"\nTime: {now}"
    await send_notification(bot, chat_id, text)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _make_handlers(chat_id: str, state: BotState, db_path: str | Path | None):
    """Return a dict of command name → async handler, closed over shared state."""

    def _authorized(update: Update) -> bool:
        return str(update.effective_chat.id) == chat_id

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
        except Exception:
            open_count = 0

        text = (
            "📊 <b>BOT STATUS</b>\n"
            f"Status: {bot_label}\n"
            f"MT5: {mt5_label}\n"
            f"Uptime: {uptime}\n"
            f"Open trades: {open_count}"
        )
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
            await update.message.reply_text("No open trades.")
            return

        lines = ["📂 <b>OPEN TRADES</b>"]
        for t in trades:
            direction = t["direction"].upper()
            pnl = t.get("profit_loss")
            pnl_label = pnl_str(pnl) if pnl is not None else "—"
            opened_dt = datetime.fromisoformat(t["opened_at"])
            lines.append(
                f"\n{t['pair']} {direction}\n"
                f"  Entry: {t['entry_price']:.5f}\n"
                f"  SL: {t['stop_loss']:.5f}  TP: {t['take_profit']:.5f}\n"
                f"  Size: {t['position_size']:.2f} lots  P&amp;L: {pnl_label}\n"
                f"  Opened: {format_time(opened_dt)}"
            )
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
            await update.message.reply_text("No closed trades yet.")
            return

        lines = ["📜 <b>LAST 5 TRADES</b>"]
        for t in trades:
            direction = t["direction"].upper()
            pnl = t.get("profit_loss", 0.0) or 0.0
            reason = t.get("close_reason", "?")
            closed_dt = datetime.fromisoformat(t["closed_at"])
            lines.append(
                f"\n{t['pair']} {direction}  {pnl_str(pnl)}\n"
                f"  Entry: {t['entry_price']:.5f} → Exit: {t['exit_price']:.5f}\n"
                f"  Reason: {reason}  Duration: {format_duration(t['duration_minutes'] or 0)}\n"
                f"  Closed: {format_time(closed_dt)}"
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
        except Exception:
            stats = {}

        peak = stats.get("peak_balance") or 0.0
        total_pnl = stats.get("total_pnl") or 0.0
        drawdown_pct = ((peak - balance) / peak * 100) if peak and balance is not None else 0.0

        balance_str = f"${balance:.2f}" if balance is not None else "N/A"
        text = (
            "💰 <b>BALANCE</b>\n"
            f"Balance: {balance_str}\n"
            f"Peak: ${peak:.2f}\n"
            f"Drawdown: {drawdown_pct:.1f}%\n"
            f"Total P&amp;L: {pnl_str(total_pnl)}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        if state.paused:
            await update.message.reply_text("⏸️ Bot is already paused.")
            return

        state.paused = True
        logger.info("Bot paused via Telegram command")
        await update.message.reply_text(
            "⏸️ <b>Bot paused.</b> No new trades will be opened.", parse_mode="HTML"
        )

    async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        if not state.paused:
            await update.message.reply_text("▶️ Bot is already running.")
            return

        state.paused = False
        logger.info("Bot resumed via Telegram command")
        await update.message.reply_text("▶️ <b>Bot resumed.</b>", parse_mode="HTML")

    async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        state.stop_requested = True
        logger.info("Stop requested via Telegram command")
        await update.message.reply_text(
            "🛑 <b>Stop requested.</b> Closing all trades and shutting down...",
            parse_mode="HTML",
        )

    async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            return

        try:
            with get_connection(db_path) as conn:
                stats = get_stats(conn)
                open_trades = get_open_trades(conn)
        except Exception as e:
            await update.message.reply_text(f"⚠️ DB error: {e}")
            return

        total = stats.get("total_trades", 0)
        winning = stats.get("winning_trades", 0)
        losing = stats.get("losing_trades", 0)
        win_rate = stats.get("win_rate", 0.0) * 100
        total_pnl = stats.get("total_pnl", 0.0) or 0.0
        avg_dur = int(stats.get("avg_trade_duration_minutes", 0) or 0)

        gross_profit = 0.0
        gross_loss = 0.0
        try:
            with get_connection(db_path) as conn:
                rows = conn.execute(
                    "SELECT profit_loss FROM trades WHERE closed_at IS NOT NULL"
                ).fetchall()
                for row in rows:
                    pnl = row[0] or 0.0
                    if pnl > 0:
                        gross_profit += pnl
                    else:
                        gross_loss += abs(pnl)
        except Exception:
            pass

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

        text = (
            "📊 <b>PERFORMANCE REPORT</b>\n\n"
            f"Open trades: {len(open_trades)}\n"
            f"Closed trades: {total}\n"
            f"Winners: {winning}  Losers: {losing}\n"
            f"Win rate: {win_rate:.1f}%\n"
            f"Profit factor: {profit_factor:.2f}\n"
            f"Total P&amp;L: {pnl_str(total_pnl)}\n"
            f"Avg duration: {format_duration(avg_dur)}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    return {
        "status": cmd_status,
        "trades": cmd_trades,
        "history": cmd_history,
        "balance": cmd_balance,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "stop": cmd_stop,
        "report": cmd_report,
    }


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def setup_bot(
    token: str,
    chat_id: str,
    state: BotState,
    db_path: str | Path | None = None,
) -> Application:
    """Build the Application, register all command handlers, and return it."""
    app = Application.builder().token(token).build()

    handlers = _make_handlers(chat_id, state, db_path)
    for command, handler in handlers.items():
        app.add_handler(CommandHandler(command, handler))

    logger.info("Telegram bot configured with %d command handlers", len(handlers))
    return app
