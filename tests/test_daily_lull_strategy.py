"""Equivalence tests for the ported Daily Lull strategy (Step 29).

The port in ``drift/strategies/daily_lull.py`` must produce the SAME signals as
the legacy ``drift.strategy.evaluate_pair`` it replaces.  Each test runs BOTH
paths over identical synthetic data and asserts the resulting action, SL, TP,
range, indicator values and reason/rejection_reason match.

The legacy path mutates ``SessionState.traded`` to True on a buy/sell (later
rolled back by main.py on rejection); the new path moves that flip to
``on_fill``.  The equivalence assertions therefore compare the SIGNAL fields,
not the post-call ``traded`` flag — which is covered by dedicated lifecycle
tests at the bottom.

No MT5 calls are made here — all tests use synthetic DataFrames built the same
way as ``tests/test_strategy.py``.  All datetimes are UTC-aware (server-time
clock, reasoned over as-is).
"""

from __future__ import annotations

import math
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

from drift.config import StrategyConfig  # noqa: E402
from drift.strategies.base import STRATEGY_REGISTRY, Decision, MarketData, Signal  # noqa: E402
from drift.strategies.daily_lull import (  # noqa: E402
    DailyLullParams,
    DailyLullStrategy,
)
from drift.strategies.daily_lull import (  # noqa: E402
    SessionState as NewSessionState,
)
from drift.strategy import (  # noqa: E402
    SessionState as OldSessionState,
)
from drift.strategy import (  # noqa: E402
    evaluate_pair,
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


def _default_config() -> StrategyConfig:
    return StrategyConfig(
        session_start_hour=21,
        session_end_hour=2,
        range_atr_min=1.0,
        range_atr_max=4.0,
        sl_atr_mult=2.5,
        rsi_oversold=35.0,
        rsi_overbought=65.0,
        adx_max_threshold=35.0,
        m15_rsi_period=14,
        m15_atr_period=14,
        h4_adx_period=14,
    )


def _default_params() -> DailyLullParams:
    """DailyLullParams matching _default_config (so both paths use same params)."""
    cfg = _default_config()
    return DailyLullParams(**{f: getattr(cfg, f) for f in DailyLullParams.__dataclass_fields__})


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


def _locked_old(range_high: float = 1.0820, range_low: float = 1.0780) -> OldSessionState:
    s = OldSessionState()
    s.session_date = datetime(2026, 1, 7, tzinfo=UTC).toordinal()
    s.high = range_high
    s.low = range_low
    s.locked = True
    s.range_high = range_high
    s.range_low = range_low
    s.traded = False
    return s


def _locked_new(range_high: float = 1.0820, range_low: float = 1.0780) -> NewSessionState:
    s = NewSessionState()
    s.session_date = datetime(2026, 1, 7, tzinfo=UTC).toordinal()
    s.high = range_high
    s.low = range_low
    s.locked = True
    s.range_high = range_high
    s.range_low = range_low
    s.traded = False
    return s


# ---------------------------------------------------------------------------
# Equivalence harness
# ---------------------------------------------------------------------------


def _signal_from_decision(decision: Decision) -> Signal:
    """Extract the Signal carried by a Decision (open/noop/close_all all carry one here)."""
    assert decision.signal is not None, f"Decision {decision.kind} carried no signal"
    return decision.signal


def _assert_signals_equivalent(old: Signal, new: Signal) -> None:
    """Assert the load-bearing fields of two signals match."""
    assert old.action == new.action, f"action: {old.action!r} != {new.action!r}"
    assert old.reason == new.reason, f"reason: {old.reason!r} != {new.reason!r}"
    assert old.rejection_reason == new.rejection_reason, (
        f"rejection_reason: {old.rejection_reason!r} != {new.rejection_reason!r}"
    )

    def _eq(a: float, b: float, name: str) -> None:
        if math.isnan(a) and math.isnan(b):
            return
        assert abs(a - b) < 1e-9, f"{name}: {a!r} != {b!r}"

    _eq(old.entry_price, new.entry_price, "entry_price")
    _eq(old.sl, new.sl, "sl")
    _eq(old.tp, new.tp, "tp")
    _eq(old.range_high, new.range_high, "range_high")
    _eq(old.range_low, new.range_low, "range_low")
    _eq(old.range_atr_ratio, new.range_atr_ratio, "range_atr_ratio")
    _eq(old.rsi, new.rsi, "rsi")
    _eq(old.atr_value, new.atr_value, "atr_value")
    _eq(old.h4_adx, new.h4_adx, "h4_adx")
    assert old.timestamp == new.timestamp, f"timestamp: {old.timestamp} != {new.timestamp}"
    assert old.h4_candle_time == new.h4_candle_time


def _run_both(
    pair: str,
    m15: pd.DataFrame,
    h4: pd.DataFrame,
    old_state: OldSessionState,
    new_state: NewSessionState,
) -> tuple[Signal, Decision]:
    """Run legacy evaluate_pair and new on_bar over the same data.

    Returns (old_signal, new_decision).
    """
    config = _default_config()
    old_sig = evaluate_pair(pair, m15, h4, old_state, config)

    strat = DailyLullStrategy(pairs=[pair], params=_default_params())
    strat._states[pair] = new_state
    market = _FakeMarket({(pair, "M15"): m15, (pair, "H4"): h4})
    bar_close = m15.index[-1].to_pydatetime()
    decision = strat.on_bar(pair, "M15", bar_close, market, _fake_ctx())
    return old_sig, decision


# ---------------------------------------------------------------------------
# 1. Equivalence across the full set of scenarios
# ---------------------------------------------------------------------------


class TestEquivalence:
    def test_range_definition_phase_22_00(self) -> None:
        """21-23 define-range window: both paths return a 'none'/noop signal."""
        m15 = _make_m15_df(n=150, end_hour=22, end_minute=0)
        h4 = _make_h4_df(n=50)
        old_sig, decision = _run_both("EURCHF", m15, h4, OldSessionState(), NewSessionState())
        assert decision.kind == "noop"
        _assert_signals_equivalent(old_sig, _signal_from_decision(decision))

    def test_outside_window_12_00(self) -> None:
        m15 = _make_m15_df(n=150, end_hour=12, end_minute=0)
        h4 = _make_h4_df(n=50)
        old_sig, decision = _run_both("EURCHF", m15, h4, OldSessionState(), NewSessionState())
        assert decision.kind == "noop"
        _assert_signals_equivalent(old_sig, _signal_from_decision(decision))

    def test_lock_at_23_00_then_equivalence(self) -> None:
        """Run the SAME bar sequence (21:00, 22:45, 23:00) through both state machines.

        Locking at 23:00 must produce identical range_high/range_low and an
        equivalent signal on the final bar.
        """
        config = _default_config()
        params = _default_params()
        from drift.strategies.daily_lull import _update_session_state as new_update
        from drift.strategy import update_session_state as old_update

        old_state = OldSessionState()
        new_state = NewSessionState()
        atr = 0.0020
        bars = [
            (datetime(2026, 1, 7, 21, 0, tzinfo=UTC), 1.0820, 1.0790),
            (datetime(2026, 1, 7, 22, 45, tzinfo=UTC), 1.0822, 1.0788),
            (datetime(2026, 1, 7, 23, 0, tzinfo=UTC), 1.0822, 1.0788),
        ]
        for bt, hi, lo in bars:
            old_update(old_state, bt, hi, lo, atr, config)
            new_update(new_state, bt, hi, lo, atr, params)

        assert old_state.locked == new_state.locked is True
        assert old_state.range_high == new_state.range_high
        assert old_state.range_low == new_state.range_low

    def test_buy_entry(self) -> None:
        m15 = _build_oversold_m15(range_low=1.0780)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        old_sig, decision = _run_both(
            "EURCHF", m15, h4, _locked_old(1.0820, 1.0780), _locked_new(1.0820, 1.0780)
        )
        new_sig = _signal_from_decision(decision)
        _assert_signals_equivalent(old_sig, new_sig)
        if old_sig.action == "buy":
            assert decision.kind == "open"
            assert new_sig.sl < new_sig.entry_price

    def test_sell_entry(self) -> None:
        m15 = _build_overbought_m15(range_high=1.0820)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        old_sig, decision = _run_both(
            "EURCHF", m15, h4, _locked_old(1.0820, 1.0780), _locked_new(1.0820, 1.0780)
        )
        new_sig = _signal_from_decision(decision)
        _assert_signals_equivalent(old_sig, new_sig)
        if old_sig.action == "sell":
            assert decision.kind == "open"
            assert new_sig.sl > new_sig.entry_price

    def test_adx_rejection(self) -> None:
        """Strongly trending H4 forces the ADX regime filter to reject (or a
        consistent 'none' from both paths)."""
        m15 = _build_oversold_m15(range_low=1.0780)
        h4 = _make_h4_df(n=50, last_adx_low=False)
        old_sig, decision = _run_both(
            "EURCHF", m15, h4, _locked_old(1.0820, 1.0780), _locked_new(1.0820, 1.0780)
        )
        assert decision.kind == "noop"
        _assert_signals_equivalent(old_sig, _signal_from_decision(decision))

    def test_invalid_range_not_locked(self) -> None:
        """Unlocked state inside the trading window → range_not_locked rejection in both."""
        m15 = _build_oversold_m15(range_low=1.0780)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        old_sig, decision = _run_both("EURCHF", m15, h4, OldSessionState(), NewSessionState())
        assert decision.kind == "noop"
        _assert_signals_equivalent(old_sig, _signal_from_decision(decision))
        assert old_sig.rejection_reason == "range_not_locked"

    def test_already_traded(self) -> None:
        old_state = _locked_old(1.0820, 1.0780)
        old_state.traded = True
        new_state = _locked_new(1.0820, 1.0780)
        new_state.traded = True
        m15 = _make_m15_df(n=150, end_hour=23, end_minute=30, last_close=1.0800)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        old_sig, decision = _run_both("EURCHF", m15, h4, old_state, new_state)
        assert decision.kind == "noop"
        _assert_signals_equivalent(old_sig, _signal_from_decision(decision))
        assert old_sig.rejection_reason == "already_traded_this_session"

    def test_no_entry_condition_inside_range(self) -> None:
        m15 = _make_m15_df(n=150, end_hour=23, end_minute=15, last_close=1.0800)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        old_sig, decision = _run_both(
            "EURCHF", m15, h4, _locked_old(1.0820, 1.0780), _locked_new(1.0820, 1.0780)
        )
        assert decision.kind == "noop"
        _assert_signals_equivalent(old_sig, _signal_from_decision(decision))

    def test_time_stop_at_02_00(self) -> None:
        """02:00 time stop: legacy returns a session_end_time_stop signal; the
        new path returns close_all carrying the same signal."""
        m15 = _make_m15_df(n=150, end_hour=2, end_minute=0)
        h4 = _make_h4_df(n=50)
        old_sig, decision = _run_both("EURCHF", m15, h4, _locked_old(), _locked_new())
        assert decision.kind == "close_all"
        assert decision.reason == "session_close"
        new_sig = _signal_from_decision(decision)
        assert old_sig.reason == "session_end_time_stop"
        _assert_signals_equivalent(old_sig, new_sig)


# ---------------------------------------------------------------------------
# 2. Lifecycle callbacks (contract extension)
# ---------------------------------------------------------------------------


class TestLifecycleCallbacks:
    def test_on_bar_does_not_set_traded_on_open(self) -> None:
        """A buy/sell Decision.open must NOT flip traded inside on_bar."""
        m15 = _build_oversold_m15(range_low=1.0780)
        h4 = _make_h4_df(n=50, last_adx_low=True)
        strat = DailyLullStrategy(pairs=["EURCHF"], params=_default_params())
        strat._states["EURCHF"] = _locked_new(1.0820, 1.0780)
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

    def test_from_dict_defaults_match_strategy_config(self) -> None:
        cfg = StrategyConfig()
        params = DailyLullParams.from_dict({})
        for field in DailyLullParams.__dataclass_fields__:
            assert getattr(params, field) == getattr(cfg, field), field

    def test_registered_under_name(self) -> None:
        assert STRATEGY_REGISTRY.get("daily_lull") is DailyLullStrategy
        assert DailyLullStrategy.name == "daily_lull"
        assert DailyLullStrategy.timeframes == frozenset({"M15"})

    def test_isinstance_marketdata_protocol(self) -> None:
        market = _FakeMarket({})
        assert isinstance(market, MarketData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
