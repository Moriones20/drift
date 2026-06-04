"""Daily Lull Scalper — live strategy module.

Ports the logic from backtest/lull_engine.py (DailyLullStrategy.next(), lines 160-258)
to a stateless-friendly function interface suitable for the live scheduler.

Session window (MT5 server time, GMT+3 — NOT UTC):
  21:00-22:59  Range definition — accumulate high/low, no entries.
  23:00-01:59  Trading window — evaluate entries once range is locked.
  02:00        Time stop — force-close any open position, reset state.
  02:01-20:59  Outside window — skip.

Candle timestamps arrive in server time but are labeled UTC-aware; the session
logic reasons over that server-time clock, not real UTC.  Timestamps must still
be tz-aware.  Never localize.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd

from drift.config import StrategyConfig
from drift.indicators import adx as _adx
from drift.indicators import atr as _atr
from drift.indicators import rsi as _rsi

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    """Per-pair tracking across one Daily Lull session (21:00→02:00 next day, server time GMT+3)."""

    session_date: int | None = None  # ordinal of session-start day (server time)
    high: float = float("-inf")  # running high during 21:00-22:59
    low: float = float("inf")  # running low during 21:00-22:59
    locked: bool = False  # True after 23:00 if range is valid
    range_high: float = float("nan")  # locked value
    range_low: float = float("nan")  # locked value
    traded: bool = False  # at most one trade per session per pair


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    action: Literal["buy", "sell", "none"]
    pair: str
    timestamp: datetime  # current M15 bar close, UTC
    m15_candle_time: datetime  # = timestamp (alias for db logging)
    h4_candle_time: datetime  # H4 bar used for ADX filter
    entry_price: float
    sl: float = float("nan")
    tp: float = float("nan")
    range_high: float = float("nan")
    range_low: float = float("nan")
    range_atr_ratio: float = float("nan")  # (range_high - range_low) / atr
    rsi: float = float("nan")  # M15
    atr_value: float = float("nan")  # M15
    h4_adx: float = float("nan")
    reason: str = ""  # human-readable rejection/acceptance reason
    rejection_reason: str | None = None  # set when a strategy check rejects the signal


# ---------------------------------------------------------------------------
# Session key helper (mirrors DailyLullStrategy._session_key)
# ---------------------------------------------------------------------------


def _session_key(bar_time: datetime) -> int:
    """Return an integer identifying the session this bar belongs to.

    Sessions start at 21:00 UTC.  Bars at 21:00-23:59 belong to the session
    of that calendar day.  Bars at 00:00-01:59 belong to the session that
    started the previous calendar day (carry-over).
    """
    if bar_time.hour < 2:
        # Carry-over: shift back to previous day's ordinal
        shifted = bar_time - pd.Timedelta(hours=3)
        return shifted.toordinal()
    return bar_time.toordinal()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def update_session_state(
    state: SessionState,
    bar_time: datetime,
    bar_high: float,
    bar_low: float,
    atr: float,
    config: StrategyConfig,
) -> None:
    """Update *state* in-place for the range-definition window (21:00-22:59 UTC).

    Also locks the range at 23:00 when the ATR filter passes.
    Called internally by evaluate_pair; exposed for the scheduler to call
    during the define-range phase when no entry evaluation is needed.
    """
    hour = bar_time.hour
    key = _session_key(bar_time)

    # Detect new session start at 21:00
    if hour == 21 and state.session_date != key:
        state.session_date = key
        state.high = float("-inf")
        state.low = float("inf")
        state.locked = False
        state.range_high = float("nan")
        state.range_low = float("nan")
        state.traded = False
        logger.debug("New session started — session_key=%d", key)

    # Accumulate range during 21:00-22:59
    if state.session_date == key and hour in (21, 22):
        if bar_high > state.high:
            state.high = bar_high
        if bar_low < state.low:
            state.low = bar_low

    # Lock range at 23:00 (only once per session)
    if (
        state.session_date == key
        and hour == 23
        and not state.locked
        and state.high > float("-inf")
        and state.low < float("inf")
    ):
        if not math.isnan(atr) and atr > 0:
            range_width = state.high - state.low
            in_range = config.range_atr_min * atr <= range_width <= config.range_atr_max * atr
            if in_range:
                state.range_high = state.high
                state.range_low = state.low
                state.locked = True
                logger.debug(
                    "Session range locked — high=%.5f low=%.5f width=%.5f atr=%.5f",
                    state.range_high,
                    state.range_low,
                    range_width,
                    atr,
                )
            else:
                # Range outside acceptable ATR bounds — skip this session
                state.locked = False
                logger.debug(
                    "Session range rejected — width=%.5f atr=%.5f min=%.1fx max=%.1fx",
                    range_width,
                    atr,
                    config.range_atr_min,
                    config.range_atr_max,
                )


def should_close_on_time(bar_time: datetime, config: StrategyConfig) -> bool:
    """Return True when bar_time signals the session time stop.

    Matches the backtest: hour == 2 triggers force-close of any open position.
    """
    return bar_time.hour == config.session_end_hour


def closed_bars(df: pd.DataFrame, before: datetime) -> pd.DataFrame:
    """Return only the bars that have already closed before *before* (D045).

    MT5 returns the still-forming bar as the last row.  Acting on it makes the
    live bot fire intra-bar on incomplete (and, at the 00:00 server rollover,
    contaminated) prices, diverging from the backtest which acts on completed-bar
    closes.  Keeping bars strictly before the current M15 boundary makes iloc[-1]
    the bar that just closed at *before*.
    """
    return df[df.index < before]


# ---------------------------------------------------------------------------
# Main evaluation function
# ---------------------------------------------------------------------------


def evaluate_pair(
    symbol: str,
    m15_df: pd.DataFrame,
    h4_df: pd.DataFrame,
    session_state: SessionState,
    config: StrategyConfig,
) -> Signal:
    """Evaluate one M15 bar close for *symbol* and return a Signal.

    Mirrors DailyLullStrategy.next() in backtest/lull_engine.py lines 160-258.

    Parameters
    ----------
    symbol:
        Forex pair identifier, e.g. "EURCHF".
    m15_df:
        M15 OHLCV DataFrame with a UTC-aware DatetimeIndex and columns
        open/high/low/close/volume (lowercase).  Must contain enough history
        for ATR and RSI warmup (~100 bars).
    h4_df:
        H4 OHLCV DataFrame with a UTC-aware DatetimeIndex and the same
        lowercase column names.  Must contain enough history for ADX warmup
        (~30 bars).
    session_state:
        Mutable per-pair state object.  Updated in-place.
    config:
        Strategy parameters.

    Returns
    -------
    Signal with action='buy'/'sell' when an entry condition is met, else
    action='none' with reason/rejection_reason populated.
    """
    if len(m15_df) < 2 or len(h4_df) < 2:
        from datetime import timezone  # local import to avoid top-level DTZ003 noise

        _now = datetime.now(tz=timezone.utc)
        _m15_t = m15_df.index[-1].to_pydatetime() if len(m15_df) >= 1 else _now
        _h4_t = h4_df.index[-1].to_pydatetime() if len(h4_df) >= 1 else _now
        return Signal(
            action="none",
            pair=symbol,
            timestamp=_m15_t,
            m15_candle_time=_m15_t,
            h4_candle_time=_h4_t,
            entry_price=float("nan"),
            reason="insufficient_data",
            rejection_reason="insufficient_data",
        )

    # --- Compute indicators ---
    m15_close = m15_df["close"]
    m15_high = m15_df["high"]
    m15_low = m15_df["low"]

    rsi_series = _rsi(m15_close, config.m15_rsi_period)
    atr_series = _atr(m15_high, m15_low, m15_close, config.m15_atr_period)

    # H4 ADX: shift by 1 to prevent look-ahead bias (mirrors prepare_lull_data)
    h4_adx_series = _adx(h4_df["high"], h4_df["low"], h4_df["close"], config.h4_adx_period)
    h4_adx_shifted = h4_adx_series.shift(1)

    # Current bar values
    atr_val = float(atr_series.iloc[-1])
    rsi_val = float(rsi_series.iloc[-1])

    # Propagate shifted H4 ADX forward-filled to the current M15 bar timestamp
    bar_ts = m15_df.index[-1]
    h4_adx_resampled = h4_adx_shifted.resample("15min").last().ffill()
    # Find the last H4 ADX value at or before the current M15 bar
    _valid_idx = h4_adx_resampled.index[h4_adx_resampled.index <= bar_ts]
    h4_adx_at_bar = h4_adx_resampled.reindex(_valid_idx)
    adx_val = float(h4_adx_at_bar.iloc[-1]) if len(h4_adx_at_bar) > 0 else float("nan")

    bar_time: datetime = bar_ts.to_pydatetime()
    h4_candle_time: datetime = h4_df.index[-1].to_pydatetime()

    curr_close = float(m15_df["close"].iloc[-1])
    curr_high = float(m15_df["high"].iloc[-1])
    curr_low = float(m15_df["low"].iloc[-1])

    def _base_signal(
        action: Literal["buy", "sell", "none"],
        reason: str,
        rejection_reason: str | None = None,
    ) -> Signal:
        range_width = (
            session_state.range_high - session_state.range_low
            if session_state.locked
            else float("nan")
        )
        ratio = (
            range_width / atr_val if (not math.isnan(range_width) and atr_val > 0) else float("nan")
        )
        return Signal(
            action=action,
            pair=symbol,
            timestamp=bar_time,
            m15_candle_time=bar_time,
            h4_candle_time=h4_candle_time,
            entry_price=curr_close,
            range_high=session_state.range_high,
            range_low=session_state.range_low,
            range_atr_ratio=ratio,
            rsi=rsi_val,
            atr_value=atr_val,
            h4_adx=adx_val,
            reason=reason,
            rejection_reason=rejection_reason,
        )

    # --- Guard: valid ATR required ---
    if math.isnan(atr_val) or atr_val <= 0:
        return _base_signal("none", "atr_not_ready", "atr_not_ready")

    # --- Guard: valid RSI and ADX required ---
    if math.isnan(rsi_val) or math.isnan(adx_val):
        return _base_signal("none", "indicators_not_ready", "indicators_not_ready")

    hour = bar_time.hour

    # --- Update session state (range definition + locking) ---
    update_session_state(
        state=session_state,
        bar_time=bar_time,
        bar_high=curr_high,
        bar_low=curr_low,
        atr=atr_val,
        config=config,
    )

    # --- Time stop: signal close at 02:00 UTC ---
    if should_close_on_time(bar_time, config):
        return _base_signal("none", "session_end_time_stop")

    # --- Entry logic: only between 23:00-01:59 UTC with a locked range ---

    # Outside trading window
    if hour not in (23, 0, 1):
        return _base_signal("none", "outside_window")

    # Range must be locked
    if not session_state.locked:
        return _base_signal("none", "range_not_locked", "range_not_locked")

    # Already traded this session
    if session_state.traded:
        return _base_signal("none", "already_traded_this_session", "already_traded_this_session")

    # H4 ADX regime filter
    if adx_val >= config.adx_max_threshold:
        reason = f"adx_trending adx={adx_val:.1f} threshold={config.adx_max_threshold}"
        logger.info("%s | action=none reason=%s", symbol, reason)
        return _base_signal("none", reason, "adx_trending")

    range_high = session_state.range_high
    range_low = session_state.range_low
    sl_dist = atr_val * config.sl_atr_mult
    mid = (range_high + range_low) / 2.0

    # BUY: price at/below session low AND RSI oversold
    if curr_close <= range_low and rsi_val < config.rsi_oversold:
        sl = curr_close - sl_dist
        tp = mid
        session_state.traded = True
        reason = f"lull_scalper_buy range_low={range_low:.5f} rsi={rsi_val:.1f} adx={adx_val:.1f}"
        logger.info(
            "%s | action=buy entry=%.5f sl=%.5f tp=%.5f rsi=%.1f adx=%.1f",
            symbol,
            curr_close,
            sl,
            tp,
            rsi_val,
            adx_val,
        )
        sig = _base_signal("buy", reason)
        sig.sl = sl
        sig.tp = tp
        return sig

    # SELL: price at/above session high AND RSI overbought
    if curr_close >= range_high and rsi_val > config.rsi_overbought:
        sl = curr_close + sl_dist
        tp = mid
        session_state.traded = True
        reason = (
            f"lull_scalper_sell range_high={range_high:.5f} rsi={rsi_val:.1f} adx={adx_val:.1f}"
        )
        logger.info(
            "%s | action=sell entry=%.5f sl=%.5f tp=%.5f rsi=%.1f adx=%.1f",
            symbol,
            curr_close,
            sl,
            tp,
            rsi_val,
            adx_val,
        )
        sig = _base_signal("sell", reason)
        sig.sl = sl
        sig.tp = tp
        return sig

    # No entry condition met
    return _base_signal(
        "none",
        f"no_entry_condition close={curr_close:.5f} range=[{range_low:.5f},{range_high:.5f}]"
        f" rsi={rsi_val:.1f}",
    )
