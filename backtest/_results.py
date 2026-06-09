"""Shared formatting/serialization for backtest result printouts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _geometric_mean(returns: pd.Series) -> float:
    """Geometric mean of a return series, matching Backtesting.py's helper.

    Mirrors ``_stats.geometric_mean`` exactly: NaNs are filled with 0 (so the
    first ``pct_change`` NaN counts in the denominator), and a non-positive
    gross return short-circuits to 0.
    """
    returns = returns.fillna(0) + 1
    if np.any(returns <= 0):
        return 0.0
    return float(np.exp(np.log(returns).sum() / (len(returns) or float("nan"))) - 1)


def compute_metrics(trades: list, equity: pd.Series) -> dict:
    """Compute the headline backtest metrics from trades and the equity curve.

    Replicates the relevant formulas of Backtesting.py's ``compute_stats`` so a
    backtest run through ``backtest.engine`` reports the same numbers
    ``backtest.lull_engine`` (Backtesting.py) would (D055 equivalence):

    - ``return_pct``      = (equity[-1] / equity[0] - 1) * 100
    - ``win_rate``        = share of trades with positive cash P&L * 100
    - ``profit_factor``   = sum(positive return_pct) / abs(sum(negative return_pct))
    - ``max_drawdown_pct``= -max(1 - equity / running_peak) * 100
    - ``sharpe``          = annualized return / annualized volatility (same as
                            ``compute_stats``: geometric-mean daily return,
                            252 or 365 trading days depending on weekend presence)
    - ``trades``          = number of closed trades
    - ``return_pcts``     = the per-trade fractional returns (for the equivalence
                            test's profit-factor / win-rate cross-checks)

    ``trades`` items must expose ``.pnl`` (net cash P&L) and ``.return_pct``
    (fractional, matching ``Trade.pl_pct``).
    """
    n = len(trades)
    eq = equity.to_numpy(dtype=float)

    return_pct = (eq[-1] - eq[0]) / eq[0] * 100 if len(eq) and eq[0] != 0 else float("nan")

    running_peak = np.maximum.accumulate(eq) if len(eq) else np.array([1.0])
    dd = 1 - eq / running_peak
    max_dd = -float(np.nan_to_num(dd.max())) if len(eq) else 0.0
    max_drawdown_pct = max_dd * 100

    pnls = np.array([t.pnl for t in trades], dtype=float)
    rets = pd.Series([t.return_pct for t in trades], dtype=float)

    win_rate = float((pnls > 0).mean()) * 100 if n else float("nan")
    pos = rets[rets > 0].sum()
    neg = abs(rets[rets < 0].sum())
    profit_factor = float(pos / neg) if neg else float("nan")

    sharpe = _annualized_sharpe(equity)

    return {
        "return_pct": return_pct,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe": sharpe,
        "trades": n,
        "return_pcts": rets.tolist(),
    }


def _annualized_sharpe(equity: pd.Series) -> float:
    """Annualized Sharpe ratio matching Backtesting.py's ``compute_stats``.

    Resamples the equity curve to daily last values, takes the geometric mean of
    daily pct-change returns, annualizes return and volatility (365 trading days
    when the index carries weekends, else 252), and divides.  Returns NaN when
    volatility is zero or the index is not datetime-based.
    """
    if not isinstance(equity.index, pd.DatetimeIndex) or len(equity) < 2:
        return float("nan")

    have_weekends = equity.index.dayofweek.to_series().between(5, 6).mean() > 2 / 7 * 0.6
    annual_trading_days = 365 if have_weekends else 252

    day_returns = equity.resample("D").last().dropna().pct_change()
    gmean = _geometric_mean(day_returns)

    annualized_return = (1 + gmean) ** annual_trading_days - 1
    var = day_returns.var(ddof=int(bool(day_returns.shape)))
    volatility = np.sqrt(
        (var + (1 + gmean) ** 2) ** annual_trading_days - (1 + gmean) ** (2 * annual_trading_days)
    )
    if not volatility or np.isnan(volatility):
        return float("nan")
    return float((annualized_return * 100) / (volatility * 100))


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


def format_metrics_results(symbol: str, metrics: dict, period_start, period_end) -> str:
    """Human-readable summary for a backtest run through ``backtest.engine``.

    Mirrors :func:`format_results` but reads the ``metrics`` dict produced by
    :func:`compute_metrics` instead of a Backtesting.py stats Series.
    """

    def _f(key: str, fmt: str = ".2f") -> str:
        val = metrics.get(key, float("nan"))
        try:
            return format(float(val), fmt)
        except (TypeError, ValueError):
            return str(val)

    lines = [
        f"{'=' * 46}",
        f"  {symbol} Backtest Results",
        f"{'=' * 46}",
        f"  Period:            {period_start}  ->  {period_end}",
        f"  Total Return:      {_f('return_pct'):>10}%",
        f"  Sharpe Ratio:      {_f('sharpe'):>10}",
        f"  Win Rate:          {_f('win_rate'):>10}%",
        f"  Profit Factor:     {_f('profit_factor'):>10}",
        f"  # Trades:          {_f('trades', 'd'):>10}",
        f"  Max Drawdown:      {_f('max_drawdown_pct'):>10}%",
        f"{'=' * 46}",
    ]
    return "\n".join(lines)


def save_metrics_results(
    symbol: str, metrics: dict, output_dir: Path, period_start=None, period_end=None
) -> Path:
    """Serialize an engine ``metrics`` dict (plus period) to JSON.

    Drops the bulky per-trade ``return_pcts`` list from the saved file; the
    headline metrics are what the comparison report and downstream tooling read.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in metrics.items() if k != "return_pcts"}
    if period_start is not None:
        payload["start"] = str(period_start)
    if period_end is not None:
        payload["end"] = str(period_end)

    out_path = output_dir / f"{symbol}.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump({"symbol": symbol, "metrics": payload}, fh, indent=2)

    logger.info("Results saved to %s", out_path)
    return out_path


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
