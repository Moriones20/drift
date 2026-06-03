"""Asian Session Scalper backtest engine — mean reversion during quiet Asian hours.

Strategy rules:
  - Timeframe: M15 (15-minute candles)
  - Trading window: 21:00-02:00 MT5 server time (NY-anchored, GMT+2/+3 w/DST; the
    quiet NY-close → pre-Asia lull — see D041. NOT the Asian session despite the name)
  - Session range: High/Low of the 21:00-23:00 definition window
  - BUY when price <= session low AND RSI(14) < rsi_oversold (default 35)
  - SELL when price >= session high AND RSI(14) > rsi_overbought (default 65)
  - Range filter: range_atr_min x ATR(14) <= range_width <= range_atr_max x ATR(14)
  - TP: middle of session range; SL: sl_atr_mult x ATR(14); time stop at 02:00 server
  - ADX(14) on H4 < adx_max_threshold (default 35, ranging market filter)
  - Max 1 trade per session per pair

Usage:
    from backtest.asian_engine import (
        prepare_asian_data, run_asian_backtest, format_results, save_results
    )
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest._indicators import adx as _adx
from backtest._indicators import atr as _atr
from backtest._indicators import rsi as _rsi
from backtest._results import format_results, save_results

logger = logging.getLogger(__name__)

ASIAN_PAIRS = ["EURCHF", "EURGBP", "AUDNZD", "USDJPY", "GBPJPY", "EURJPY"]


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------


def prepare_asian_data(
    df_h4: pd.DataFrame,
    df_m15: pd.DataFrame,
    rsi_period: int = 14,
    atr_period: int = 14,
    adx_period: int = 14,
) -> pd.DataFrame:
    """Merge H4 ADX onto M15 timeframe and compute M15 RSI and ATR.

    H4 ADX is shifted by one H4 bar to prevent look-ahead bias.
    Accepts DataFrames with lowercase OHLCV columns (open/high/low/close/tick_volume
    or volume). Returns a DataFrame with capitalised OHLCV columns plus indicator
    columns, ready for Backtesting.py.
    """
    df_h4 = df_h4.copy()
    df_m15 = df_m15.copy()

    for df in (df_h4, df_m15):
        if "tick_volume" in df.columns and "volume" not in df.columns:
            df.rename(columns={"tick_volume": "volume"}, inplace=True)

    df_h4["h4_adx"] = _adx(df_h4["high"], df_h4["low"], df_h4["close"], adx_period)

    # Shift by one H4 bar so each M15 bar sees only the previous H4 close ADX.
    h4_adx_shifted = df_h4[["h4_adx"]].shift(1)
    h4_adx_resampled = h4_adx_shifted.resample("15min").last().ffill()

    df_merged = df_m15.join(h4_adx_resampled, how="left")
    df_merged["h4_adx"] = df_merged["h4_adx"].ffill()

    # M15 indicators
    df_merged["rsi"] = _rsi(df_m15["close"], rsi_period)
    df_merged["atr"] = _atr(df_m15["high"], df_m15["low"], df_m15["close"], atr_period)

    df_merged = df_merged.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )

    df_merged = df_merged.dropna(subset=["h4_adx", "rsi", "atr", "Open", "High", "Low", "Close"])

    logger.info("Prepared Asian M15 DataFrame: %d rows", len(df_merged))
    return df_merged


# ---------------------------------------------------------------------------
# Backtesting.py strategy
# ---------------------------------------------------------------------------


class AsianSessionStrategy(Strategy):
    """Asian session scalper — mean reversion to session range midpoint.

    Entries only between 23:00-02:00 server time after the 21:00-23:00 range is set.
    """

    sl_atr_mult: float = 2.5
    adx_max_threshold: float = 35.0
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    range_atr_min: float = 1.0
    range_atr_max: float = 4.0

    def init(self) -> None:
        self.rsi_ind = self.I(lambda: self.data.rsi, name="RSI")
        self.atr_ind = self.I(lambda: self.data.atr, name="ATR")
        self.h4_adx = self.I(lambda: self.data.h4_adx, name="H4_ADX")

        # Per-session tracking (reset each 21:00 server time)
        self._session_date: int | None = None  # ordinal of the session-start day
        self._session_high: float = float("-inf")
        self._session_low: float = float("inf")
        self._session_range_locked: bool = False  # True after 23:00 range confirmed
        self._session_range_high: float = float("nan")
        self._session_range_low: float = float("nan")
        self._session_traded: bool = False  # max 1 trade per session

    def _bar_hour(self) -> int:
        """Return the MT5 server-time hour of the current bar (see D041)."""
        return int(self.data.index[-1].hour)

    def _bar_day_ordinal(self) -> int:
        """Return the ordinal day of the current bar (server date)."""
        return int(self.data.index[-1].toordinal())

    def _session_key(self) -> int:
        """Return an integer that identifies the current session.

        Sessions start at 21:00 server time.  Bars at 21:00-23:59 belong to the
        'session of that calendar day'; bars at 00:00-01:59 belong to the
        session that started the previous calendar day.
        """
        ts = self.data.index[-1]
        if ts.hour < 2:
            # Carry-over from previous calendar day's 21:00 session
            return (ts - pd.Timedelta(hours=3)).toordinal()
        return ts.toordinal()

    def _reset_session(self, session_key: int) -> None:
        """Reset all session-tracking state for a new session."""
        self._session_date = session_key
        self._session_high = float("-inf")
        self._session_low = float("inf")
        self._session_range_locked = False
        self._session_range_high = float("nan")
        self._session_range_low = float("nan")
        self._session_traded = False

    def next(self) -> None:
        if len(self.data) < 2:
            return

        atr_val: float = float(self.atr_ind[-1])
        if np.isnan(atr_val) or atr_val <= 0:
            return

        rsi_val: float = float(self.rsi_ind[-1])
        adx_val: float = float(self.h4_adx[-1])
        curr_close: float = float(self.data.Close[-1])
        curr_high: float = float(self.data.High[-1])
        curr_low: float = float(self.data.Low[-1])

        if any(np.isnan(v) for v in [rsi_val, adx_val]):
            return

        hour: int = self._bar_hour()
        session_key: int = self._session_key()

        # --- Detect new session (21:00 server time) ---
        if hour == 21 and self._session_date != session_key:
            self._reset_session(session_key)

        # --- Accumulate session range definition window (21:00-23:00) ---
        if self._session_date == session_key and hour in (21, 22):
            if curr_high > self._session_high:
                self._session_high = curr_high
            if curr_low < self._session_low:
                self._session_low = curr_low

        # --- Lock the range at 23:00 ---
        if (
            self._session_date == session_key
            and hour == 23
            and not self._session_range_locked
            and self._session_high > float("-inf")
            and self._session_low < float("inf")
        ):
            range_width = self._session_high - self._session_low
            in_range = self.range_atr_min * atr_val <= range_width <= self.range_atr_max * atr_val
            if atr_val > 0 and in_range:
                self._session_range_high = self._session_high
                self._session_range_low = self._session_low
                self._session_range_locked = True
                logger.debug(
                    "Session range locked: high=%.5f low=%.5f width=%.5f atr=%.5f",
                    self._session_range_high,
                    self._session_range_low,
                    range_width,
                    atr_val,
                )
            else:
                # Range outside acceptable ATR bounds — skip this session
                self._session_range_locked = False

        # --- Time stop: close any open trade at 02:00 server time ---
        if hour == 2 and self.position:
            self.position.close()
            return

        # --- Exit at session TP (range midpoint) ---
        if self.position and self._session_range_locked:
            mid = (self._session_range_high + self._session_range_low) / 2.0
            if self.position.is_long and curr_close >= mid:
                self.position.close()
                return
            if self.position.is_short and curr_close <= mid:
                self.position.close()
                return

        # --- Entry logic: only between 23:00-01:59 with a locked range ---
        if self.position or self._session_traded:
            return

        if not self._session_range_locked:
            return

        # Only trade during the entry window (23:00-01:59 server time)
        if hour not in (23, 0, 1):
            return

        # H4 regime filter
        if np.isnan(adx_val) or adx_val >= self.adx_max_threshold:
            return

        range_high = self._session_range_high
        range_low = self._session_range_low
        sl_dist = atr_val * self.sl_atr_mult

        # BUY: price at/below session low and RSI oversold
        if curr_close <= range_low and rsi_val < self.rsi_oversold:
            self.buy(sl=curr_close - sl_dist)
            self._session_traded = True

        # SELL: price at/above session high and RSI overbought
        elif curr_close >= range_high and rsi_val > self.rsi_overbought:
            self.sell(sl=curr_close + sl_dist)
            self._session_traded = True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_asian_backtest(
    df: pd.DataFrame,
    cash: float = 500,
    commission: float = 0.00007,
    sl_atr_mult: float = 2.5,
    adx_max_threshold: float = 35.0,
    rsi_oversold: float = 35.0,
    rsi_overbought: float = 65.0,
    range_atr_min: float = 1.0,
    range_atr_max: float = 4.0,
) -> tuple[pd.Series, Backtest]:
    """Run the Asian Session Scalper backtest and return (stats, bt).

    bt is kept so the caller can invoke bt.plot() or bt.optimize().
    """
    bt = Backtest(
        df,
        AsianSessionStrategy,
        cash=cash,
        commission=commission,
        exclusive_orders=True,
    )
    stats = bt.run(
        sl_atr_mult=sl_atr_mult,
        adx_max_threshold=adx_max_threshold,
        rsi_oversold=rsi_oversold,
        rsi_overbought=rsi_overbought,
        range_atr_min=range_atr_min,
        range_atr_max=range_atr_max,
    )
    logger.info(
        "Asian backtest complete — %d trades, return %.2f%%",
        int(stats.get("# Trades", 0)),
        float(stats.get("Return [%]", float("nan"))),
    )
    return stats, bt


__all__ = [
    "ASIAN_PAIRS",
    "prepare_asian_data",
    "AsianSessionStrategy",
    "run_asian_backtest",
    "format_results",
    "save_results",
]
