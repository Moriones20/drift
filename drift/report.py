"""Weekly performance report generator.

Generates an HTML-formatted summary of the last 7 days of trading activity
for delivery via Telegram every Monday 01:00 UTC (Sunday 8pm UTC-5).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from drift.formatting import UTC_MINUS_5, format_duration, pnl_str

logger = logging.getLogger(__name__)


def _period_label(since: datetime) -> str:
    """Return 'May 19 - May 25, 2026' style label in UTC-5."""
    now_local = datetime.now(UTC_MINUS_5)
    since_local = since.astimezone(UTC_MINUS_5)
    end_local = now_local - timedelta(days=1)

    if since_local.year == now_local.year:
        start_str = since_local.strftime("%b %d")
        end_str = end_local.strftime("%b %d, %Y")
    else:
        start_str = since_local.strftime("%b %d, %Y")
        end_str = end_local.strftime("%b %d, %Y")
    return f"{start_str} - {end_str}"


def generate_weekly_report(
    conn: sqlite3.Connection,
    current_balance: float,
    peak_balance: float,
) -> str:
    """Generate an HTML weekly report string from trades closed in the last 7 days.

    Args:
        conn: Active SQLite connection with row_factory set.
        current_balance: Current account balance from MT5.
        peak_balance: Highest balance recorded since bot start.

    Returns:
        HTML-formatted report string ready for Telegram.
    """
    since = datetime.now(timezone.utc) - timedelta(days=7)
    since_iso = since.isoformat()

    trades = conn.execute(
        "SELECT * FROM trades WHERE closed_at IS NOT NULL AND closed_at >= ?"
        " ORDER BY closed_at ASC",
        (since_iso,),
    ).fetchall()

    open_count = conn.execute("SELECT COUNT(*) FROM trades WHERE closed_at IS NULL").fetchone()[0]

    period_label = _period_label(since)

    if not trades:
        drawdown_pct = (
            (peak_balance - current_balance) / peak_balance * 100 if peak_balance > 0 else 0.0
        )
        return (
            "📊 <b>WEEKLY REPORT</b>\n"
            f"Period: {period_label}\n\n"
            "<b>No trades this week.</b>\n\n"
            "<b>ACCOUNT</b>\n"
            f"Balance: ${current_balance:.2f}\n"
            f"Peak: ${peak_balance:.2f}\n"
            f"Drawdown: {drawdown_pct:.1f}%\n"
            f"Open Trades: {open_count}"
        )

    total = len(trades)
    wins = sum(1 for t in trades if (t["profit_loss"] or 0.0) > 0)
    losses = total - wins
    win_rate = (wins / total * 100) if total > 0 else 0.0

    gross_profit = sum((t["profit_loss"] or 0.0) for t in trades if (t["profit_loss"] or 0.0) > 0)
    gross_loss = sum(abs(t["profit_loss"] or 0.0) for t in trades if (t["profit_loss"] or 0.0) <= 0)
    net_pnl = gross_profit - gross_loss
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

    best = max(trades, key=lambda t: t["profit_loss"] or 0.0)
    worst = min(trades, key=lambda t: t["profit_loss"] or 0.0)

    avg_duration_mins = int(sum(t["duration_minutes"] or 0 for t in trades) / total)

    pairs_rows = conn.execute(
        """
        SELECT pair, SUM(profit_loss) AS pair_pnl, COUNT(*) AS pair_count
        FROM trades
        WHERE closed_at IS NOT NULL AND closed_at >= ?
        GROUP BY pair
        ORDER BY pair_pnl DESC
        """,
        (since_iso,),
    ).fetchall()

    drawdown_pct = (
        (peak_balance - current_balance) / peak_balance * 100 if peak_balance > 0 else 0.0
    )

    best_pnl = best["profit_loss"] or 0.0
    worst_pnl = worst["profit_loss"] or 0.0
    best_line = f"{best['pair']} {best['direction'].upper()} {pnl_str(best_pnl)}"
    worst_line = f"{worst['pair']} {worst['direction'].upper()} {pnl_str(worst_pnl)}"

    pairs_lines = "\n".join(
        f"{row['pair']}: {pnl_str(row['pair_pnl'] or 0.0)} ({row['pair_count']} trades)"
        for row in pairs_rows
    )

    report = (
        "📊 <b>WEEKLY REPORT</b>\n"
        f"Period: {period_label}\n\n"
        "<b>SUMMARY</b>\n"
        f"Trades: {total} ({wins}W / {losses}L)\n"
        f"Win Rate: {win_rate:.1f}%\n"
        f"Profit Factor: {profit_factor:.2f}\n\n"
        "<b>P&amp;L</b>\n"
        f"Gross Profit: {pnl_str(gross_profit)}\n"
        f"Gross Loss: -${gross_loss:.2f}\n"
        f"Net P&amp;L: {pnl_str(net_pnl)}\n\n"
        "<b>TRADES</b>\n"
        f"Best: {best_line}\n"
        f"Worst: {worst_line}\n"
        f"Avg Duration: {format_duration(avg_duration_mins)}\n\n"
        "<b>PAIRS</b>\n"
        f"{pairs_lines}\n\n"
        "<b>ACCOUNT</b>\n"
        f"Balance: ${current_balance:.2f}\n"
        f"Peak: ${peak_balance:.2f}\n"
        f"Drawdown: {drawdown_pct:.1f}%\n"
        f"Open Trades: {open_count}"
    )

    logger.info(
        "Weekly report generated: %d trades, net P&L=%.2f, win_rate=%.1f%%",
        total,
        net_pnl,
        win_rate,
    )
    return report
