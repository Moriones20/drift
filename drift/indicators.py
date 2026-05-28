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


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if len(df) < period + 1:
        raise ValueError(f"Need at least {period + 1} rows for RSI({period}), got {len(df)}")
    result = ta.rsi(df["close"], length=period)
    result.name = f"rsi_{period}"
    return result


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    min_rows = period * 2
    if len(df) < min_rows:
        raise ValueError(f"Need at least {min_rows} rows for ADX({period}), got {len(df)}")
    result = ta.adx(df["high"], df["low"], df["close"], length=period)
    adx_col = f"ADX_{period}"
    adx = result[adx_col].rename(f"adx_{period}")
    return adx


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


def compute_bollinger_bands(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute Bollinger Bands (upper, middle/SMA, lower).

    Returns (bb_upper, bb_middle, bb_lower) as pandas Series.
    """
    if len(df) < period:
        raise ValueError(
            f"Need at least {period} rows for Bollinger Bands({period}), got {len(df)}"
        )
    close = df["close"]
    middle = close.rolling(window=period).mean()
    std = close.rolling(window=period).std(ddof=1)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    upper.name = f"bb_upper_{period}"
    middle.name = f"bb_middle_{period}"
    lower.name = f"bb_lower_{period}"
    return upper, middle, lower


def compute_all(df_d1: pd.DataFrame, df_h4: pd.DataFrame, config: StrategyConfig) -> dict:
    logger.debug(
        "Computing indicators — D1 rows: %d, H4 rows: %d",
        len(df_d1),
        len(df_h4),
    )

    adx = compute_adx(df_d1, config.adx_period)
    rsi = compute_rsi(df_h4, config.rsi_period)
    atr = compute_atr(df_h4, config.atr_period)
    bb_upper, bb_middle, bb_lower = compute_bollinger_bands(
        df_h4, config.bb_period, config.bb_std_dev
    )

    return {
        "adx": adx,
        "rsi": rsi,
        "atr": atr,
        "bb_upper": bb_upper,
        "bb_middle": bb_middle,
        "bb_lower": bb_lower,
    }
