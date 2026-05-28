from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# pandas_ta is an optional heavy dependency that requires Python <3.14 (numba/tqdm constraint).
# Import it lazily so the Wilder-smoothed indicator functions (rsi, atr, adx) remain importable
# in environments where pandas_ta is not installed (e.g. Python 3.14 on dev machines).
# The compute_* wrappers below will raise ImportError at call time if pandas_ta is absent.
try:
    import pandas_ta as ta  # type: ignore[import-untyped]

    _TA_AVAILABLE = True
except ImportError:  # pragma: no cover
    ta = None  # type: ignore[assignment]
    _TA_AVAILABLE = False


def _require_ta() -> None:
    if not _TA_AVAILABLE:
        raise ImportError(
            "pandas_ta is required for compute_* functions but is not installed. "
            "Install it with: pip install pandas_ta"
        )


def compute_ema(df: pd.DataFrame, period: int) -> pd.Series:
    _require_ta()
    if len(df) < period:
        raise ValueError(f"Need at least {period} rows for EMA({period}), got {len(df)}")
    result = ta.ema(df["close"], length=period)
    result.name = f"ema_{period}"
    return result


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    _require_ta()
    if len(df) < period + 1:
        raise ValueError(f"Need at least {period + 1} rows for RSI({period}), got {len(df)}")
    result = ta.rsi(df["close"], length=period)
    result.name = f"rsi_{period}"
    return result


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    _require_ta()
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
    _require_ta()
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
    _require_ta()
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


# ---------------------------------------------------------------------------
# Wilder-smoothing implementations for Asian Session Scalper
# These mirror backtest/_indicators.py exactly to ensure live/backtest parity.
# ---------------------------------------------------------------------------


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI using Wilder's smoothing (EWM alpha=1/period).

    Matches backtest/_indicators.py rsi() exactly.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100.0 - (100.0 / (1.0 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Compute ATR using Wilder's smoothing (EWM alpha=1/period).

    Matches backtest/_indicators.py atr() exactly.
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute ADX using Wilder's smoothing (alpha=1/period).

    Matches backtest/_indicators.py adx() exactly.
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    plus_dm = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)
    # Where +DM <= -DM, zero out +DM and vice-versa
    mask = plus_dm >= minus_dm
    plus_dm = plus_dm.where(mask, 0.0)
    minus_dm = minus_dm.where(~mask, 0.0)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    alpha = 1.0 / period
    atr_val = tr.ewm(alpha=alpha, adjust=False).mean()
    _nan = float("nan")
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_val.replace(0, _nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_val.replace(0, _nan)

    di_sum = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_val = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx_val
