from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# pandas_ta is an optional heavy dependency that requires Python <3.14 (numba/tqdm constraint).
# Import it lazily so the Wilder-smoothed indicator functions (rsi, atr, adx) remain importable
# in environments where pandas_ta is not installed (e.g. Python 3.14 on dev machines).
# compute_atr uses pandas_ta and is required by drift/trailing.py.
try:
    import pandas_ta as ta  # type: ignore[import-untyped]

    _TA_AVAILABLE = True
except ImportError:  # pragma: no cover
    ta = None  # type: ignore[assignment]
    _TA_AVAILABLE = False


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if not _TA_AVAILABLE:
        raise ImportError(
            "pandas_ta is required for compute_atr but is not installed. "
            "Install it with: pip install pandas_ta"
        )
    if len(df) < period:
        raise ValueError(f"Need at least {period} rows for ATR({period}), got {len(df)}")
    result = ta.atr(df["high"], df["low"], df["close"], length=period)
    result.name = f"atr_{period}"
    return result


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
