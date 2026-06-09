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


if __name__ == "__main__":
    unittest.main()
