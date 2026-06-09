"""Unit tests for the strategy framework contract (drift/strategies/base.py).

Covers: Decision convenience constructors, the registry (register / get_strategy
including duplicate and unknown-name errors), and a runtime_checkable smoke test
for the Strategy protocol.

No MT5 calls are made here.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure project root is on the path when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from drift.strategies.base import (  # noqa: E402
    STRATEGY_REGISTRY,
    Decision,
    MarketData,
    Signal,
    Strategy,
    StrategyContext,
    get_strategy,
    register,
)


def _make_signal() -> Signal:
    now = datetime.now(tz=timezone.utc)
    return Signal(
        action="buy",
        pair="EURCHF",
        timestamp=now,
        m15_candle_time=now,
        h4_candle_time=now,
        entry_price=0.95,
    )


# ---------------------------------------------------------------------------
# Decision constructors
# ---------------------------------------------------------------------------


def test_decision_open_carries_signal() -> None:
    signal = _make_signal()
    decision = Decision.open(signal)
    assert decision.kind == "open"
    assert decision.signal is signal
    assert decision.ticket is None
    assert decision.reason == ""


def test_decision_close_carries_ticket_and_reason() -> None:
    decision = Decision.close(12345, reason="manual")
    assert decision.kind == "close"
    assert decision.ticket == 12345
    assert decision.reason == "manual"
    assert decision.signal is None


def test_decision_close_all_carries_reason() -> None:
    decision = Decision.close_all(reason="session_end_time_stop")
    assert decision.kind == "close_all"
    assert decision.reason == "session_end_time_stop"
    assert decision.ticket is None
    assert decision.signal is None


def test_decision_noop_without_signal() -> None:
    decision = Decision.noop()
    assert decision.kind == "noop"
    assert decision.signal is None


def test_decision_noop_carries_rejection_signal() -> None:
    signal = _make_signal()
    signal.action = "none"
    signal.rejection_reason = "outside_window"
    decision = Decision.noop(signal)
    assert decision.kind == "noop"
    assert decision.signal is signal
    assert decision.signal.rejection_reason == "outside_window"


# ---------------------------------------------------------------------------
# Signal defaults
# ---------------------------------------------------------------------------


def test_signal_optional_fields_default_to_nan() -> None:
    signal = _make_signal()
    assert signal.sl != signal.sl  # NaN
    assert signal.range_high != signal.range_high
    assert signal.rsi != signal.rsi
    assert signal.h4_adx != signal.h4_adx
    assert signal.rejection_reason is None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Snapshot and restore the global registry around each test."""
    snapshot = dict(STRATEGY_REGISTRY)
    yield
    STRATEGY_REGISTRY.clear()
    STRATEGY_REGISTRY.update(snapshot)


def test_register_and_get_strategy() -> None:
    class DummyStrategy:
        name = "dummy_register"

    returned = register(DummyStrategy)
    assert returned is DummyStrategy  # usable as a decorator
    assert STRATEGY_REGISTRY["dummy_register"] is DummyStrategy
    assert get_strategy("dummy_register") is DummyStrategy


def test_register_duplicate_name_raises() -> None:
    class FirstStrategy:
        name = "dup_name"

    class SecondStrategy:
        name = "dup_name"

    register(FirstStrategy)
    with pytest.raises(ValueError, match="already registered"):
        register(SecondStrategy)


def test_register_without_name_raises() -> None:
    class Nameless:
        pass

    with pytest.raises(ValueError, match="non-empty string 'name'"):
        register(Nameless)


def test_register_empty_name_raises() -> None:
    class EmptyName:
        name = ""

    with pytest.raises(ValueError, match="non-empty string 'name'"):
        register(EmptyName)


def test_get_strategy_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="No strategy registered"):
        get_strategy("does_not_exist")


# ---------------------------------------------------------------------------
# Protocol smoke tests (runtime_checkable)
# ---------------------------------------------------------------------------


def test_strategy_protocol_isinstance() -> None:
    class ConformingStrategy:
        name = "conforming"
        pairs = ["EURCHF"]
        timeframes = frozenset({"M15"})

        def on_bar(self, pair, timeframe, bar_close_time, market, ctx) -> Decision:
            return Decision.noop()

        def on_fill(self, pair, signal, ticket) -> None:
            pass

        def on_order_rejected(self, pair, signal, reason) -> None:
            pass

    assert isinstance(ConformingStrategy(), Strategy)


def test_non_conforming_class_is_not_strategy() -> None:
    class NotAStrategy:
        name = "nope"

    assert not isinstance(NotAStrategy(), Strategy)


def test_market_data_protocol_isinstance() -> None:
    class FakeMarket:
        def candles(self, pair, timeframe, count):
            return None

    assert isinstance(FakeMarket(), MarketData)


# ---------------------------------------------------------------------------
# StrategyContext
# ---------------------------------------------------------------------------


def test_strategy_context_defaults() -> None:
    ctx = StrategyContext(
        account_balance=2500.0,
        account_equity=2510.0,
        allocated_capital=2500.0,
    )
    assert ctx.open_positions == []
    assert ctx.paused is False
