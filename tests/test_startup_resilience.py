"""Tests for resilient Telegram setup at startup (D049).

A transient DNS/network failure while bringing up the Telegram control plane
must not kill the autonomous bot — it should retry with backoff and only give
up (re-raise) after exhausting all attempts.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import main  # noqa: E402

_CONFIG = SimpleNamespace(telegram=SimpleNamespace(bot_token="token", chat_id="chat"))


class TestTelegramResilientSetup(unittest.TestCase):
    def test_retries_transient_failure_then_succeeds(self) -> None:
        bot_app = MagicMock()
        loop = MagicMock()
        # attempt 1: setup_bot call raises; attempt 2: setup ok, then initialize ok
        loop.run_until_complete.side_effect = [
            ConnectionError("getaddrinfo failed"),
            bot_app,  # setup_bot(...) result
            None,  # bot_app.initialize() result
        ]
        with (
            mock.patch.object(main, "setup_bot", MagicMock()),
            mock.patch.object(main.time, "sleep") as sleep,
        ):
            result = main._setup_telegram_resilient(_CONFIG, MagicMock(), loop)

        self.assertIs(result, bot_app)
        self.assertEqual(loop.run_until_complete.call_count, 3)
        sleep.assert_called_once()  # backed off exactly once between the two attempts

    def test_reraises_after_exhausting_attempts(self) -> None:
        loop = MagicMock()
        loop.run_until_complete.side_effect = ConnectionError("getaddrinfo failed")
        with (
            mock.patch.object(main, "setup_bot", MagicMock()),
            mock.patch.object(main.time, "sleep"),
            mock.patch.object(main, "_TELEGRAM_SETUP_MAX_ATTEMPTS", 3),
        ):
            with self.assertRaises(ConnectionError):
                main._setup_telegram_resilient(_CONFIG, MagicMock(), loop)

        self.assertEqual(loop.run_until_complete.call_count, 3)


class TestShutdownClosesAllStrategyMagics(unittest.TestCase):
    """_shutdown with stop_requested must close positions for every strategy magic (D052).

    Strategies with magic_offset > 0 have an effective magic different from the
    base magic_number.  The old code only queried the base magic, leaving those
    positions open after /stop.
    """

    def _make_config(self):
        """Two enabled strategies: lull at offset 0, scalper at offset 1."""
        lull_cfg = SimpleNamespace(magic_offset=0, enabled=True)
        scalper_cfg = SimpleNamespace(magic_offset=1, enabled=True)
        return SimpleNamespace(
            system=SimpleNamespace(magic_number=234000),
            telegram=SimpleNamespace(chat_id="chat"),
            strategies={"daily_lull": lull_cfg, "scalper": scalper_cfg},
        )

    def _run_shutdown(self, config, state, *, positions_by_magic=None):
        """Run _shutdown with all MT5/DB/Telegram collaborators mocked.

        Returns ``(mock_get_open_positions, mock_close_trade)`` for assertions.
        """
        positions_by_magic = positions_by_magic or {}
        bot_app = MagicMock()
        shutdown_event = MagicMock()

        with (
            mock.patch.object(
                main,
                "get_open_positions",
                side_effect=lambda magic: positions_by_magic.get(magic, []),
            ) as mock_gop,
            mock.patch.object(main, "close_trade") as mock_ct,
            mock.patch.object(main, "get_balance", return_value=1000.0),
            mock.patch.object(main, "get_connection"),
            mock.patch.object(main, "log_event"),
            mock.patch.object(main, "disconnect"),
            mock.patch.object(main, "_fire_and_forget"),
            mock.patch.object(main, "notify_bot_status"),
            mock.patch.object(main.time, "sleep"),
        ):
            main._shutdown(config, state, bot_app, shutdown_event)

        return mock_gop, mock_ct

    def test_stop_requested_closes_positions_for_offset_zero_strategy(self) -> None:
        config = self._make_config()
        state = SimpleNamespace(stop_requested=True)
        lull_pos = {"ticket": 101, "pair": "EURCHF", "volume": 0.01, "direction": "buy"}

        mock_gop, mock_ct = self._run_shutdown(
            config, state, positions_by_magic={234000: [lull_pos], 234001: []}
        )

        # Both magic numbers must be queried (one per enabled strategy)
        queried_magics = {call.args[0] for call in mock_gop.call_args_list}
        self.assertIn(234000, queried_magics)
        self.assertIn(234001, queried_magics)

        # close_trade called once for the lull position with base magic
        mock_ct.assert_called_once_with(
            ticket=101,
            pair="EURCHF",
            lot_size=0.01,
            direction="buy",
            magic=234000,
        )

    def test_stop_requested_closes_positions_for_nonzero_offset_strategy(self) -> None:
        config = self._make_config()
        state = SimpleNamespace(stop_requested=True)
        scalper_pos = {"ticket": 202, "pair": "AUDNZD", "volume": 0.02, "direction": "sell"}

        _, mock_ct = self._run_shutdown(
            config, state, positions_by_magic={234000: [], 234001: [scalper_pos]}
        )

        # Scalper position (offset=1, magic=234001) must be closed with its magic
        mock_ct.assert_called_once_with(
            ticket=202,
            pair="AUDNZD",
            lot_size=0.02,
            direction="sell",
            magic=234001,
        )

    def test_stop_not_requested_skips_position_close(self) -> None:
        config = self._make_config()
        state = SimpleNamespace(stop_requested=False)

        mock_gop, mock_ct = self._run_shutdown(config, state)

        mock_gop.assert_not_called()
        mock_ct.assert_not_called()


if __name__ == "__main__":
    unittest.main()
