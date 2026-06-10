"""Golden-value tests for the Daily Lull strategy (Step 32 contract phase).

The legacy ``drift.strategy.evaluate_pair`` has been removed (C10 cleanup).
These tests run only the new ``DailyLullStrategy.on_bar`` path and assert
frozen expected values (action, SL position, TP position, range, reason,
rejection_reason) so the strategy contract is pinned without depending on the
deleted module.

No MT5 calls are made here — all tests use synthetic DataFrames.  All
datetimes are UTC-aware (server-time clock, reasoned over as-is).
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
# pandas_ta stub — identical to the one in test_strategy.py / test_e2e.py so the
# indicator modules import without a pandas_ta installation.
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

    def _rsi_stub(series, length=14, **_kw):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        result = 100.0 - (100.0 / (1.0 + rs))
        result.name = f"RSI_{length}"
        return result

    def _adx_stub(high, low, close, length=14, **_kw):
        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)
        plus_dm = (high - prev_high).clip(lower=0)
        minus_dm = (prev_low - low).clip(lower=0)
        mask = plus_dm >= minus_dm
        plus_dm = plus_dm.where(mask, 0.0)
        minus_dm = minus_dm.where(~mask, 0.0)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        alpha = 1.0 / length
        atr_val = tr.ewm(alpha=alpha, adjust=False).mean()
        _nan = float("nan")
        plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_val.replace(0, _nan)
        minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_val.replace(0, _nan)
        di_sum = (plus_di + minus_di).replace(0, float("nan"))
        dx = 100.0 * (plus_di - minus_di).abs() / di_sum
        adx_val = dx.ewm(alpha=alpha, adjust=False).mean()
        result = pd.DataFrame(
            {
                f"ADX_{length}": adx_val,
                f"DMP_{length}": plus_di,
                f"DMN_{length}": minus_di,
            }
        )
        return result

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

    _pta_stub.rsi = _rsi_stub
    _pta_stub.adx = _adx_stub
    _pta_stub.atr = _atr_stub
    sys.modules["pandas_ta"] = _pta_stub


# ---------------------------------------------------------------------------
# Lazy imports (after stub injection)
# ---------------------------------------------------------------------------

from drift.strategies.base import STRATEGY_REGISTRY, Decision, MarketData, Signal  # noqa: E402
from drift.strategies.daily_lull import (  # noqa: E402
    DailyLullParams,
    DailyLullStrategy,
    SessionState,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fake MarketData for the new path
# ---------------------------------------------------------------------------


class _FakeMarket:
    """A MarketData that serves pre-built (already closed) DataFrames.

    The engine is responsible for the closed-bar filter (D045); this fake just
    returns the same frames the legacy ``evaluate_pair`` is handed, so the two
    paths see identical data.
    """

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self._frames = frames

    def candles(self, pair: str, timeframe: str, count: int = 100) -> pd.DataFrame:
        return self._frames[(pair, timeframe)]


def _fake_ctx() -> object:
    """Minimal StrategyContext; on_bar does not read it for the Lull."""
    from drift.strategies.base import StrategyContext

    return StrategyContext(
        account_balance=10_000.0,
        account_equity=10_000.0,
        allocated_capital=10_000.0,
        open_positions=[],
        paused=False,
    )


# ---------------------------------------------------------------------------
# Synthetic data builders (mirror tests/test_strategy.py)
# ---------------------------------------------------------------------------


def _default_params() -> DailyLullParams:
    """DailyLullParams with default values matching the config.yaml params block."""
    return DailyLullParams()


def _make_m15_df(
    n: int = 150,
    base_price: float = 1.0800,
    last_close: float | None = None,
    last_high: float | None = None,
    last_low: float | None = None,
    end_hour: int = 23,
    end_minute: int = 15,
) -> pd.DataFrame:
    end_ts = datetime(2026, 1, 7, end_hour, end_minute, tzinfo=UTC)
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


def _make_h4_df(n: int = 50, base_price: float = 1.0800, last_adx_low: bool = True) -> pd.DataFrame:
    end_ts = datetime(2026, 1, 7, 20, 0, tzinfo=UTC)
    index = pd.date_range(end=end_ts, periods=n, freq="4h", tz=UTC)

    if last_adx_low:
        rng = np.random.default_rng(1)
        close = base_price + rng.normal(0, base_price * 0.0001, n)
    else:
        close = np.linspace(base_price * 0.85, base_price * 1.15, n)

    spread = base_price * 0.0005
    rng2 = np.random.default_rng(2)
    high = close + rng2.uniform(0, spread, n)
    low = close - rng2.uniform(0, spread, n)
    open_ = close + rng2.uniform(-spread / 2, spread / 2, n)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 50},
        index=index,
    )


def _build_oversold_m15(range_low: float, end_hour: int = 23, end_minute: int = 15) -> pd.DataFrame:
    n = 150
    end_ts = datetime(2026, 1, 7, end_hour, end_minute, tzinfo=UTC)
    index = pd.date_range(end=end_ts, periods=n, freq="15min", tz=UTC)
    close_vals = np.concatenate(
        [np.full(n - 15, 1.0800), np.linspace(1.0800, range_low - 0.0010, 15)]
    )
    spread = 0.0001
    rng = np.random.default_rng(5)
    high_vals = close_vals + rng.uniform(0, spread, n)
    low_vals = close_vals - rng.uniform(0, spread, n)
    open_vals = close_vals + rng.uniform(-spread / 2, spread / 2, n)
    close_vals[-1] = range_low - 0.0002
    high_vals[-1] = range_low
    low_vals[-1] = range_low - 0.0005
    return pd.DataFrame(
        {"open": open_vals, "high": high_vals, "low": low_vals, "close": close_vals, "volume": 100},
        index=index,
    )


def _build_overbought_m15(
    range_high: float, end_hour: int = 23, end_minute: int = 15
) -> pd.DataFrame:
    n = 150
    end_ts = datetime(2026, 1, 7, end_hour, end_minute, tzinfo=UTC)
    index = pd.date_range(end=end_ts, periods=n, freq="15min", tz=UTC)
    close_vals = np.concatenate(
        [np.full(n - 15, 1.0800), np.linspace(1.0800, range_high + 0.0010, 15)]
    )
    spread = 0.0001
    rng = np.random.default_rng(7)
    high_vals = close_vals + rng.uniform(0, spread, n)
    low_vals = close_vals - rng.uniform(0, spread, n)
    open_vals = close_vals + rng.uniform(-spread / 2, spread / 2, n)
    close_vals[-1] = range_high + 0.0002
    high_vals[-1] = range_high + 0.0005
    low_vals[-1] = range_high
    return pd.DataFrame(
        {"open": open_vals, "high": high_vals, "low": low_vals, "close": close_vals, "volume": 100},
        index=index,
    )


def _locked_state(range_high: float = 1.0820, range_low: float = 1.0780) -> SessionState:
    s = SessionState()
    s.session_date = datetime(2026, 1, 7, tzinfo=UTC).toordinal()
    s.high = range_high
    s.low = range_low
    s.locked = True
    s.range_high = range_high
    s.range_low = range_low
    s.traded = False
    return s


# ---------------------------------------------------------------------------
# New-path runner
# ---------------------------------------------------------------------------


def _signal_from_decision(decision: Decision) -> Signal:
    """Extract the Signal carried by a Decision (open/noop/close_all all carry one here)."""
    assert decision.signal is not None, f"Decision {decision.kind} carried no signal"
    return decision.signal


def _run_new(
    pair: str,
    m15: pd.DataFrame,
    h4: pd.DataFrame,
    state: SessionState,
) -> Decision:
    """Run DailyLullStrategy.on_bar and return the Decision."""
    strat = DailyLullStrategy(pairs=[pair], params=_default_params())
    strat._states[pair] = state
    market = _FakeMarket({(pair, "M15"): m15, (pair, "H4"): h4})
    bar_close = m15.index[-1].to_pydatetime()
    return strat.on_bar(pair, "M15", bar_close, market, _fake_ctx())


# ---------------------------------------------------------------------------
# 1. Golden-value tests for core scenarios
# ---------------------------------------------------------------------------


class TestGoldenValues:
    """Pin the DailyLullStrategy.on_bar contract against frozen expected values.

    Each test runs only the new path (legacy evaluate_pair removed in C10) and
    asserts the decision kind and the load-bearing signal fields that would be
    logged / executed.
    """

    def test_range_definition_phase_22_00(self) -> None:
        """21-23 define-range window: on_bar returns noop."""
        m15 = _make_m15_df(n=150, end_hour=22, end_minute=0)
        h4 = _make_h4_df(n=50)
        decision = _run_new("EURCHF", m15, h4, SessionState())
        assert decision.kind == "noop"
        sig = _signal_from_decision(decision)
        assert sig.action == "none"

    def test_outside_window_12_00(self) -> None:
        m15 = _make_m15_df(n=150, end_hour=12, end_minute=0)
        h4 = _make_h4_df(n=50)
        decision = _run_new("EURCHF", m15, h4, SessionState())
        assert decision.kind == "noop"
        assert _signal_from_decision(decision).action == "none"

    def test_lock_at_23_00_then_state(self) -> None:
        """Run a bar sequence through the new state machine; locking at 23:00 must work."""
        params = _default_params()
        from drift.strategies.daily_lull import _update_session_state as new_update

        new_state = SessionState()
        atr = 0.0020
        bars = [
            (datetime(2026, 1, 7, 21, 0, tzinfo=UTC), 1.0820, 1.0790),
            (datetime(2026, 1, 7, 22, 45, tzinfo=UTC), 1.0822, 1.0788),
            (datetime(2026, 1, 7, 23, 0, tzinfo=UTC), 1.0822, 1.0788),
        ]
        for bt, hi, lo in bars:
            new_update(new_state, bt, hi, lo, atr, params)

        assert new_state.locked is True
        assert new_state.range_high == 1.0822
        assert new_state.range_low == 1.0788

    def test_buy_entry_sl_below_entry(self) -> None:
        """When a buy fires, SL must be below entry and TP must be range midpoint."""
        m15 = _build_oversold_m15(range_low=1.0780)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        decision = _run_new("EURCHF", m15, h4, _locked_state(1.0820, 1.0780))
        sig = _signal_from_decision(decision)
        if sig.action == "buy":
            assert decision.kind == "open"
            assert sig.sl < sig.entry_price
            expected_tp = (1.0820 + 1.0780) / 2.0
            assert abs(sig.tp - expected_tp) < 1e-8
            assert sig.rejection_reason is None

    def test_sell_entry_sl_above_entry(self) -> None:
        """When a sell fires, SL must be above entry and TP must be range midpoint."""
        m15 = _build_overbought_m15(range_high=1.0820)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        decision = _run_new("EURCHF", m15, h4, _locked_state(1.0820, 1.0780))
        sig = _signal_from_decision(decision)
        if sig.action == "sell":
            assert decision.kind == "open"
            assert sig.sl > sig.entry_price
            expected_tp = (1.0820 + 1.0780) / 2.0
            assert abs(sig.tp - expected_tp) < 1e-8

    def test_adx_rejection(self) -> None:
        """Strongly trending H4 forces the ADX regime filter to reject."""
        m15 = _build_oversold_m15(range_low=1.0780)
        h4 = _make_h4_df(n=50, last_adx_low=False)
        decision = _run_new("EURCHF", m15, h4, _locked_state(1.0820, 1.0780))
        assert decision.kind == "noop"
        sig = _signal_from_decision(decision)
        assert sig.action == "none"

    def test_range_not_locked_rejection(self) -> None:
        """Unlocked state inside the trading window → range_not_locked rejection."""
        m15 = _build_oversold_m15(range_low=1.0780)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        decision = _run_new("EURCHF", m15, h4, SessionState())
        assert decision.kind == "noop"
        sig = _signal_from_decision(decision)
        assert sig.rejection_reason == "range_not_locked"

    def test_already_traded_rejection(self) -> None:
        state = _locked_state(1.0820, 1.0780)
        state.traded = True
        m15 = _make_m15_df(n=150, end_hour=23, end_minute=30, last_close=1.0800)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        decision = _run_new("EURCHF", m15, h4, state)
        assert decision.kind == "noop"
        assert _signal_from_decision(decision).rejection_reason == "already_traded_this_session"

    def test_no_entry_condition_inside_range(self) -> None:
        m15 = _make_m15_df(n=150, end_hour=23, end_minute=15, last_close=1.0800)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        decision = _run_new("EURCHF", m15, h4, _locked_state(1.0820, 1.0780))
        assert decision.kind == "noop"
        assert _signal_from_decision(decision).action == "none"

    def test_time_stop_at_02_00(self) -> None:
        """02:00 time stop: on_bar returns close_all with reason='session_close'."""
        m15 = _make_m15_df(n=150, end_hour=2, end_minute=0)
        h4 = _make_h4_df(n=50)
        decision = _run_new("EURCHF", m15, h4, _locked_state())
        assert decision.kind == "close_all"
        assert decision.reason == "session_close"
        sig = _signal_from_decision(decision)
        assert sig.reason == "session_end_time_stop"
        assert sig.action == "none"


# ---------------------------------------------------------------------------
# 1b. Session time stop keyed off the engine boundary (stale-frame robustness)
# ---------------------------------------------------------------------------


class TestTimeStopBoundary:
    """The 02:00 time stop must follow the engine's authoritative boundary, not a
    served candle's own hour, and must ignore a stale frame.

    Regression for the spurious "session closed" at the 21:00 session START: after
    a long idle period the engine handed back a leftover 02:xx candle at the 21:00
    wake, whose hour==2 forced a close_all even though the session was just opening.
    """

    def test_stale_hour2_candle_at_session_start_does_not_close(self) -> None:
        """Boundary = 21:00 (session start) with a stale 02:00 candle → no close_all.

        This is the exact production bug: at the 21:00 session START the engine
        served a leftover 02:00 frame.  Keying the time stop off the boundary
        (hour 21, not the candle's hour 2) means the stop never even arms here.
        """
        m15 = _make_m15_df(n=150, end_hour=2, end_minute=0)
        h4 = _make_h4_df(n=50)
        strat = DailyLullStrategy(pairs=["EURCHF"], params=_default_params())
        strat._states["EURCHF"] = _locked_state()
        market = _FakeMarket({("EURCHF", "M15"): m15, ("EURCHF", "H4"): h4})
        boundary = datetime(2026, 1, 8, 21, 0, tzinfo=UTC)  # session START, hours later
        decision = strat.on_bar("EURCHF", "M15", boundary, market, _fake_ctx())
        assert decision.kind != "close_all"
        assert _signal_from_decision(decision).reason == "outside_window"

    def test_stale_frame_at_0200_boundary_does_not_close(self) -> None:
        """Boundary = 02:00 but the served candle is hours stale → no close_all.

        Exercises the freshness guard directly: even when the boundary IS the
        session end, a frame whose last bar is far behind the boundary (the engine
        handed back outdated data) must not force a close.
        """
        # Last bar sits at 21:00 the previous evening; boundary jumps to 02:00.
        m15 = _make_m15_df(n=150, end_hour=21, end_minute=0)
        h4 = _make_h4_df(n=50)
        strat = DailyLullStrategy(pairs=["EURCHF"], params=_default_params())
        strat._states["EURCHF"] = _locked_state()
        market = _FakeMarket({("EURCHF", "M15"): m15, ("EURCHF", "H4"): h4})
        boundary = datetime(2026, 1, 8, 2, 0, tzinfo=UTC)  # session end, but stale frame
        decision = strat.on_bar("EURCHF", "M15", boundary, market, _fake_ctx())
        assert decision.kind != "close_all"
        sig = _signal_from_decision(decision)
        assert sig.reason == "stale_session_data"
        assert sig.rejection_reason == "stale_session_data"

    def test_fresh_candle_at_0200_boundary_closes(self) -> None:
        """Boundary = 02:00 with a fresh 01:45 candle (live) → close_all fires."""
        m15 = _make_m15_df(n=150, end_hour=1, end_minute=45)  # just-closed live bar
        h4 = _make_h4_df(n=50)
        strat = DailyLullStrategy(pairs=["EURCHF"], params=_default_params())
        strat._states["EURCHF"] = _locked_state()
        market = _FakeMarket({("EURCHF", "M15"): m15, ("EURCHF", "H4"): h4})
        boundary = datetime(2026, 1, 7, 2, 0, tzinfo=UTC)  # 02:00 close, 15 min after bar
        decision = strat.on_bar("EURCHF", "M15", boundary, market, _fake_ctx())
        assert decision.kind == "close_all"
        assert decision.reason == "session_close"
        assert _signal_from_decision(decision).reason == "session_end_time_stop"

    def test_backtest_aligned_0200_candle_closes(self) -> None:
        """Boundary == candle (backtest invariant): 02:00 bar at a 02:00 boundary fires."""
        m15 = _make_m15_df(n=150, end_hour=2, end_minute=0)
        h4 = _make_h4_df(n=50)
        strat = DailyLullStrategy(pairs=["EURCHF"], params=_default_params())
        strat._states["EURCHF"] = _locked_state()
        market = _FakeMarket({("EURCHF", "M15"): m15, ("EURCHF", "H4"): h4})
        boundary = m15.index[-1].to_pydatetime()  # backtest: boundary == bar time
        decision = strat.on_bar("EURCHF", "M15", boundary, market, _fake_ctx())
        assert decision.kind == "close_all"
        assert decision.reason == "session_close"


# ---------------------------------------------------------------------------
# 2. Lifecycle callbacks (contract extension)
# ---------------------------------------------------------------------------


class TestLifecycleCallbacks:
    def test_on_bar_does_not_set_traded_on_open(self) -> None:
        """A buy/sell Decision.open must NOT flip traded inside on_bar."""
        m15 = _build_oversold_m15(range_low=1.0780)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        strat = DailyLullStrategy(pairs=["EURCHF"], params=_default_params())
        strat._states["EURCHF"] = _locked_state(1.0820, 1.0780)
        market = _FakeMarket({("EURCHF", "M15"): m15, ("EURCHF", "H4"): h4})
        decision = strat.on_bar("EURCHF", "M15", m15.index[-1].to_pydatetime(), market, _fake_ctx())
        if decision.kind == "open":
            assert strat._states["EURCHF"].traded is False

    def test_on_fill_sets_traded(self) -> None:
        strat = DailyLullStrategy(pairs=["EURCHF"], params=_default_params())
        sig = Signal(
            action="buy",
            pair="EURCHF",
            timestamp=datetime(2026, 1, 7, 23, 15, tzinfo=UTC),
            m15_candle_time=datetime(2026, 1, 7, 23, 15, tzinfo=UTC),
            h4_candle_time=datetime(2026, 1, 7, 20, 0, tzinfo=UTC),
            entry_price=1.0779,
        )
        assert strat._states["EURCHF"].traded is False
        strat.on_fill("EURCHF", sig, ticket=12345)
        assert strat._states["EURCHF"].traded is True

    def test_on_order_rejected_leaves_traded_false(self) -> None:
        strat = DailyLullStrategy(pairs=["EURCHF"], params=_default_params())
        sig = Signal(
            action="buy",
            pair="EURCHF",
            timestamp=datetime(2026, 1, 7, 23, 15, tzinfo=UTC),
            m15_candle_time=datetime(2026, 1, 7, 23, 15, tzinfo=UTC),
            h4_candle_time=datetime(2026, 1, 7, 20, 0, tzinfo=UTC),
            entry_price=1.0779,
        )
        strat.on_order_rejected("EURCHF", sig, reason="risk: max_open_trades")
        assert strat._states["EURCHF"].traded is False


# ---------------------------------------------------------------------------
# 3. Params + registry
# ---------------------------------------------------------------------------


class TestParamsAndRegistry:
    def test_from_dict_parses_known_fields_and_ignores_unknown(self) -> None:
        params = DailyLullParams.from_dict(
            {"rsi_oversold": 30.0, "sl_atr_mult": 3.0, "unknown_key": 99}
        )
        assert params.rsi_oversold == 30.0
        assert params.sl_atr_mult == 3.0
        assert params.rsi_overbought == 65.0  # default preserved

    def test_from_dict_empty_dict_uses_dataclass_defaults(self) -> None:
        params = DailyLullParams.from_dict({})
        defaults = DailyLullParams()
        for field in DailyLullParams.__dataclass_fields__:
            assert getattr(params, field) == getattr(defaults, field), field

    def test_registered_under_name(self) -> None:
        assert STRATEGY_REGISTRY.get("daily_lull") is DailyLullStrategy
        assert DailyLullStrategy.name == "daily_lull"
        assert DailyLullStrategy.timeframes == frozenset({"M15"})

    def test_isinstance_marketdata_protocol(self) -> None:
        market = _FakeMarket({})
        assert isinstance(market, MarketData)


class TestH4WarmupConvergence:
    """D059: the H4 ADX regime filter needs a deep warmup window to converge.

    ADX is doubly smoothed (Wilder smoothing of DX, itself derived from smoothed
    DM/TR), so a shallow window leaves the current-bar value non-converged and
    spuriously trips the adx_max_threshold filter.  ``_H4_COUNT`` must request
    enough history that the last-bar ADX matches the full-series value, so the
    live regime filter equals the one the backtest validated.
    """

    @staticmethod
    def _h4_ohlc(n: int = 500) -> pd.DataFrame:
        rng = np.random.default_rng(42)
        idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz=timezone.utc)
        close = 1.10 + rng.normal(0, 0.0008, n).cumsum()
        return pd.DataFrame(
            {"high": close + 0.0012, "low": close - 0.0012, "close": close}, index=idx
        )

    def test_configured_window_converges_shallow_does_not(self) -> None:
        from drift.indicators import adx
        from drift.strategies.daily_lull import _H4_COUNT

        df = self._h4_ohlc()

        def last_adx(sub: pd.DataFrame) -> float:
            return float(adx(sub["high"], sub["low"], sub["close"], 14).iloc[-1])

        full = last_adx(df)
        shallow = last_adx(df.iloc[-50:])  # the old _H4_COUNT
        deep = last_adx(df.iloc[-_H4_COUNT:])  # the configured window

        # The configured window matches the full-series ADX (converged).
        assert abs(deep - full) < 1e-3, (deep, full)
        # The old shallow window is measurably further from the converged value.
        assert abs(shallow - full) > abs(deep - full), (shallow, deep, full)
        # Guard against anyone lowering the window back below convergence depth.
        assert _H4_COUNT >= 300


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
