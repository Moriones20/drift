"""Own backtest engine that consumes the LIVE ``Strategy.on_bar`` (D055).

This is the unified backtest adapter mandated by D055: instead of a second,
hand-ported copy of the strategy logic (``backtest/lull_engine.py``, built on
Backtesting.py), this engine runs the *very same* ``on_bar`` that the live
engine calls.  A strategy's signal logic therefore has a single source of
truth; the backtest only supplies a fake :class:`MarketData` /
:class:`StrategyContext` and a fill model.

Fill model — replicated EXACTLY from ``backtest/lull_engine.py`` so the two
produce equivalent results (guarded by ``tests/test_backtest_engine.py``):

- **Decisions come from ``on_bar``** (entry signal with its SL and TP=midpoint,
  and the 02:00 ``close_all`` time stop).  The *fill mechanics* (how a decision
  becomes a trade) are the engine's job, matching Backtesting.py as used by
  ``lull_engine``:

  * Market entry (``self.buy``/``self.sell`` with no ``tp``) fills at the
    **next bar's open** (Backtesting.py processes queued orders at the start of
    the following bar).
  * The strategy attaches an SL only; the take-profit (range midpoint) is
    realised by ``lull_engine`` as a manual ``position.close()`` when the bar
    close crosses the midpoint — which also fills at the **next bar's open**.
    The engine reproduces that midpoint-close-on-next-open exit using the
    ``signal.tp`` level, NOT as an intrabar broker TP (that would be the live
    executor's model and would diverge — see the equivalence test docstring).
  * The SL is a broker stop checked **intrabar** against high/low; on the entry
    bar too (Backtesting.py reprocesses the freshly attached SL in the same
    bar).  A gap through the stop fills at the worse of open/stop.
  * The 02:00 time stop (``close_all``) fills at the **next bar's open**.

- **Commission** is ``abs(size) * price * commission`` charged at BOTH entry and
  exit (Backtesting.py's relative commission), where ``size`` is the integer
  number of units bought all-in with available cash.

- **Equity** is recorded per bar as ``cash + open_position_floating_pl`` using
  the bar close, mirroring Backtesting.py's ``_Broker.next`` equity log.

Metrics (profit factor, win rate, max drawdown, Sharpe, return, trade count)
are computed in :mod:`backtest._results` from the trade list and equity curve,
using the same formulas Backtesting.py's ``compute_stats`` applies.

Time is treated exactly as ``lull_engine`` does: the DataFrame index is read as
MT5 server time directly (D041); no timezone conversion is performed.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest._results import compute_metrics
from drift.indicators import adx as _adx
from drift.indicators import atr as _atr
from drift.indicators import rsi as _rsi
from drift.strategies.base import MarketData, Signal, StrategyContext
from drift.strategies.daily_lull import DailyLullParams, DailyLullStrategy

# Backtesting.py's ``_FULL_EQUITY`` sentinel: ``self.buy()`` with no size uses a
# fraction one epsilon below 1.0 of available equity.  We replicate the same
# all-in sizing so the integer unit count (and thus equity) matches.
_FULL_EQUITY = 1 - sys.float_info.epsilon

# Per-timeframe warm-up depth served to ``on_bar``.  The H4 ADX is doubly
# smoothed (DI then DX), so it needs several hundred bars to converge to the
# full-series value ``lull_engine`` uses; at 320 bars it matches to ~1e-6 (and
# the entry decisions are bit-exact, since 1e-6 never flips the ADX<35 gate).
# M15 RSI/ATR converge well within ~150 bars; 250 leaves margin.  Bounded
# windows keep the per-bar cost constant, so the loop is O(n) not O(n^2).
DEFAULT_WARMUP_BARS: dict[str, int] = {"H4": 320, "M15": 250}


# ---------------------------------------------------------------------------
# Fake MarketData / StrategyContext served to on_bar
# ---------------------------------------------------------------------------


class _HistoricalMarketData(MarketData):
    """Serve historical candles already CLOSED at the current bar (D045/D055).

    Holds the full raw OHLC frames (lowercase columns, tz-aware server-time
    index) for one pair across timeframes.  ``set_now`` advances the cursor to
    the close time of the M15 bar being evaluated; ``candles`` then returns only
    bars whose index is at or before that instant — never the still-forming one.
    """

    def __init__(
        self,
        pair: str,
        frames: dict[str, pd.DataFrame],
        warmup_bars: dict[str, int] | None = None,
    ) -> None:
        self._pair = pair
        # Pre-sort once; the slicing and positional cursor below rely on a
        # monotonic index.
        self._frames = {tf: df.sort_index() for tf, df in frames.items()}
        # Precompute the integer positions for fast "bars at or before now"
        # slicing without an O(n) boolean mask on every call.
        self._index = {tf: df.index for tf, df in self._frames.items()}
        self._now: pd.Timestamp | None = None
        # Minimum bars to serve per timeframe, regardless of the strategy's own
        # ``count``.  A doubly-smoothed EWM indicator (the H4 ADX) needs a deep
        # warm-up to converge to ``lull_engine``'s full-series value; serving a
        # bounded-but-large window keeps the per-bar cost constant (linear loop)
        # while reproducing lull_engine to floating-point precision.  See D059.
        self._warmup_bars = warmup_bars or {}

    def set_now(self, now: pd.Timestamp) -> None:
        self._now = now

    def candles(self, pair: str, timeframe: str, count: int) -> pd.DataFrame:
        if pair != self._pair:
            raise KeyError(f"engine serves only {self._pair!r}, asked for {pair!r}")
        df = self._frames.get(timeframe)
        if df is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        idx = self._index[timeframe]
        if self._now is None:
            upper = len(df)
        else:
            # Number of bars with index <= now (idx is sorted ascending).
            upper = int(idx.searchsorted(self._now, side="right"))
        want = max(int(count or 0), int(self._warmup_bars.get(timeframe, 0)))
        lower = max(0, upper - want) if want else 0
        return df.iloc[lower:upper]


# ---------------------------------------------------------------------------
# Trade / position records
# ---------------------------------------------------------------------------


@dataclass
class BacktestTrade:
    """One closed trade, with the fields the metrics and equivalence test need."""

    pair: str
    direction: str  # "buy" | "sell"
    size: int  # units (positive long, negative short) as Backtesting.py sizes it
    entry_time: datetime
    exit_time: datetime
    entry_price: float  # fill price (next-bar open), no spread (matches lull_engine)
    exit_price: float
    reason: str  # "tp" | "sl" | "time_stop"
    commission: float  # total entry + exit commission in cash
    pnl: float  # net cash P&L (after commission)
    return_pct: float  # net fractional return on the trade (matches Trade.pl_pct)


@dataclass
class _OpenPosition:
    direction: str
    size: int
    entry_time: pd.Timestamp
    entry_bar: int
    entry_price: float
    sl: float
    tp: float  # range midpoint exit level


@dataclass
class _PendingMarket:
    """A market order queued during a bar, filled at the next bar's open."""

    kind: str  # "open" | "close"
    signal: Signal | None = None  # for "open"
    reason: str = ""  # for "close"


