"""Tests for the closed-bar filter: only completed bars are evaluated (D045).

The filter is applied by ``EngineMarketData.candles``; the standalone
``closed_bars`` helper that used to live in ``drift.engine`` was removed in
cleanup #10 because it was dead in production.  Coverage is equivalent:
construct an ``EngineMarketData`` at a given boundary and assert that the
still-forming bar is excluded.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from drift import engine as engine_mod  # noqa: E402
from drift.engine import EngineMarketData  # noqa: E402


def _df(times: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(t, tz="UTC") for t in times], name="time")
    return pd.DataFrame({"close": range(len(times))}, index=idx)


class TestClosedBarsViaEngineMarketData(unittest.TestCase):
    def test_drops_forming_bar_at_boundary(self):
        # At the 00:00 boundary, the 00:00 bar is still forming -> dropped.
        df = _df(["2026-06-03 23:45", "2026-06-04 00:00"])
        boundary = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
        with mock.patch.object(engine_mod, "get_candles", return_value=df):
            market = EngineMarketData(boundary)
            out = market.candles("EURCHF", "M15", 150)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.index[-1], pd.Timestamp("2026-06-03 23:45", tz="UTC"))

    def test_keeps_all_when_none_forming(self):
        df = _df(["2026-06-03 23:30", "2026-06-03 23:45"])
        boundary = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
        with mock.patch.object(engine_mod, "get_candles", return_value=df):
            market = EngineMarketData(boundary)
            out = market.candles("EURCHF", "M15", 150)
        self.assertEqual(len(out), 2)

    def test_rollover_bar_becomes_current_at_next_boundary(self):
        # At the 00:15 boundary the 00:00 (rollover) bar has closed -> it is the
        # bar evaluated, matching the backtest's completed-bar close.
        df = _df(["2026-06-04 00:00", "2026-06-04 00:15"])
        boundary = datetime(2026, 6, 4, 0, 15, tzinfo=timezone.utc)
        with mock.patch.object(engine_mod, "get_candles", return_value=df):
            market = EngineMarketData(boundary)
            out = market.candles("EURCHF", "M15", 150)
        self.assertEqual(out.index[-1], pd.Timestamp("2026-06-04 00:00", tz="UTC"))


if __name__ == "__main__":
    unittest.main()
