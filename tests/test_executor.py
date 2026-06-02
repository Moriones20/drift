"""Tests for order execution retry on transient broker rejections (D040)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from drift import executor

DONE = 10009  # mt5.TRADE_RETCODE_DONE
MARKET_CLOSED = 10018  # transient
INVALID_STOPS = 10016  # fatal


def _result(retcode: int, order: int = 0, comment: str = "") -> SimpleNamespace:
    return SimpleNamespace(retcode=retcode, order=order, comment=comment)


def _fake_mt5(order_send_results: list) -> mock.MagicMock:
    m = mock.MagicMock()
    m.TRADE_RETCODE_DONE = DONE
    m.symbol_info.return_value = SimpleNamespace(digits=5)
    m.symbol_info_tick.return_value = SimpleNamespace(ask=1.10000, bid=1.09990)
    m.order_send.side_effect = order_send_results
    return m


class TestOpenTradeRetry(unittest.TestCase):
    def test_retries_transient_then_succeeds(self):
        # Two "market closed" rejections, then success on the third attempt.
        results = [_result(MARKET_CLOSED), _result(MARKET_CLOSED), _result(DONE, order=555)]
        fake = _fake_mt5(results)
        with mock.patch.object(executor, "mt5", fake):
            ticket = executor.open_trade(
                "EURJPY",
                "buy",
                0.1,
                185.6,
                185.9,
                magic=234000,
                max_retries=3,
                retry_delay_seconds=0,
            )
        self.assertEqual(ticket, 555)
        self.assertEqual(fake.order_send.call_count, 3)

    def test_fatal_retcode_fails_without_retry(self):
        # Invalid stops is fatal — must not retry even with budget available.
        fake = _fake_mt5([_result(INVALID_STOPS, comment="Invalid stops")])
        with mock.patch.object(executor, "mt5", fake):
            ticket = executor.open_trade(
                "EURJPY",
                "buy",
                0.1,
                185.6,
                185.9,
                magic=234000,
                max_retries=3,
                retry_delay_seconds=0,
            )
        self.assertIsNone(ticket)
        self.assertEqual(fake.order_send.call_count, 1)

    def test_transient_exhausts_retries_returns_none(self):
        results = [_result(MARKET_CLOSED)] * 3  # max_retries=2 -> 3 attempts
        fake = _fake_mt5(results)
        with mock.patch.object(executor, "mt5", fake):
            ticket = executor.open_trade(
                "EURJPY",
                "sell",
                0.1,
                186.1,
                185.9,
                magic=234000,
                max_retries=2,
                retry_delay_seconds=0,
            )
        self.assertIsNone(ticket)
        self.assertEqual(fake.order_send.call_count, 3)

    def test_default_is_single_shot(self):
        # Backward-compat: no retry args -> exactly one attempt.
        fake = _fake_mt5([_result(MARKET_CLOSED)])
        with mock.patch.object(executor, "mt5", fake):
            ticket = executor.open_trade("EURJPY", "buy", 0.1, 185.6, 185.9, magic=234000)
        self.assertIsNone(ticket)
        self.assertEqual(fake.order_send.call_count, 1)

    def test_guard_abandons_when_price_reverts_on_retry(self):
        # Attempt 1 rejected (market closed); on retry the bid has reverted
        # above the buy boundary -> abandon WITHOUT sending a second order.
        fake = _fake_mt5([_result(MARKET_CLOSED), _result(DONE, order=777)])
        fake.symbol_info_tick.side_effect = [
            SimpleNamespace(ask=1.10010, bid=1.10000),  # attempt 1: at the floor
            SimpleNamespace(ask=1.10060, bid=1.10050),  # attempt 2: reverted up
        ]
        with mock.patch.object(executor, "mt5", fake):
            ticket = executor.open_trade(
                "EURGBP",
                "buy",
                0.1,
                1.0980,
                1.1010,
                magic=234000,
                max_retries=3,
                retry_delay_seconds=0,
                guard_boundary=1.10000,
            )
        self.assertIsNone(ticket)
        self.assertEqual(fake.order_send.call_count, 1)  # no 2nd send

    def test_guard_allows_retry_when_still_at_extreme(self):
        # Attempt 1 rejected; on retry the bid is still at/below the boundary
        # -> proceed and fill.
        fake = _fake_mt5([_result(MARKET_CLOSED), _result(DONE, order=888)])
        fake.symbol_info_tick.side_effect = [
            SimpleNamespace(ask=1.10010, bid=1.10000),  # attempt 1
            SimpleNamespace(ask=1.10005, bid=0.99995),  # attempt 2: still <= boundary
        ]
        with mock.patch.object(executor, "mt5", fake):
            ticket = executor.open_trade(
                "EURGBP",
                "buy",
                0.1,
                1.0980,
                1.1010,
                magic=234000,
                max_retries=3,
                retry_delay_seconds=0,
                guard_boundary=1.10000,
            )
        self.assertEqual(ticket, 888)
        self.assertEqual(fake.order_send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
