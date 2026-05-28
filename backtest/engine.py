"""Drift backtesting engine — mean reversion strategy.

Replicates the live strategy exactly:
  - Regime filter: MLP trained on first 70% of data (walk-forward), falls back to ADX < 25
  - H4 Bollinger Bands (20, 2σ) + RSI(14) entry
  - ATR-based SL (2x ATR), TP at middle Bollinger Band
  - Max trade duration: 30 H4 bars (~5 days)

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

from backtest._indicators import adx as _adx
from backtest._indicators import atr as _atr
from backtest._indicators import bollinger_bands as _bb
from backtest._indicators import rsi as _rsi
from drift.mlp_filter import RegimeFilter

logger = logging.getLogger(__name__)

PAIRS = ["AUDCAD", "NZDCAD", "AUDNZD", "EURCHF", "EURGBP"]


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------


def _compute_mlp_predictions(df_merged: pd.DataFrame, train_fraction: float = 0.70) -> np.ndarray:
    """Walk-forward MLP regime predictions — no look-ahead bias.

    Trains on the first `train_fraction` of data, predicts on the rest.
    Returns an array of length len(df_merged) where:
      - 1.0 = ranging (trade allowed)
      - 0.0 = trending (trade blocked)
      - NaN = training period or insufficient data
    """
    n = len(df_merged)
    split = int(n * train_fraction)

    rf = RegimeFilter()
    feature_df = df_merged[["adx", "bb_upper", "bb_middle", "bb_lower", "atr"]].copy()
    # The merged frame uses capitalised OHLCV already at this stage? No — renaming happens after.
    # This function is called before renaming, so lowercase columns are still present.
    features = rf.compute_features(feature_df)

    # Labels for training portion (uses future data — only valid inside training window)
    close_col = "close" if "close" in df_merged.columns else "Close"
    close_arr = df_merged[close_col].values
    labels = rf.label_regime(close_arr, lookforward=20, threshold=0.4)

    mlp_allowed = np.full(n, np.nan)

    # Need enough training data
    if split < 100:
        logger.warning("Too little data for MLP walk-forward (%d bars) — skipping MLP", n)
        return mlp_allowed

    train_features = features[:split]
    train_labels = labels[:split]

    mask = ~(np.isnan(train_features).any(axis=1) | np.isnan(train_labels))
    if mask.sum() < 50:
        logger.warning("Insufficient clean training samples (%d) — skipping MLP", mask.sum())
        return mlp_allowed

    rf.train(train_features, train_labels)
    logger.info(
        "MLP trained on %d samples (train frac=%.0f%%) — predicting on %d bars",
        mask.sum(),
        train_fraction * 100,
        n - split,
    )

    # Predict on the held-out portion bar by bar
    for i in range(split, n):
        row = features[i]
        if np.isnan(row).any():
            mlp_allowed[i] = np.nan
        else:
            mlp_allowed[i] = 1.0 if rf.predict(row) else 0.0

    return mlp_allowed


def prepare_backtest_data(
    df_d1: pd.DataFrame,
    df_h4: pd.DataFrame,
    bb_period: int = 20,
    bb_std_dev: float = 2.0,
    atr_period: int = 14,
    rsi_period: int = 14,
    adx_period: int = 14,
    use_mlp: bool = True,
    mlp_train_fraction: float = 0.70,
) -> pd.DataFrame:
    """Merge D1 ADX onto H4 timeframe and compute H4 indicators.

    When use_mlp=True the MLP is trained on the first mlp_train_fraction of data
    and its predictions are stored in the `mlp_allowed` column (1=ranging, 0=trending).
    The backtest strategy reads this column instead of the raw ADX threshold.

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

    df_d1["adx"] = _adx(df_d1["high"], df_d1["low"], df_d1["close"], adx_period)

    # Forward-fill D1 ADX onto H4 timestamps.
    # Shift by 1 day so each H4 bar only sees the previous D1 close — no look-ahead.
    d1_cols = df_d1[["adx"]].shift(1).resample("4h").last().ffill()
    df_merged = df_h4.join(d1_cols, how="left")
    df_merged["adx"] = df_merged["adx"].ffill()

    # H4 indicators
    bb_upper, bb_middle, bb_lower = _bb(df_h4["close"], bb_period, bb_std_dev)
    df_merged["bb_upper"] = bb_upper
    df_merged["bb_middle"] = bb_middle
    df_merged["bb_lower"] = bb_lower
    df_merged["rsi"] = _rsi(df_h4["close"], rsi_period)
    df_merged["atr"] = _atr(df_h4["high"], df_h4["low"], df_h4["close"], atr_period)

    # MLP walk-forward predictions (computed before NaN-drop and column rename)
    if use_mlp:
        df_merged["mlp_allowed"] = _compute_mlp_predictions(df_merged, mlp_train_fraction)
    else:
        df_merged["mlp_allowed"] = np.nan  # NaN → strategy falls back to ADX threshold

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
        subset=[
            "adx",
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "rsi",
            "atr",
            "Open",
            "High",
            "Low",
            "Close",
        ]
    )

    logger.info("Prepared backtest DataFrame: %d rows", len(df_merged))
    return df_merged


