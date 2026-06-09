"""Tests for ``DailyLullStrategy.next_wake`` (Step 32a / D058, C10 cleanup).

For a broad battery of ``now`` timestamps in MT5 server time,
``DailyLullStrategy.next_wake(now)`` must return EXACTLY the boundary produced
by the scheduling helpers in ``drift.strategies.daily_lull``:

    inside the session window  -> ``_next_m15_close(now)``
    outside the session window -> ``_next_session_start(now, start_hour)``

The decision of which branch applies is driven by
``_in_session_window(now, start_hour, end_hour)``.

No MT5 calls are made here; only pure datetime arithmetic.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure project root is on the path when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from drift.strategies.daily_lull import (  # noqa: E402
    DailyLullParams,
    DailyLullStrategy,
    _in_session_window,
    _next_m15_close,
    _next_session_start,
)

# Session hours used on both sides of the equivalence (the live defaults).
_START_HOUR = 21
_END_HOUR = 2

# MT5 server time is labeled tz-aware but reasoned over as the server clock.
_SERVER_TZ = timezone.utc


def _strategy() -> DailyLullStrategy:
    params = DailyLullParams(
        session_start_hour=_START_HOUR,
        session_end_hour=_END_HOUR,
    )
    return DailyLullStrategy(pairs=["EURCHF"], params=params)


def _legacy_wake(now: datetime) -> datetime:
    """Reproduce what the main loop would wake on next, for *now*.

    Uses the helpers from drift.strategies.daily_lull (ported verbatim from
    the old main.py helpers — same logic, just parameterised differently).
    """
    if _in_session_window(now, _START_HOUR, _END_HOUR):
        return _next_m15_close(now)
    return _next_session_start(now, _START_HOUR)


# A 2026 calendar anchor whose weekdays are known:
#   2026-06-01 is a Monday.
_MON = datetime(2026, 6, 1, tzinfo=_SERVER_TZ)  # Monday
_TUE = datetime(2026, 6, 2, tzinfo=_SERVER_TZ)  # Tuesday
_THU = datetime(2026, 6, 4, tzinfo=_SERVER_TZ)  # Thursday
_FRI = datetime(2026, 6, 5, tzinfo=_SERVER_TZ)  # Friday
_SAT = datetime(2026, 6, 6, tzinfo=_SERVER_TZ)  # Saturday
_SUN = datetime(2026, 6, 7, tzinfo=_SERVER_TZ)  # Sunday


def _at(day: datetime, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return day.replace(hour=hour, minute=minute, second=second, microsecond=0)


# A broad battery of server-time instants covering the cases called out in the
# plan: inside the window at various hours/minutes, outside on a weekday, Friday,
# Saturday, Sunday 20:00 and 21:00, the tail at 00:30 and 01:45, the 02:00 edge,
# and Thursday 23:50.
_CASES: list[datetime] = [
    # --- Inside the window, varied hours/minutes (range-def + trading) ---
    _at(_MON, 21, 0),
    _at(_MON, 21, 7),
    _at(_MON, 21, 15),
    _at(_MON, 21, 44),
    _at(_MON, 22, 30),
    _at(_MON, 22, 59, 59),
    _at(_MON, 23, 0),
    _at(_MON, 23, 12),
    _at(_MON, 23, 45),
    _at(_MON, 23, 59, 30),
    # --- Tail of the session (carry-over to next calendar day) ---
    _at(_TUE, 0, 0),
    _at(_TUE, 0, 30),
    _at(_TUE, 1, 0),
    _at(_TUE, 1, 45),
    _at(_TUE, 1, 59, 59),
    # --- The 02:00 edge (session_end_hour — outside the window) ---
    _at(_TUE, 2, 0),
    _at(_TUE, 2, 1),
    # --- Outside the window during the day on a weekday ---
    _at(_TUE, 3, 0),
    _at(_TUE, 9, 17),
    _at(_TUE, 20, 0),
    _at(_TUE, 20, 59),
    # --- Thursday just before a close, inside window ---
    _at(_THU, 23, 50),
    # --- Friday: a Friday session start is skipped ---
    _at(_FRI, 20, 0),
    _at(_FRI, 21, 0),
    _at(_FRI, 22, 0),
    _at(_FRI, 23, 30),
    # --- Friday tail 00:00-01:59 belongs to Thursday's session (allowed) ---
    _at(_FRI, 0, 30),
    _at(_FRI, 1, 45),
    # --- Saturday: market closed ---
    _at(_SAT, 0, 30),
    _at(_SAT, 10, 0),
    _at(_SAT, 21, 0),
    _at(_SAT, 23, 0),
    # --- Sunday 20:00 (outside) and 21:00 (valid new-week session start) ---
    _at(_SUN, 20, 0),
    _at(_SUN, 21, 0),
    _at(_SUN, 21, 30),
    _at(_SUN, 23, 45),
    # --- Sunday tail into Monday handled above; add Sunday daytime ---
    _at(_SUN, 5, 0),
]


@pytest.mark.parametrize("now", _CASES, ids=[c.isoformat() for c in _CASES])
def test_next_wake_matches_main_loop(now: datetime) -> None:
    strategy = _strategy()
    assert strategy.next_wake(now) == _legacy_wake(now)


def test_next_wake_uses_strategy_params_not_globals() -> None:
    """A different session window shifts the boundary accordingly.

    The strategy must schedule off its own params; with start_hour=18/end_hour=1
    a 17:00 instant is outside the window and the next wake is 18:00 the same
    day, while 19:30 is inside and the next wake is the next M15 close.
    """
    params = DailyLullParams(session_start_hour=18, session_end_hour=1)
    strategy = DailyLullStrategy(pairs=["EURCHF"], params=params)

    outside = _at(_MON, 17, 0)
    assert strategy.next_wake(outside) == _at(_MON, 18, 0)

    inside = _at(_MON, 19, 30)
    assert strategy.next_wake(inside) == _at(_MON, 19, 45)


def test_next_wake_is_server_time_aware_and_not_relocalized() -> None:
    """The returned boundary keeps the same tzinfo as *now* (no conversion)."""
    now = _at(_MON, 22, 10)
    wake = _strategy().next_wake(now)
    assert wake is not None
    assert wake.tzinfo == now.tzinfo
    # Boundary is strictly after now and on a 15-minute grid.
    assert wake > now
    assert wake.minute % 15 == 0
    assert wake.second == 0


def test_friday_session_start_rolls_to_sunday() -> None:
    """From Friday evening the next session start is Sunday 21:00 (skips Fri/Sat)."""
    now = _at(_FRI, 22, 0)
    wake = _strategy().next_wake(now)
    assert wake == _at(_SUN, 21, 0)
    # And it equals what the legacy loop would compute.
    assert wake == _legacy_wake(now)


def test_minute_boundary_rolls_hour() -> None:
    """A 23:50 instant inside the window wakes at 00:00 next day (hour rollover)."""
    now = _at(_THU, 23, 50)
    expected = _at(_THU, 23, 50) + timedelta(minutes=10)  # 00:00 next day
    assert _strategy().next_wake(now) == expected.replace(second=0, microsecond=0)
    assert _strategy().next_wake(now) == _legacy_wake(now)
