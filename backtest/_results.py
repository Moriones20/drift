"""Shared formatting/serialization for backtest result printouts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


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
