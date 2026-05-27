"""Drift backtesting engine — reusable module for all 6 pairs.

Replicates the live strategy exactly:
  - D1 EMA 50/200 trend filter
  - H4 MACD histogram crossover entry
  - ATR-based SL (1.5x) and TP (2x SL)

Usage:
    from backtest.engine import prepare_backtest_data, run_backtest, format_results, save_results
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest._indicators import atr as _atr
from backtest._indicators import ema as _ema
from backtest._indicators import macd_histogram as _macd_histogram

logger = logging.getLogger(__name__)

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURGBP"]


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------


def prepare_backtest_data(
    df_d1: pd.DataFrame,
    df_h4: pd.DataFrame,
    ema_fast: int = 50,
    ema_slow: int = 200,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Merge D1 EMAs onto H4 timeframe and compute H4 indicators.

    Accepts DataFrames with lowercase OHLCV columns (open/high/low/close/tick_volume
    or volume). Returns a DataFrame with capitalised OHLCV columns plus indicator
    columns, ready for Backtesting.py.
    """
    df_d1 = df_d1.copy()
    df_h4 = df_h4.copy()

    # Normalise volume column name
    for df in (df_d1, df_h4):
        if "tick_volume" in df.columns and "volume" not in df.columns:
            df.rename(columns={"tick_volume": "volume"}, inplace=True)

    df_d1["ema_fast"] = _ema(df_d1["close"], ema_fast)
    df_d1["ema_slow"] = _ema(df_d1["close"], ema_slow)

    # Forward-fill D1 EMAs onto H4 timestamps
    d1_emas = df_d1[["ema_fast", "ema_slow"]].resample("4h").last().ffill()
    df_merged = df_h4.join(d1_emas, how="left")
    df_merged["ema_fast"] = df_merged["ema_fast"].ffill()
    df_merged["ema_slow"] = df_merged["ema_slow"].ffill()

    df_merged["macd_hist"] = _macd_histogram(df_h4["close"], macd_fast, macd_slow, macd_signal)
    df_merged["atr"] = _atr(df_h4["high"], df_h4["low"], df_h4["close"], atr_period)

    df_merged = df_merged.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )

    df_merged = df_merged.dropna(
        subset=["ema_fast", "ema_slow", "macd_hist", "atr", "Open", "High", "Low", "Close"]
    )

    logger.info("Prepared backtest DataFrame: %d rows", len(df_merged))
    return df_merged


# ---------------------------------------------------------------------------
# Backtesting.py strategy
# ---------------------------------------------------------------------------


class DriftBacktestStrategy(Strategy):
    """EMA trend filter (D1) + MACD histogram crossover entry (H4)."""

    sl_atr_mult: float = 1.5
    tp_ratio: float = 2.0

    def init(self) -> None:
        self.ema_fast = self.I(lambda: self.data.ema_fast, name="EMA_Fast")
        self.ema_slow = self.I(lambda: self.data.ema_slow, name="EMA_Slow")
        self.macd_hist = self.I(lambda: self.data.macd_hist, name="MACD_Hist")
        self.atr = self.I(lambda: self.data.atr, name="ATR")

    def next(self) -> None:
        if len(self.data) < 2:
            return

        atr_val: float = self.atr[-1]
        if np.isnan(atr_val) or atr_val <= 0:
            return

        sl_dist = atr_val * self.sl_atr_mult
        tp_dist = sl_dist * self.tp_ratio

        bullish_trend = self.ema_fast[-1] > self.ema_slow[-1]
        bearish_trend = self.ema_fast[-1] < self.ema_slow[-1]

        hist_prev: float = self.macd_hist[-2]
        hist_curr: float = self.macd_hist[-1]

        if np.isnan(hist_prev) or np.isnan(hist_curr):
            return

        macd_crossed_up = hist_prev < 0 < hist_curr
        macd_crossed_down = hist_prev > 0 > hist_curr

        price = self.data.Close[-1]

        if not self.position:
            if bullish_trend and macd_crossed_up:
                self.buy(sl=price - sl_dist, tp=price + tp_dist)
            elif bearish_trend and macd_crossed_down:
                self.sell(sl=price + sl_dist, tp=price - tp_dist)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_backtest(
    df: pd.DataFrame,
    cash: float = 500,
    commission: float = 0.00007,
    sl_atr_mult: float = 1.5,
    tp_ratio: float = 2.0,
) -> tuple[pd.Series, Backtest]:
    """Run the Drift strategy backtest and return (stats, bt).

    bt is kept so the caller can invoke bt.plot() or bt.optimize().
    """
    bt = Backtest(
        df,
        DriftBacktestStrategy,
        cash=cash,
        commission=commission,
        exclusive_orders=True,
    )
    stats = bt.run(sl_atr_mult=sl_atr_mult, tp_ratio=tp_ratio)
    logger.info(
        "Backtest complete — %d trades, return %.2f%%",
        int(stats.get("# Trades", 0)),
        float(stats.get("Return [%]", float("nan"))),
    )
    return stats, bt


def format_results(symbol: str, stats: pd.Series) -> str:
    """Return a human-readable summary string for a completed backtest."""

    def _f(key: str, fmt: str = ".2f") -> str:
        val = stats.get(key, float("nan"))
        try:
            return format(float(val), fmt)
        except (TypeError, ValueError):
            return str(val)

    period_start = stats.get("Start", "?")
    period_end = stats.get("End", "?")

    lines = [
        f"{'=' * 46}",
        f"  {symbol} Backtest Results",
        f"{'=' * 46}",
        f"  Period:            {period_start}  ->  {period_end}",
        f"  Total Return:      {_f('Return [%]'):>10}%",
        f"  CAGR:              {_f('Return (Ann.) [%]'):>10}%",
        f"  Sharpe Ratio:      {_f('Sharpe Ratio'):>10}",
        f"  Sortino Ratio:     {_f('Sortino Ratio'):>10}",
        f"  Win Rate:          {_f('Win Rate [%]'):>10}%",
        f"  Profit Factor:     {_f('Profit Factor'):>10}",
        f"  # Trades:          {_f('# Trades', 'd'):>10}",
        f"  Max Drawdown:      {_f('Max. Drawdown [%]'):>10}%",
        f"  Avg Trade:         {_f('Avg. Trade [%]'):>10}%",
        f"  Avg Trade Duration:{_f('Avg. Trade Duration'):>10}",
        f"  Best Trade:        {_f('Best Trade [%]'):>10}%",
        f"  Worst Trade:       {_f('Worst Trade [%]'):>10}%",
        f"{'=' * 46}",
    ]
    return "\n".join(lines)


def save_results(symbol: str, stats: pd.Series, output_dir: Path) -> Path:
    """Serialize backtest stats to JSON and return the saved file path."""
    output_dir.mkdir(parents=True, exist_ok=True)

    serialisable: dict = {}
    for key, value in stats.items():
        if isinstance(value, (int, float, str, bool)) or value is None:
            serialisable[key] = value
        elif isinstance(value, pd.Timestamp):
            serialisable[key] = value.isoformat()
        elif isinstance(value, pd.Timedelta):
            serialisable[key] = str(value)
        else:
            try:
                serialisable[key] = float(value)
            except (TypeError, ValueError):
                serialisable[key] = str(value)

    out_path = output_dir / f"{symbol}.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump({"symbol": symbol, "stats": serialisable}, fh, indent=2)

    logger.info("Results saved to %s", out_path)
    return out_path
