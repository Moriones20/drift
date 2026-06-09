"""Weekly performance report generator.

Generates an HTML-formatted summary of the last 7 days of trading activity
for delivery via Telegram every Monday 01:00 UTC (Sunday 8pm UTC-5).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from drift.config import DriftConfig
from drift.db import get_stats
from drift.formatting import format_duration, get_display_tz, pnl_str

logger = logging.getLogger(__name__)


def _period_label(since: datetime) -> str:
    """Return 'May 19 - May 25, 2026' style label in the display timezone."""
    display_tz = get_display_tz()
    now_local = datetime.now(display_tz)
    since_local = since.astimezone(display_tz)
    end_local = now_local - timedelta(days=1)

    if since_local.year == now_local.year:
        start_str = since_local.strftime("%b %d")
        end_str = end_local.strftime("%b %d, %Y")
    else:
        start_str = since_local.strftime("%b %d, %Y")
        end_str = end_local.strftime("%b %d, %Y")
    return f"{start_str} - {end_str}"


def _strategy_section(conn: sqlite3.Connection, since_iso: str, config: DriftConfig) -> str:
    """Return an HTML section with per-strategy stats for the weekly report."""
    lines: list[str] = []
    for name, strat in config.strategies.items():
        if not strat.enabled:
            continue
        strat_stats = get_stats(conn, strategy=name)
        total = strat_stats.get("total_trades", 0)
        winning = strat_stats.get("winning_trades", 0)
        losing = total - winning
        win_rate = strat_stats.get("win_rate", 0.0) * 100
        total_pnl = strat_stats.get("total_pnl") or 0.0

        # Weekly P&L for this strategy (last 7 days only)
        rows = conn.execute(
            "SELECT profit_loss FROM trades"
            " WHERE closed_at IS NOT NULL AND closed_at >= ? AND strategy = ?",
            (since_iso, name),
        ).fetchall()
        week_total = len(rows)
        week_wins = sum(1 for r in rows if (r[0] or 0.0) > 0)
        week_pnl = sum(r[0] or 0.0 for r in rows)
        week_win_rate = (week_wins / week_total * 100) if week_total > 0 else 0.0

        lines.append(
            f"  <b>{name}</b>\n"
            f"    Week: {week_total} trades  Win rate: {week_win_rate:.1f}%"
            f"  Net P&amp;L: {pnl_str(week_pnl)}\n"
            f"    All-time: {total} ({winning}W / {losing}L)"
            f"  Win rate: {win_rate:.1f}%  Total P&amp;L: {pnl_str(total_pnl)}"
        )

    if not lines:
        return ""
    return "<b>BY STRATEGY</b>\n" + "\n".join(lines)


def generate_weekly_report(
    conn: sqlite3.Connection,
    current_balance: float,
    peak_balance: float,
    config: DriftConfig | None = None,
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
        report_no_trades = (
            "📊 <b>WEEKLY REPORT</b>\n"
            f"Period: {period_label}\n\n"
            "<b>No trades this week.</b>\n\n"
            "<b>ACCOUNT</b>\n"
            f"Balance: ${current_balance:.2f}\n"
            f"Peak: ${peak_balance:.2f}\n"
            f"Drawdown: {drawdown_pct:.1f}%\n"
            f"Open Trades: {open_count}"
        )
        if config is not None:
            strategy_sec = _strategy_section(conn, since_iso, config)
            if strategy_sec:
                report_no_trades += f"\n\n{strategy_sec}"
        return report_no_trades

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

    if config is not None:
        strategy_sec = _strategy_section(conn, since_iso, config)
        if strategy_sec:
            report += f"\n\n{strategy_sec}"

    logger.info(
        "Weekly report generated: %d trades, net P&L=%.2f, win_rate=%.1f%%",
        total,
        net_pnl,
        win_rate,
    )
    return report
