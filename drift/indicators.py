from __future__ import annotations

import logging

import pandas as pd
import pandas_ta as ta

from drift.config import StrategyConfig

logger = logging.getLogger(__name__)


def compute_ema(df: pd.DataFrame, period: int) -> pd.Series:
    if len(df) < period:
        raise ValueError(f"Need at least {period} rows for EMA({period}), got {len(df)}")
    result = ta.ema(df["close"], length=period)
    result.name = f"ema_{period}"
    return result


def compute_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    min_rows = slow + signal
    if len(df) < min_rows:
        raise ValueError(
            f"Need at least {min_rows} rows for MACD({fast},{slow},{signal}), got {len(df)}"
        )
    result = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
    suffix = f"{fast}_{slow}_{signal}"
    macd_line = result[f"MACD_{suffix}"].rename("macd_line")
    signal_line = result[f"MACDs_{suffix}"].rename("macd_signal")
    histogram = result[f"MACDh_{suffix}"].rename("macd_histogram")
    return macd_line, signal_line, histogram


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if len(df) < period:
        raise ValueError(f"Need at least {period} rows for ATR({period}), got {len(df)}")
    result = ta.atr(df["high"], df["low"], df["close"], length=period)
    result.name = f"atr_{period}"
    return result


def compute_all(df_d1: pd.DataFrame, df_h4: pd.DataFrame, config: StrategyConfig) -> dict:
    logger.debug(
        "Computing indicators — D1 rows: %d, H4 rows: %d",
        len(df_d1),
        len(df_h4),
    )

    ema_fast = compute_ema(df_d1, config.ema_fast)
    ema_slow = compute_ema(df_d1, config.ema_slow)
    macd_line, macd_signal, macd_histogram = compute_macd(
        df_h4,
        fast=config.macd_fast,
        slow=config.macd_slow,
        signal=config.macd_signal,
    )
    atr = compute_atr(df_h4, config.atr_period)

    return {
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "atr": atr,
    }
