"""Shared formatting helpers used by telegram_bot and report modules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC_MINUS_5 = timezone(timedelta(hours=-5))


def format_duration(minutes: int) -> str:
    """Convert a duration in minutes to a human-readable string like '2d 3h 15m'."""
    days = minutes // 1440
    hours = (minutes % 1440) // 60
    mins = minutes % 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts)


def pnl_str(value: float) -> str:
    """Format a P&L value as '+$12.34' or '-$12.34'."""
    return f"+${value:.2f}" if value >= 0 else f"-${abs(value):.2f}"


def format_time(dt: datetime) -> str:
    """Convert a UTC datetime to UTC-5 and format as 'YYYY-MM-DD HH:MM'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(UTC_MINUS_5)
    return local.strftime("%Y-%m-%d %H:%M")
