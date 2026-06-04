"""Tests for closed_bars: evaluate only completed bars, drop the forming one (D045)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pandas as pd

from drift.strategy import closed_bars


def _df(times: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(t, tz="UTC") for t in times], name="time")
    return pd.DataFrame({"close": range(len(times))}, index=idx)


class TestClosedBars(unittest.TestCase):
    def test_drops_forming_bar_at_boundary(self):
        # At the 00:00 boundary, the 00:00 bar is still forming -> dropped.
        df = _df(["2026-06-03 23:45", "2026-06-04 00:00"])
        boundary = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
        out = closed_bars(df, boundary)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.index[-1], pd.Timestamp("2026-06-03 23:45", tz="UTC"))

    def test_keeps_all_when_none_forming(self):
        df = _df(["2026-06-03 23:30", "2026-06-03 23:45"])
        boundary = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(len(closed_bars(df, boundary)), 2)

    def test_rollover_bar_becomes_current_at_next_boundary(self):
        # At the 00:15 boundary the 00:00 (rollover) bar has closed -> it is the
        # bar evaluated, matching the backtest's completed-bar close.
        df = _df(["2026-06-04 00:00", "2026-06-04 00:15"])
        boundary = datetime(2026, 6, 4, 0, 15, tzinfo=timezone.utc)
        out = closed_bars(df, boundary)
        self.assertEqual(out.index[-1], pd.Timestamp("2026-06-04 00:00", tz="UTC"))


if __name__ == "__main__":
    unittest.main()
