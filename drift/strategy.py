from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from drift.config import StrategyConfig
from drift.indicators import compute_all

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signal:
    pair: str
    timestamp: datetime
    h4_candle_time: datetime
    ema_fast: float
    ema_slow: float
    trend_direction: str
    macd_value: float
    macd_signal: float
    macd_histogram: float
    atr_value: float
    action: str
    reason: str


def check_d1_trend(ema_fast: pd.Series, ema_slow: pd.Series) -> str:
    fast = ema_fast.iloc[-1]
    slow = ema_slow.iloc[-1]
    if fast > slow:
        return "bullish"
    if fast < slow:
        return "bearish"
    return "none"


def check_h4_entry(macd_histogram: pd.Series, trend_direction: str) -> str:
    prev = macd_histogram.iloc[-2]
    curr = macd_histogram.iloc[-1]
    if trend_direction == "bullish" and prev < 0 and curr > 0:
        return "buy"
    if trend_direction == "bearish" and prev > 0 and curr < 0:
        return "sell"
    return "none"


def analyze_pair(
    pair: str,
    df_d1: pd.DataFrame,
    df_h4: pd.DataFrame,
    config: StrategyConfig,
) -> Signal:
    indicators = compute_all(df_d1, df_h4, config)

    ema_fast_val = float(indicators["ema_fast"].iloc[-1])
    ema_slow_val = float(indicators["ema_slow"].iloc[-1])
    macd_val = float(indicators["macd_line"].iloc[-1])
    macd_sig_val = float(indicators["macd_signal"].iloc[-1])
    macd_hist_val = float(indicators["macd_histogram"].iloc[-1])
    atr_val = float(indicators["atr"].iloc[-1])
    h4_candle_time = df_h4.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)

    trend = check_d1_trend(indicators["ema_fast"], indicators["ema_slow"])

    if trend == "none":
        reason = "no clear trend"
        logger.info("%s | trend=%s action=none reason=%s", pair, trend, reason)
        return Signal(
            pair=pair,
            timestamp=datetime.now(timezone.utc),
            h4_candle_time=h4_candle_time,
            ema_fast=ema_fast_val,
            ema_slow=ema_slow_val,
            trend_direction=trend,
            macd_value=macd_val,
            macd_signal=macd_sig_val,
            macd_histogram=macd_hist_val,
            atr_value=atr_val,
            action="none",
            reason=reason,
        )

    entry = check_h4_entry(indicators["macd_histogram"], trend)

    if entry == "none":
        reason = "no entry signal"
    else:
        reason = "trend + MACD confirmed"

    action = entry
    logger.info("%s | trend=%s action=%s reason=%s", pair, trend, action, reason)

    return Signal(
        pair=pair,
        timestamp=datetime.now(timezone.utc),
        h4_candle_time=h4_candle_time,
        ema_fast=ema_fast_val,
        ema_slow=ema_slow_val,
        trend_direction=trend,
        macd_value=macd_val,
        macd_signal=macd_sig_val,
        macd_histogram=macd_hist_val,
        atr_value=atr_val,
        action=action,
        reason=reason,
    )
