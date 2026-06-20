"""Tests for LondonOrbStrategy (Step 44).

Mirror of test_daily_lull_strategy.py style: fake MarketData + StrategyContext,
deterministic candles, no MT5 calls.  All datetimes are UTC-aware (server-time
clock, reasoned over as-is per D041/D058).

Required test cases (spec):
  1. BUY breakout
  2. SELL breakout
  3. range_atr_ratio below range_atr_min → rejected, session un-tradeable
  4. range_atr_ratio above range_atr_max → rejected
  5. range_width_pips below per-pair pip floor → rejected
  6. Breakout candle during define-range window (10:00-10:59) → Decision.noop
  7. Second signal same day after one trade → Decision.noop
  8. 18:00 time-stop → Decision.close_all(reason="session_end_time_stop")
  9. Heartbeat: close_all with 0 open positions → Decision.close_all still fires
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root is on the path when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# pandas_ta stub (identical pattern to test_daily_lull_strategy.py)
# ---------------------------------------------------------------------------


def _try_import_pandas_ta() -> bool:
    try:
        import pandas_ta  # noqa: F401

        return True
    except Exception:
        return False


HAS_PANDAS_TA = _try_import_pandas_ta()

if not HAS_PANDAS_TA and "pandas_ta" not in sys.modules:
    from unittest.mock import MagicMock

    _pta_stub = MagicMock()

    def _atr_stub(high, low, close, length=14, **_kw):
        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(span=length, adjust=False).mean()
        atr.name = f"ATRr_{length}"
        return atr

    _pta_stub.atr = _atr_stub
    sys.modules["pandas_ta"] = _pta_stub

# ---------------------------------------------------------------------------
# Lazy imports (after stub injection)
# ---------------------------------------------------------------------------

from drift.strategies.base import STRATEGY_REGISTRY, Decision, MarketData, Signal  # noqa: E402
from drift.strategies.london_orb import (  # noqa: E402
    LondonOrbParams,
    LondonOrbStrategy,
    OrbSessionState,
)

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Default params used across most tests
# ---------------------------------------------------------------------------

_DEFAULT_PAIRS = ["GBPJPY", "GBPUSD", "EURJPY", "EURUSD"]
_DEFAULT_PAIR = "GBPUSD"

# A range that will comfortably pass both ATR-ratio and pip-floor filters
# with the synthetic candle data used in tests.
_DEFAULT_PARAMS = LondonOrbParams(
    range_start_hour=10,
    range_end_hour=11,
    time_stop_hour=18,
    range_atr_min=0.5,
    range_atr_max=2.0,
    range_pip_floor={"GBPJPY": 18.0, "GBPUSD": 10.0, "EURJPY": 12.0, "EURUSD": 8.0},
    tp_mult=1.0,
    atr_period=14,
)


# ---------------------------------------------------------------------------
# Fake MarketData
# ---------------------------------------------------------------------------


class _FakeMarket:
    """A MarketData that serves pre-built (already closed) DataFrames."""

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self._frames = frames

    def candles(self, pair: str, timeframe: str, count: int = 100) -> pd.DataFrame:
        return self._frames[(pair, timeframe)]


def _fake_ctx() -> object:
    from drift.strategies.base import StrategyContext

    return StrategyContext(
        account_balance=10_000.0,
        account_equity=10_000.0,
        allocated_capital=10_000.0,
        open_positions=[],
        paused=False,
    )


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------


def _make_m15_df(
    n: int = 150,
    base_price: float = 1.2800,
    last_close: float | None = None,
    last_high: float | None = None,
    last_low: float | None = None,
    end_hour: int = 11,
    end_minute: int = 15,
    date: datetime | None = None,
) -> pd.DataFrame:
    if date is None:
        date = datetime(2026, 1, 5, end_hour, end_minute, tzinfo=UTC)  # Monday
    else:
        date = date.replace(hour=end_hour, minute=end_minute, tzinfo=UTC)
    end_ts = date
    index = pd.date_range(end=end_ts, periods=n, freq="15min", tz=UTC)

    rng = np.random.default_rng(42)
    close = base_price + rng.normal(0, base_price * 0.0002, n).cumsum() * 0.1
    close = np.clip(close, base_price * 0.99, base_price * 1.01)
    spread = base_price * 0.0005
    high = close + rng.uniform(0, spread, n)
    low = close - rng.uniform(0, spread, n)
    open_ = close + rng.uniform(-spread / 2, spread / 2, n)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 100},
        index=index,
    )
    if last_close is not None:
        df.iloc[-1, df.columns.get_loc("close")] = last_close
    if last_high is not None:
        df.iloc[-1, df.columns.get_loc("high")] = last_high
    if last_low is not None:
        df.iloc[-1, df.columns.get_loc("low")] = last_low
    return df


def _locked_state(
    range_high: float = 1.2850,
    range_low: float = 1.2800,
    atr_val: float = 0.0030,
    tradeable: bool = True,
) -> OrbSessionState:
    """Build a pre-locked OrbSessionState with a valid range."""
    s = OrbSessionState()
    range_width = range_high - range_low
    ratio = range_width / atr_val if atr_val > 0 else float("nan")
    s.session_date = datetime(2026, 1, 5, tzinfo=UTC).toordinal()
    s.high = range_high
    s.low = range_low
    s.locked = True
    s.tradeable = tradeable
    s.range_high = range_high
    s.range_low = range_low
    s.range_atr_ratio = ratio
    s.atr_at_lock = atr_val
    s.traded = False
    return s


def _signal_from_decision(decision: Decision) -> Signal:
    assert decision.signal is not None, f"Decision {decision.kind!r} carried no signal"
    return decision.signal


def _run(
    pair: str,
    m15: pd.DataFrame,
    state: OrbSessionState,
    params: LondonOrbParams = _DEFAULT_PARAMS,
    bar_close_time: datetime | None = None,
) -> Decision:
    """Run LondonOrbStrategy.on_bar with the given inputs."""
    strat = LondonOrbStrategy(pairs=[pair], params=params)
    strat._states[pair] = state
    market = _FakeMarket({(pair, "M15"): m15})
    if bar_close_time is None:
        bar_close_time = m15.index[-1].to_pydatetime()
    return strat.on_bar(pair, "M15", bar_close_time, market, _fake_ctx())


# ---------------------------------------------------------------------------
# Test 1 — BUY breakout
# ---------------------------------------------------------------------------


class TestBuyBreakout:
    def test_buy_breakout_triggers_open(self) -> None:
        """Close above range_high with a locked range → Decision.open(buy)."""
        range_high = 1.2850
        range_low = 1.2800
        state = _locked_state(range_high=range_high, range_low=range_low, atr_val=0.0030)
        # Last bar closes above the range high
        m15 = _make_m15_df(
            n=150,
            base_price=range_high,
            last_close=range_high + 0.0010,
            end_hour=11,
            end_minute=15,
        )
        decision = _run(_DEFAULT_PAIR, m15, state)
        assert decision.kind == "open"
        sig = _signal_from_decision(decision)
        assert sig.action == "buy"
        assert sig.entry_price > range_high
        # SL must be at range_low
        assert abs(sig.sl - range_low) < 1e-8
        # TP must be range_high + 1.0 × range_width
        range_width = range_high - range_low
        assert abs(sig.tp - (range_high + _DEFAULT_PARAMS.tp_mult * range_width)) < 1e-8
        assert sig.rejection_reason is None


# ---------------------------------------------------------------------------
# Test 2 — SELL breakout
# ---------------------------------------------------------------------------


class TestSellBreakout:
    def test_sell_breakout_triggers_open(self) -> None:
        """Close below range_low with a locked range → Decision.open(sell)."""
        range_high = 1.2850
        range_low = 1.2800
        state = _locked_state(range_high=range_high, range_low=range_low, atr_val=0.0030)
        # Last bar closes below the range low
        m15 = _make_m15_df(
            n=150,
            base_price=range_low,
            last_close=range_low - 0.0010,
            end_hour=11,
            end_minute=15,
        )
        decision = _run(_DEFAULT_PAIR, m15, state)
        assert decision.kind == "open"
        sig = _signal_from_decision(decision)
        assert sig.action == "sell"
        assert sig.entry_price < range_low
        # SL must be at range_high
        assert abs(sig.sl - range_high) < 1e-8
        # TP must be range_low - 1.0 × range_width
        range_width = range_high - range_low
        assert abs(sig.tp - (range_low - _DEFAULT_PARAMS.tp_mult * range_width)) < 1e-8
        assert sig.rejection_reason is None


# ---------------------------------------------------------------------------
# Test 3 — range_atr_ratio below range_atr_min (range too narrow in ATR terms)
# ---------------------------------------------------------------------------


class TestRangeAtrTooNarrow:
    def test_range_rejected_when_atr_ratio_below_min(self) -> None:
        """A range that is too narrow relative to ATR → session un-tradeable."""
        # Narrow params: require ratio >= 2.0 but the range will only produce ~0.5
        params = LondonOrbParams(
            range_start_hour=10,
            range_end_hour=11,
            time_stop_hour=18,
            range_atr_min=2.0,  # very high minimum
            range_atr_max=5.0,
            range_pip_floor={},  # no pip floor
            tp_mult=1.0,
            atr_period=14,
        )
        pair = "EURUSD"
        # Build state manually to control range dimensions precisely.
        strat = LondonOrbStrategy(pairs=[pair], params=params)
        state = strat._states[pair]
        # Simulate a narrow range (only ~0.0010 = ~10 pips vs big ATR)
        state.high = 1.1010
        state.low = 1.1000
        state.session_date = datetime(2026, 1, 5, tzinfo=UTC).toordinal()

        # Now run at 11:00 (lock moment)
        m15_lock = _make_m15_df(
            n=150, base_price=1.1005, last_close=1.1005, end_hour=11, end_minute=0
        )
        strat._states[pair] = state
        market = _FakeMarket({(pair, "M15"): m15_lock})
        boundary = m15_lock.index[-1].to_pydatetime()
        decision = strat.on_bar(pair, "M15", boundary, market, _fake_ctx())

        assert decision.kind == "noop"
        sig = _signal_from_decision(decision)
        assert sig.action == "none"
        assert not strat._states[pair].tradeable
        assert sig.rejection_reason is None  # lock returns "range_rejected", not a rejection_reason
        assert "range_rejected" in sig.reason

        # Now confirm that a subsequent trading-hour bar is also rejected
        m15_trade = _make_m15_df(
            n=150, base_price=1.1005, last_close=1.1020, end_hour=11, end_minute=15
        )
        market2 = _FakeMarket({(pair, "M15"): m15_trade})
        boundary2 = m15_trade.index[-1].to_pydatetime()
        decision2 = strat.on_bar(pair, "M15", boundary2, market2, _fake_ctx())
        assert decision2.kind == "noop"
        sig2 = _signal_from_decision(decision2)
        assert sig2.rejection_reason == "range_not_tradeable"


# ---------------------------------------------------------------------------
# Test 4 — range_atr_ratio above range_atr_max (range too wide)
# ---------------------------------------------------------------------------


class TestRangeAtrTooWide:
    def test_range_rejected_when_atr_ratio_above_max(self) -> None:
        """A range that is wider than range_atr_max × ATR → session un-tradeable."""
        params = LondonOrbParams(
            range_start_hour=10,
            range_end_hour=11,
            time_stop_hour=18,
            range_atr_min=0.1,
            range_atr_max=0.2,  # very low maximum: any meaningful range fails
            range_pip_floor={},
            tp_mult=1.0,
            atr_period=14,
        )
        pair = "EURUSD"
        strat = LondonOrbStrategy(pairs=[pair], params=params)
        state = strat._states[pair]
        # Wide range
        state.high = 1.1100
        state.low = 1.1000
        state.session_date = datetime(2026, 1, 5, tzinfo=UTC).toordinal()

        m15_lock = _make_m15_df(n=150, base_price=1.1050, end_hour=11, end_minute=0)
        strat._states[pair] = state
        market = _FakeMarket({(pair, "M15"): m15_lock})
        boundary = m15_lock.index[-1].to_pydatetime()
        decision = strat.on_bar(pair, "M15", boundary, market, _fake_ctx())

        assert decision.kind == "noop"
        assert not strat._states[pair].tradeable
        sig = _signal_from_decision(decision)
        assert "range_rejected" in sig.reason


# ---------------------------------------------------------------------------
# Test 5 — range_width_pips below per-pair pip floor
# ---------------------------------------------------------------------------


class TestRangePipFloor:
    def test_range_rejected_when_below_pip_floor(self) -> None:
        """Range passes ATR filter but is below the pair pip floor → un-tradeable."""
        pair = "GBPUSD"
        # Set a very high pip floor for GBPUSD (range will be ~10 pips, floor = 50)
        params = LondonOrbParams(
            range_start_hour=10,
            range_end_hour=11,
            time_stop_hour=18,
            range_atr_min=0.1,  # low ATR min so the ratio filter passes
            range_atr_max=10.0,  # high ATR max so the ratio filter passes
            range_pip_floor={"GBPUSD": 50.0},  # 50-pip floor; range will be ~10 pips
            tp_mult=1.0,
            atr_period=14,
        )
        strat = LondonOrbStrategy(pairs=[pair], params=params)
        state = strat._states[pair]
        # Range of ~10 pips (0.0010 in price)
        state.high = 1.2810
        state.low = 1.2800
        state.session_date = datetime(2026, 1, 5, tzinfo=UTC).toordinal()

        m15_lock = _make_m15_df(n=150, base_price=1.2805, end_hour=11, end_minute=0)
        strat._states[pair] = state
        market = _FakeMarket({(pair, "M15"): m15_lock})
        boundary = m15_lock.index[-1].to_pydatetime()
        decision = strat.on_bar(pair, "M15", boundary, market, _fake_ctx())

        assert decision.kind == "noop"
        assert not strat._states[pair].tradeable
        sig = _signal_from_decision(decision)
        assert "range_rejected" in sig.reason


# ---------------------------------------------------------------------------
# Test 6 — Breakout candle during define-range window (10:00-10:59) → noop
# ---------------------------------------------------------------------------


class TestNoEntryDuringDefineRange:
    def test_define_range_bars_never_trigger_entry(self) -> None:
        """Bars during 10:00-10:59 are accumulation only, never an entry signal."""
        range_high = 1.2850
        range_low = 1.2800
        state = _locked_state(range_high=range_high, range_low=range_low, atr_val=0.0030)
        # Bar at 10:30 with a close far above range_high
        m15 = _make_m15_df(
            n=150,
            base_price=range_high,
            last_close=range_high + 0.0050,
            end_hour=10,
            end_minute=30,
        )
        decision = _run(_DEFAULT_PAIR, m15, state)
        # Must be noop — no entry during define-range window
        assert decision.kind == "noop"
        sig = _signal_from_decision(decision)
        assert sig.action == "none"
        assert sig.rejection_reason is None  # "define_range" is not a rejection


# ---------------------------------------------------------------------------
# Test 7 — Second signal same day after one trade → noop
# ---------------------------------------------------------------------------


class TestNoReentryAfterTrade:
    def test_second_breakout_ignored_after_trade(self) -> None:
        """After one fill, subsequent breakouts on the same day are ignored."""
        range_high = 1.2850
        range_low = 1.2800
        state = _locked_state(range_high=range_high, range_low=range_low, atr_val=0.0030)
        state.traded = True  # simulate on_fill having been called

        m15 = _make_m15_df(
            n=150,
            base_price=range_high,
            last_close=range_high + 0.0010,
            end_hour=11,
            end_minute=30,
        )
        decision = _run(_DEFAULT_PAIR, m15, state)
        assert decision.kind == "noop"
        sig = _signal_from_decision(decision)
        assert sig.rejection_reason == "already_traded_this_session"


# ---------------------------------------------------------------------------
# Test 8 — 18:00 time-stop → Decision.close_all
# ---------------------------------------------------------------------------


class TestTimeStop:
    def test_time_stop_at_18_00_returns_close_all(self) -> None:
        """18:00 boundary → Decision.close_all with signal.reason='session_end_time_stop'."""
        state = _locked_state()
        # Fresh bar: 17:45 (just before 18:00)
        m15 = _make_m15_df(n=150, end_hour=17, end_minute=45)
        boundary = datetime(2026, 1, 5, 18, 0, tzinfo=UTC)
        decision = _run(_DEFAULT_PAIR, m15, state, bar_close_time=boundary)
        assert decision.kind == "close_all"
        assert decision.reason == "session_close"
        sig = _signal_from_decision(decision)
        assert sig.reason == "session_end_time_stop"
        assert sig.action == "none"

    def test_time_stop_resets_state(self) -> None:
        """After the time stop fires the per-pair state is reset."""
        strat = LondonOrbStrategy(pairs=[_DEFAULT_PAIR], params=_DEFAULT_PARAMS)
        strat._states[_DEFAULT_PAIR] = _locked_state()
        m15 = _make_m15_df(n=150, end_hour=17, end_minute=45)
        market = _FakeMarket({(_DEFAULT_PAIR, "M15"): m15})
        boundary = datetime(2026, 1, 5, 18, 0, tzinfo=UTC)
        strat.on_bar(_DEFAULT_PAIR, "M15", boundary, market, _fake_ctx())
        s = strat._states[_DEFAULT_PAIR]
        assert not s.locked
        assert not s.tradeable
        assert not s.traded
        assert s.session_date is None


# ---------------------------------------------------------------------------
# Test 9 — Heartbeat: close_all even with 0 open positions
# ---------------------------------------------------------------------------


class TestHeartbeatWithZeroPositions:
    def test_close_all_returned_even_with_no_positions(self) -> None:
        """The time stop returns close_all even when ctx has no open positions.

        This validates the D066 / L4 pattern: the session-closed heartbeat must
        fire on every quiet night, not just when trades were open.
        """
        from drift.strategies.base import StrategyContext

        strat = LondonOrbStrategy(pairs=[_DEFAULT_PAIR], params=_DEFAULT_PARAMS)
        strat._states[_DEFAULT_PAIR] = _locked_state()
        m15 = _make_m15_df(n=150, end_hour=17, end_minute=45)
        market = _FakeMarket({(_DEFAULT_PAIR, "M15"): m15})
        boundary = datetime(2026, 1, 5, 18, 0, tzinfo=UTC)

        # Explicitly pass ctx with 0 open positions
        ctx = StrategyContext(
            account_balance=10_000.0,
            account_equity=10_000.0,
            allocated_capital=10_000.0,
            open_positions=[],  # no trades open
            paused=False,
        )
        decision = strat.on_bar(_DEFAULT_PAIR, "M15", boundary, market, ctx)
        assert decision.kind == "close_all"
        assert decision.reason == "session_close"
        sig = _signal_from_decision(decision)
        assert sig.reason == "session_end_time_stop"


# ---------------------------------------------------------------------------
# Params + registry tests
# ---------------------------------------------------------------------------


class TestParamsAndRegistry:
    def test_from_dict_parses_known_fields_and_ignores_unknown(self) -> None:
        params = LondonOrbParams.from_dict(
            {
                "range_atr_min": 0.8,
                "tp_mult": 1.5,
                "unknown_key": 99,
                "range_pip_floor": {"GBPJPY": 20.0, "EURUSD": 5.0},
            }
        )
        assert params.range_atr_min == 0.8
        assert params.tp_mult == 1.5
        assert params.range_pip_floor == {"GBPJPY": 20.0, "EURUSD": 5.0}
        # Defaults preserved
        assert params.range_end_hour == 11
        assert params.atr_period == 14

    def test_from_dict_empty_uses_defaults(self) -> None:
        params = LondonOrbParams.from_dict({})
        defaults = LondonOrbParams()
        for f in LondonOrbParams.__dataclass_fields__:
            assert getattr(params, f) == getattr(defaults, f), f

    def test_range_pip_floor_missing_pair_defaults_to_zero(self) -> None:
        params = LondonOrbParams.from_dict({"range_pip_floor": {"GBPJPY": 18.0}})
        # Missing pair → floor 0 (no restriction)
        assert params.range_pip_floor.get("EURUSD", 0.0) == 0.0

    def test_registered_under_name(self) -> None:
        assert STRATEGY_REGISTRY.get("london_orb") is LondonOrbStrategy
        assert LondonOrbStrategy.name == "london_orb"
        assert LondonOrbStrategy.timeframes == frozenset({"M15"})

    def test_isinstance_marketdata_protocol(self) -> None:
        market = _FakeMarket({})
        assert isinstance(market, MarketData)


# ---------------------------------------------------------------------------
# Lifecycle callbacks
# ---------------------------------------------------------------------------


class TestLifecycleCallbacks:
    def test_on_bar_does_not_set_traded_on_open(self) -> None:
        """A buy Decision.open must NOT flip traded inside on_bar."""
        range_high = 1.2850
        range_low = 1.2800
        state = _locked_state(range_high=range_high, range_low=range_low, atr_val=0.0030)
        strat = LondonOrbStrategy(pairs=[_DEFAULT_PAIR], params=_DEFAULT_PARAMS)
        strat._states[_DEFAULT_PAIR] = state
        m15 = _make_m15_df(
            n=150, base_price=range_high, last_close=range_high + 0.0010, end_hour=11, end_minute=15
        )
        market = _FakeMarket({(_DEFAULT_PAIR, "M15"): m15})
        boundary = m15.index[-1].to_pydatetime()
        decision = strat.on_bar(_DEFAULT_PAIR, "M15", boundary, market, _fake_ctx())
        if decision.kind == "open":
            assert strat._states[_DEFAULT_PAIR].traded is False

    def test_on_fill_sets_traded(self) -> None:
        strat = LondonOrbStrategy(pairs=[_DEFAULT_PAIR], params=_DEFAULT_PARAMS)
        sig = Signal(
            action="buy",
            pair=_DEFAULT_PAIR,
            timestamp=datetime(2026, 1, 5, 11, 15, tzinfo=UTC),
            m15_candle_time=datetime(2026, 1, 5, 11, 15, tzinfo=UTC),
            h4_candle_time=datetime(2026, 1, 5, 11, 15, tzinfo=UTC),
            entry_price=1.2855,
        )
        assert strat._states[_DEFAULT_PAIR].traded is False
        strat.on_fill(_DEFAULT_PAIR, sig, ticket=99001)
        assert strat._states[_DEFAULT_PAIR].traded is True

    def test_on_order_rejected_leaves_traded_false(self) -> None:
        strat = LondonOrbStrategy(pairs=[_DEFAULT_PAIR], params=_DEFAULT_PARAMS)
        sig = Signal(
            action="sell",
            pair=_DEFAULT_PAIR,
            timestamp=datetime(2026, 1, 5, 11, 15, tzinfo=UTC),
            m15_candle_time=datetime(2026, 1, 5, 11, 15, tzinfo=UTC),
            h4_candle_time=datetime(2026, 1, 5, 11, 15, tzinfo=UTC),
            entry_price=1.2795,
        )
        strat.on_order_rejected(_DEFAULT_PAIR, sig, reason="risk: max_open_trades")
        assert strat._states[_DEFAULT_PAIR].traded is False


# ---------------------------------------------------------------------------
# next_wake scheduling
# ---------------------------------------------------------------------------


class TestNextWake:
    def test_inside_window_returns_next_m15(self) -> None:
        """Inside 10:00-18:00 on a weekday, next_wake is the next M15 boundary."""
        strat = LondonOrbStrategy(pairs=[_DEFAULT_PAIR], params=_DEFAULT_PARAMS)
        now = datetime(2026, 1, 5, 11, 7, tzinfo=UTC)  # Monday 11:07
        wake = strat.next_wake(now)
        assert wake is not None
        assert wake == datetime(2026, 1, 5, 11, 15, tzinfo=UTC)

    def test_outside_window_returns_next_10_00(self) -> None:
        """After 18:00 server time, next_wake is the next weekday 10:00."""
        strat = LondonOrbStrategy(pairs=[_DEFAULT_PAIR], params=_DEFAULT_PARAMS)
        now = datetime(2026, 1, 5, 18, 1, tzinfo=UTC)  # Monday 18:01
        wake = strat.next_wake(now)
        assert wake is not None
        # Next weekday 10:00 is Tuesday 2026-01-06
        assert wake == datetime(2026, 1, 6, 10, 0, tzinfo=UTC)

    def test_friday_18_01_returns_monday_10_00(self) -> None:
        """After Friday session end, next_wake jumps past the weekend."""
        strat = LondonOrbStrategy(pairs=[_DEFAULT_PAIR], params=_DEFAULT_PARAMS)
        now = datetime(2026, 1, 9, 18, 1, tzinfo=UTC)  # Friday 18:01
        wake = strat.next_wake(now)
        assert wake is not None
        # Monday 2026-01-12
        assert wake == datetime(2026, 1, 12, 10, 0, tzinfo=UTC)

    def test_outside_window_weekend(self) -> None:
        """On Saturday, next_wake is Monday 10:00."""
        strat = LondonOrbStrategy(pairs=[_DEFAULT_PAIR], params=_DEFAULT_PARAMS)
        now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)  # Saturday
        wake = strat.next_wake(now)
        assert wake is not None
        assert wake == datetime(2026, 1, 12, 10, 0, tzinfo=UTC)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
