"""Shared formatting helpers used by telegram_bot and report modules."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

UTC_MINUS_5 = timezone(timedelta(hours=-5))

# Module-level display timezone used by format_time() and report.py when no
# explicit tz is passed.  Set once at startup from config.reports.timezone via
# set_display_tz() so all user-facing times share a single configurable zone.
# Default stays at UTC-5 (Bogotá) to preserve behaviour when unset.
_DISPLAY_TZ = UTC_MINUS_5

_OFFSET_RE = re.compile(r"^UTC([+-]\d{1,2})?$", re.IGNORECASE)


def parse_utc_offset(tz_str: str) -> timezone:
    """Parse a 'UTC', 'UTC-5' or 'UTC+3' string into a timezone.

    Whitespace is ignored.  Raises ValueError on an unrecognised format.
    """
    cleaned = (tz_str or "").replace(" ", "")
    match = _OFFSET_RE.match(cleaned)
    if match is None:
        raise ValueError(f"Invalid UTC offset string: {tz_str!r}")
    hours = int(match.group(1)) if match.group(1) else 0
    return timezone(timedelta(hours=hours))


def set_display_tz(tz: timezone) -> None:
    """Set the module-level display timezone used by format_time and report.py."""
    global _DISPLAY_TZ
    _DISPLAY_TZ = tz


def get_display_tz() -> timezone:
    """Return the currently configured display timezone."""
    return _DISPLAY_TZ


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


def format_time(dt: datetime, tz: timezone | None = None) -> str:
    """Format a real-UTC datetime in the display timezone as 'YYYY-MM-DD HH:MM'.

    The input is always assumed to be real UTC (server→UTC conversion happens at
    persistence time).  Pass tz to override the module display timezone; when
    omitted, the timezone set via set_display_tz() is used (default UTC-5).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(tz or _DISPLAY_TZ)
    return local.strftime("%Y-%m-%d %H:%M")
