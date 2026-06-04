"""Tests for the rollover-aware post-close execution delay (D044)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import main


def _cfg(rollover_settle: int = 150):
    return SimpleNamespace(system=SimpleNamespace(rollover_settle_seconds=rollover_settle))


class TestPostCloseDelay(unittest.TestCase):
    def test_rollover_candle_gets_settle_delay(self):
        # The 00:00 server candle lands on the broker rollover -> long settle.
        nc = datetime(2026, 6, 3, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(main._post_close_delay_seconds(nc, _cfg(150)), 150)

    def test_normal_candles_get_short_delay(self):
        # Every other in-window candle (incl. 02:00 close, 23:00 lock) is normal.
        for hh, mm in [(0, 15), (23, 0), (1, 45), (21, 0), (2, 0)]:
            nc = datetime(2026, 6, 3, hh, mm, tzinfo=timezone.utc)
            self.assertEqual(
                main._post_close_delay_seconds(nc, _cfg(150)),
                main.CANDLE_CLOSE_DELAY_SECONDS,
            )

    def test_settle_delay_is_configurable(self):
        nc = datetime(2026, 6, 3, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(main._post_close_delay_seconds(nc, _cfg(200)), 200)


if __name__ == "__main__":
    unittest.main()
