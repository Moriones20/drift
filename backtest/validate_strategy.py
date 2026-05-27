"""Validation backtest for Drift strategy — EURUSD and GBPUSD.

Runs with MT5 if available; falls back to synthetic trending data otherwise.
Usage: python backtest/validate_strategy.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Backtest

# Make project root importable when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.engine import DriftBacktestStrategy, prepare_backtest_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PAIRS = ["EURUSD", "GBPUSD"]
D1_COUNT = 500
H4_COUNT = 3000
COMMISSION = 0.00007  # ~$7/lot round trip for ICMarkets
INITIAL_CASH = 10_000
EQUITY_FRACTION = 0.10  # proxy for 1% risk at typical leverage


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _fetch_mt5_data(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Return (df_d1, df_h4) from MT5, or None if MT5 is unavailable."""
    try:
        import MetaTrader5  # noqa: F401
    except ImportError:
        logger.warning("MetaTrader5 package not installed — skipping MT5 path")
        return None

    try:
        from drift.config import load_config
        from drift.mt5_client import connect, disconnect, get_candles
    except Exception as exc:
        logger.warning("Could not import drift modules (%s) — skipping MT5 path", exc)
        return None

    try:
        config = load_config()
    except FileNotFoundError:
        logger.warning("config.yaml not found — skipping MT5 path")
        return None

    if not connect(config.broker):
        logger.warning("MT5 connection failed — falling back to synthetic data")
        return None

    try:
        df_d1 = get_candles(symbol, "D1", count=D1_COUNT)
        df_h4 = get_candles(symbol, "H4", count=H4_COUNT)
    finally:
        disconnect()

    df_d1.columns = df_d1.columns.str.lower()
    df_h4.columns = df_h4.columns.str.lower()

    return df_d1, df_h4


def _make_synthetic_data(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate synthetic OHLCV data with alternating trend regimes.

    Regime alternation ensures the EMA crossover / MACD strategy sees both
    bullish and bearish conditions, producing a realistic mix of signals.
    """
    logger.info("Generating synthetic data for %s", symbol)
    rng = np.random.default_rng(seed=42 if symbol == "EURUSD" else 7)

    base_price = 1.08 if symbol == "EURUSD" else 1.26
    h4_vol = 0.0008

    n = H4_COUNT
    idx = pd.date_range("2022-01-03", periods=n, freq="4h", tz="UTC")

    closes: list[float] = [base_price]
    trend_cycle = 200  # bars per regime
    for i in range(1, n):
        phase = (i // trend_cycle) % 2
        drift_per_bar = h4_vol * (0.3 if phase == 0 else -0.3)
        closes.append(max(closes[-1] + drift_per_bar + rng.normal(0, h4_vol), 0.5))

    c = np.array(closes)
    spread = h4_vol * 0.5
    highs = c + np.abs(rng.normal(0, spread, n))
    lows = c - np.abs(rng.normal(0, spread, n))
    opens = np.roll(c, 1)
    opens[0] = c[0]
    volumes = rng.integers(500, 2000, n).astype(float)

    df_h4 = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": c, "tick_volume": volumes},
        index=idx,
    )

    df_d1 = (
        df_h4.resample("D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "tick_volume": "sum"})
        .dropna()
        .iloc[:D1_COUNT]
    )

    return df_d1, df_h4


def _get_data(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = _fetch_mt5_data(symbol)
    if result is not None:
        logger.info("Using live MT5 data for %s", symbol)
        return result
    logger.info("Using synthetic data for %s", symbol)
    return _make_synthetic_data(symbol)


# ---------------------------------------------------------------------------
# Metrics output
# ---------------------------------------------------------------------------

_PASS_CRITERIA: list[tuple[str, str, float]] = [
    ("Profit Factor", ">=", 1.2),
    ("Win Rate [%]", ">=", 35.0),
    ("Max. Drawdown [%]", "<=", 25.0),
    ("# Trades", ">=", 30),
]


def _print_summary(symbol: str, stats: pd.Series, period_start: str, period_end: str) -> None:
    total_return = stats.get("Return [%]", float("nan"))
    win_rate = stats.get("Win Rate [%]", float("nan"))
    profit_factor = stats.get("Profit Factor", float("nan"))
    max_dd = stats.get("Max. Drawdown [%]", float("nan"))
    n_trades = int(stats.get("# Trades", 0))
    sharpe = stats.get("Sharpe Ratio", float("nan"))
    avg_trade = stats.get("Avg. Trade [%]", float("nan"))

    print(f"\n{'=' * 42}")
    print(f"  {symbol} Validation Backtest")
    print(f"{'=' * 42}")
    print(f"  Period:          {period_start}  ->  {period_end}")
    print(f"  Total Return:    {total_return:>8.2f}%")
    print(f"  Win Rate:        {win_rate:>8.2f}%")
    print(f"  Profit Factor:   {profit_factor:>8.2f}")
    print(f"  Max Drawdown:    {max_dd:>8.2f}%")
    print(f"  # Trades:        {n_trades:>8d}")
    print(f"  Sharpe Ratio:    {sharpe:>8.2f}")
    print(f"  Avg Trade:       {avg_trade:>8.2f}%")
    print()

    all_pass = True
    for metric, op, threshold in _PASS_CRITERIA:
        raw = stats.get(metric, float("nan"))
        if isinstance(raw, float) and np.isnan(raw):
            label = "N/A  "
            ok = False
        else:
            value = float(raw)
            ok = (value >= threshold) if op == ">=" else (value <= threshold)
            label = "PASS " if ok else "FAIL "
        all_pass = all_pass and ok
        print(f"  [{label}] {metric}: {float(raw):.2f}  ({op} {threshold})")

    print()
    verdict = (
        "STRATEGY HAS MERIT — proceed to demo"
        if all_pass
        else "STRATEGY NEEDS REVIEW — document in DECISIONS.md"
    )
    print(f"  VERDICT: {verdict}")
    print(f"{'=' * 42}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_backtest(symbol: str) -> None:
    logger.info("Starting validation backtest for %s", symbol)

    df_d1, df_h4 = _get_data(symbol)

    period_start = str(df_h4.index[0].date())
    period_end = str(df_h4.index[-1].date())

    df_bt = prepare_backtest_data(df_d1, df_h4)

    bt = Backtest(
        df_bt,
        DriftBacktestStrategy,
        cash=INITIAL_CASH,
        commission=COMMISSION,
        exclusive_orders=True,
    )
    stats = bt.run()

    _print_summary(symbol, stats, period_start, period_end)


def main() -> None:
    for symbol in PAIRS:
        try:
            run_backtest(symbol)
        except Exception:
            logger.exception("Backtest failed for %s", symbol)


if __name__ == "__main__":
    main()