# ---------------------------------------------------------------------------
# Strategy context construction
# ---------------------------------------------------------------------------


def _make_context(
    cash: float,
    position: _OpenPosition | None,
    last_close: float,
) -> StrategyContext:
    """Build the per-tick context for ``on_bar``.

    The Daily Lull only reads ``open_positions`` (to know whether it already
    holds one) indirectly through its own ``traded`` flag; it does not size or
    check drawdown (D053).  Equity is informational.  ``paused`` is always
    False in backtest.
    """
    open_positions: list[dict] = []
    floating = 0.0
    if position is not None:
        floating = position.size * (last_close - position.entry_price)
        open_positions.append(
            {
                "ticket": position.entry_bar,
                "direction": position.direction,
                "entry_price": position.entry_price,
            }
        )
    equity = cash + floating
    return StrategyContext(
        account_balance=cash,
        account_equity=equity,
        allocated_capital=cash,
        open_positions=open_positions,
        paused=False,
    )


# ---------------------------------------------------------------------------
# Sizing / commission (replicates Backtesting.py)
# ---------------------------------------------------------------------------


def _solve_size(cash: float, price: float, commission: float, is_long: bool) -> int:
    """Integer all-in unit count, matching Backtesting.py's relative-size fill.

    Backtesting.py computes ``size = int((margin * leverage * fraction) //
    adjusted_price_plus_commission)`` with ``margin=leverage=1``,
    ``fraction=_FULL_EQUITY``, ``adjusted_price = price`` (spread 0) and a
    per-unit commission of ``price * commission``.  Returns a signed integer
    (negative for shorts).
    """
    adjusted_price_plus_commission = price + price * commission
    units = int((cash * _FULL_EQUITY) // adjusted_price_plus_commission)
    return units if is_long else -units


def _commission(size: int, price: float, commission: float) -> float:
    """Relative commission in cash for ``abs(size)`` units at ``price``."""
    return abs(size) * price * commission


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------


@dataclass
class BacktestResult:
    """Result of a single-pair backtest: trades, equity curve and metrics."""

    pair: str
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    metrics: dict = field(default_factory=dict)


def _first_evaluable_index(strategy: DailyLullStrategy, m15: pd.DataFrame, h4: pd.DataFrame) -> int:
    """Return the first M15 positional index whose indicators are all valid.

    ``lull_engine`` drops the warm-up rows where RSI/ATR/H4-ADX are NaN before
    running, so its first evaluated bar is the first fully-warmed one.  We mirror
    that so both engines evaluate the same set of bars.  Computed once over the
    full series with the strategy's own indicator helpers (same module the live
    ``on_bar`` uses), then forward-filled H4 ADX onto the M15 grid exactly like
    ``daily_lull._evaluate`` / ``lull_engine.prepare_lull_data``.
    """
    p = strategy.params
    rsi_series = _rsi(m15["close"], p.m15_rsi_period)
    atr_series = _atr(m15["high"], m15["low"], m15["close"], p.m15_atr_period)
    h4_adx = _adx(h4["high"], h4["low"], h4["close"], p.h4_adx_period).shift(1)
    h4_on_m15 = h4_adx.resample("15min").last().ffill().reindex(m15.index, method="ffill")

    valid = (
        (~rsi_series.isna()) & (~atr_series.isna()) & (atr_series > 0) & (~h4_on_m15.isna())
    ).to_numpy()
    return int(np.argmax(valid)) if valid.any() else len(m15)


def run_backtest(
    strategy: DailyLullStrategy,
    m15: pd.DataFrame,
    h4: pd.DataFrame,
    pair: str,
    cash: float = 500.0,
    commission: float = 0.00007,
    warmup_bars: dict[str, int] | None = None,
) -> BacktestResult:
    """Run the Daily Lull backtest for one pair by driving the live ``on_bar``.

    Parameters
    ----------
    strategy:
        A :class:`~drift.strategies.daily_lull.DailyLullStrategy` instance.  Its
        per-pair state is reset for ``pair`` before the run.
    m15, h4:
        Raw OHLC frames with lowercase columns and a tz-aware DatetimeIndex in
        MT5 server time (as loaded by ``download_data.load_lull_data``).
    pair:
        The symbol to backtest (must be in ``strategy.pairs`` or it is added).
    cash:
        Starting capital.
    commission:
        Relative commission per side (default 0.00007, matching ``lull_engine``).
    warmup_bars:
        Minimum bars to serve ``on_bar`` per timeframe.  Defaults to
        :data:`DEFAULT_WARMUP_BARS` (H4 600, M15 1500) — deep enough for the
        doubly-smoothed H4 ADX and the M15 RSI/ATR to converge to
        ``lull_engine``'s full-series values (exact D055 equivalence) while
        keeping the loop cost constant per bar.  Pass ``{"H4": 50, "M15": 150}``
        to instead reproduce the live bot's bounded-lookback behaviour.
    """
    m15 = _normalise(m15)
    h4 = _normalise(h4)

    # Fresh per-pair state so repeated runs are deterministic.
    strategy._states[pair] = type(strategy._state(pair))()
    if pair not in strategy.pairs:
        strategy.pairs.append(pair)

    market = _HistoricalMarketData(
        pair, {"M15": m15, "H4": h4}, warmup_bars=warmup_bars or DEFAULT_WARMUP_BARS
    )

    start = _first_evaluable_index(strategy, m15, h4)

    index = m15.index
    opens = m15["open"].to_numpy()
    highs = m15["high"].to_numpy()
    lows = m15["low"].to_numpy()
    closes = m15["close"].to_numpy()
    hours = index.hour.to_numpy()
    n = len(m15)

    # Hours at which on_bar can change session state or act: the range-definition
    # window (start_hour .. start_hour + range_hours - 1), the lock/trade hours
    # 23/00/01, and the time-stop hour.  Outside these hours the Lull is always
    # flat (every position is time-stopped at 02:00) and on_bar mutates no state,
    # so we skip the (expensive) on_bar call without changing results.  Computed
    # from the strategy's own params, not hard-coded, so non-default windows work.
    p = strategy.params
    active_hours = set(range(p.session_start_hour, p.session_start_hour + p.range_definition_hours))
    active_hours |= {23, 0, 1, p.session_end_hour}
    active_hours = {h % 24 for h in active_hours}

    cash_balance = cash
    position: _OpenPosition | None = None
    pending: _PendingMarket | None = None
    trades: list[BacktestTrade] = []
    equity = [float("nan")] * n

    for i in range(n):
        bar_open = float(opens[i])
        bar_high = float(highs[i])
        bar_low = float(lows[i])
        bar_close = float(closes[i])
        bar_time = index[i]

        # --- 1. Process the order queued on the previous bar (fills at this open) ---
        if pending is not None:
            if pending.kind == "open":
                position = _fill_open(
                    pending.signal, bar_open, bar_time, i, cash_balance, commission
                )
                if position is not None:
                    cash_balance -= _commission(position.size, position.entry_price, commission)
                    strategy.on_fill(pair, pending.signal, ticket=i)
                else:
                    strategy.on_order_rejected(pair, pending.signal, "insufficient_size")
            elif pending.kind == "close" and position is not None:
                trade, cash_balance = _close_position(
                    position, bar_open, bar_time, pending.reason, cash_balance, commission, pair
                )
                trades.append(trade)
                position = None
            pending = None

        # --- 2. Intrabar SL check on any open position (entry bar included) ---
        if position is not None:
            sl_price = _sl_fill_price(position, bar_open, bar_high, bar_low)
            if sl_price is not None:
                trade, cash_balance = _close_position(
                    position, sl_price, bar_time, "sl", cash_balance, commission, pair
                )
                trades.append(trade)
                position = None

        # --- 3. Record equity for this bar (cash + floating P&L at close) ---
        floating = 0.0
        if position is not None:
            floating = position.size * (bar_close - position.entry_price)
        equity[i] = cash_balance + floating

        # --- 4. Below the warm-up start, do not evaluate the strategy ---
        if i < start or i < 1:
            continue

        # Skip on_bar for hours where the Lull provably does nothing and holds
        # no position (see active_hours above): a pure performance shortcut.
        if hours[i] not in active_hours and position is None:
            continue

        # --- 5. Drive the live on_bar for this closed M15 bar ---
        market.set_now(bar_time)
        ctx = _make_context(cash_balance, position, bar_close)
        decision = strategy.on_bar(pair, "M15", bar_time.to_pydatetime(), market, ctx)

        # --- 6. Translate the Decision into the lull_engine fill model ---
        if decision.kind == "close_all":
            # 02:00 time stop: close at next bar's open (matches position.close()).
            if position is not None:
                pending = _PendingMarket(kind="close", reason="time_stop")
            continue

        if decision.kind == "open" and position is None and pending is None:
            # Mid exit takes priority in lull_engine, but a fresh entry only
            # happens when flat — so an open decision while flat queues a market
            # order for the next open.
            pending = _PendingMarket(kind="open", signal=decision.signal)
            continue

        # noop / close while flat: check the midpoint exit for an open position.
        if position is not None:
            mid = position.tp
            crossed = (position.direction == "buy" and bar_close >= mid) or (
                position.direction == "sell" and bar_close <= mid
            )
            if crossed:
                pending = _PendingMarket(kind="close", reason="tp")

    # Span the equity curve over the same bars lull_engine does: it indexes the
    # equity array by the prepared (warm-up-dropped) frame, starting at the first
    # evaluable bar.  Trimming to ``start`` aligns the daily-return resample used
    # by the Sharpe/volatility calculation.
    eq_start = start if start < n else 0
    equity_series = pd.Series(equity[eq_start:], index=index[eq_start:]).bfill().fillna(cash)
    metrics = compute_metrics(trades, equity_series)
    return BacktestResult(pair=pair, trades=trades, equity_curve=equity_series, metrics=metrics)


# ---------------------------------------------------------------------------
# Fill helpers
# ---------------------------------------------------------------------------


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Return a frame with lowercase OHLC columns and a sorted tz-aware index."""
    out = df.copy()
    if "tick_volume" in out.columns and "volume" not in out.columns:
        out = out.rename(columns={"tick_volume": "volume"})
    out.columns = [c.lower() for c in out.columns]
    out = out.sort_index()
    return out


def _fill_open(
    signal: Signal,
    price: float,
    bar_time: pd.Timestamp,
    bar_index: int,
    cash: float,
    commission: float,
) -> _OpenPosition | None:
    """Fill a queued market entry at ``price`` (this bar's open)."""
    is_long = signal.action == "buy"
    size = _solve_size(cash, price, commission, is_long)
    if size == 0:
        return None
    return _OpenPosition(
        direction=signal.action,
        size=size,
        entry_time=bar_time,
        entry_bar=bar_index,
        entry_price=price,
        sl=float(signal.sl),
        tp=float(signal.tp),
    )


def _sl_fill_price(position: _OpenPosition, bar_open: float, bar_high: float, bar_low: float):
    """Return the SL fill price if the stop is hit this bar, else None.

    Matches Backtesting.py: a long is stopped when ``low <= sl`` and fills at
    ``min(open, sl)`` (gap-through fills worse); a short when ``high >= sl`` and
    fills at ``max(open, sl)``.
    """
    sl = position.sl
    if math.isnan(sl):
        return None
    if position.direction == "buy":
        if bar_low <= sl:
            return min(bar_open, sl)
    else:
        if bar_high >= sl:
            return max(bar_open, sl)
    return None


def _close_position(
    position: _OpenPosition,
    price: float,
    bar_time: pd.Timestamp,
    reason: str,
    cash: float,
    commission: float,
    pair: str,
) -> tuple[BacktestTrade, float]:
    """Close ``position`` at ``price``; return the trade record and new cash.

    P&L and return mirror Backtesting.py's ``Trade.pl`` / ``Trade.pl_pct``:
    gross ``size * (exit - entry)`` minus entry+exit commission for P&L; the
    fractional return subtracts the per-unit commission from the gross percent.
    """
    entry_comm = _commission(position.size, position.entry_price, commission)
    exit_comm = _commission(position.size, price, commission)
    total_comm = entry_comm + exit_comm
    gross = position.size * (price - position.entry_price)
    pnl = gross - total_comm
    new_cash = cash + gross - exit_comm

    sign = 1.0 if position.size > 0 else -1.0
    gross_pct = sign * (price / position.entry_price - 1.0)
    comm_pct = total_comm / (abs(position.size) * position.entry_price)
    return_pct = gross_pct - comm_pct

    trade = BacktestTrade(
        pair=pair,
        direction=position.direction,
        size=position.size,
        entry_time=position.entry_time.to_pydatetime(),
        exit_time=bar_time.to_pydatetime(),
        entry_price=position.entry_price,
        exit_price=price,
        reason=reason,
        commission=total_comm,
        pnl=pnl,
        return_pct=return_pct,
    )
    return trade, new_cash


# ---------------------------------------------------------------------------
# Convenience: build a strategy from params and run
# ---------------------------------------------------------------------------


def build_lull_strategy(pairs: list[str], **param_overrides) -> DailyLullStrategy:
    """Construct a :class:`DailyLullStrategy` with the given params for backtest."""
    params = DailyLullParams(**param_overrides) if param_overrides else DailyLullParams()
    return DailyLullStrategy(pairs=list(pairs), params=params)


__all__ = [
    "BacktestResult",
    "BacktestTrade",
    "run_backtest",
    "build_lull_strategy",
]