# ---------------------------------------------------------------------------
# Backtesting.py strategy
# ---------------------------------------------------------------------------


class DriftBacktestStrategy(Strategy):
    """MLP regime filter (walk-forward) + H4 BB + RSI mean reversion.

    Falls back to ADX < adx_max_threshold when mlp_allowed column is NaN.
    """

    sl_atr_mult: float = 2.0
    adx_max_threshold: float = 25.0
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    max_duration_bars: int = 30  # ~5 days at H4

    def init(self) -> None:
        self.adx_ind = self.I(lambda: self.data.adx, name="ADX")
        self.bb_upper = self.I(lambda: self.data.bb_upper, name="BB_Upper")
        self.bb_middle = self.I(lambda: self.data.bb_middle, name="BB_Middle")
        self.bb_lower = self.I(lambda: self.data.bb_lower, name="BB_Lower")
        self.rsi_ind = self.I(lambda: self.data.rsi, name="RSI")
        self.atr = self.I(lambda: self.data.atr, name="ATR")
        self.mlp_allowed = self.I(lambda: self.data.mlp_allowed, name="MLP_Allowed")
        self._entry_bar: int = 0

    def next(self) -> None:
        if len(self.data) < 2:
            return

        atr_val: float = self.atr[-1]
        if np.isnan(atr_val) or atr_val <= 0:
            return

        bb_upper_val: float = self.bb_upper[-1]
        bb_middle_val: float = self.bb_middle[-1]
        bb_lower_val: float = self.bb_lower[-1]
        rsi_val: float = self.rsi_ind[-1]
        curr_close: float = self.data.Close[-1]

        if any(np.isnan(v) for v in [bb_upper_val, bb_middle_val, bb_lower_val, rsi_val]):
            return

        # --- Exit: check TP at middle BB or time stop ---
        if self.position:
            bars_held = len(self.data) - self._entry_bar
            if self.position.is_long:
                if curr_close >= bb_middle_val:
                    self.position.close()
                    return
            elif self.position.is_short:
                if curr_close <= bb_middle_val:
                    self.position.close()
                    return
            if bars_held >= self.max_duration_bars:
                self.position.close()
                return

        # --- Entry: only in ranging markets ---
        if self.position:
            return

        # Regime check: prefer MLP prediction, fall back to ADX threshold
        mlp_val: float = self.mlp_allowed[-1]
        if not np.isnan(mlp_val):
            if mlp_val < 0.5:  # MLP says trending — skip
                return
        else:
            # MLP not available (training period or NaN) — use ADX fallback
            adx_val: float = self.adx_ind[-1]
            if np.isnan(adx_val) or adx_val >= self.adx_max_threshold:
                return

        sl_dist = atr_val * self.sl_atr_mult
        price = curr_close

        # BUY: price below lower BB and RSI oversold
        if curr_close < bb_lower_val and rsi_val < self.rsi_oversold:
            self.buy(sl=price - sl_dist)
            self._entry_bar = len(self.data)

        # SELL: price above upper BB and RSI overbought
        elif curr_close > bb_upper_val and rsi_val > self.rsi_overbought:
            self.sell(sl=price + sl_dist)
            self._entry_bar = len(self.data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_backtest(
    df: pd.DataFrame,
    cash: float = 500,
    commission: float = 0.00007,
    sl_atr_mult: float = 2.0,
    adx_max_threshold: float = 25.0,
) -> tuple[pd.Series, Backtest]:
    """Run the Drift mean reversion backtest and return (stats, bt).

    bt is kept so the caller can invoke bt.plot() or bt.optimize().
    """
    bt = Backtest(
        df,
        DriftBacktestStrategy,
        cash=cash,
        commission=commission,
        exclusive_orders=True,
    )
    stats = bt.run(sl_atr_mult=sl_atr_mult, adx_max_threshold=adx_max_threshold)
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
