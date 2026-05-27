"""Download historical D1/H4 OHLCV data for all 6 pairs.

MT5 mode: uses mt5.copy_rates_range() if MT5 is available and connected.
Synthetic mode: generates realistic trending data when MT5 is unavailable.

Usage: python backtest/download_data.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURGBP"]

_BASE_PRICES: dict[str, float] = {
    "EURUSD": 1.08,
    "GBPUSD": 1.26,
    "USDJPY": 150.0,
    "AUDUSD": 0.65,
    "USDCAD": 1.36,
    "EURGBP": 0.86,
}

_H4_VOLATILITY: dict[str, float] = {
    "EURUSD": 0.0008,
    "GBPUSD": 0.0010,
    "USDJPY": 0.12,
    "AUDUSD": 0.0007,
    "USDCAD": 0.0009,
    "EURGBP": 0.0006,
}

_SEEDS: dict[str, int] = {
    "EURUSD": 42,
    "GBPUSD": 7,
    "USDJPY": 13,
    "AUDUSD": 99,
    "USDCAD": 55,
    "EURGBP": 21,
}


def download_mt5_data(symbol: str, timeframe: str, years: int = 2) -> pd.DataFrame | None:
    """Download historical data from MT5. Returns None if MT5 unavailable."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        logger.warning("MetaTrader5 package not installed")
        return None

    try:
        from drift.config import load_config
        from drift.mt5_client import connect, disconnect
    except Exception as exc:
        logger.warning("Could not import drift modules (%s)", exc)
        return None

    try:
        config = load_config()
    except FileNotFoundError:
        logger.warning("config.yaml not found")
        return None

    if not connect(config.broker):
        logger.warning("MT5 connection failed for %s", symbol)
        return None

    tf_map = {
        "D1": mt5.TIMEFRAME_D1,
        "H4": mt5.TIMEFRAME_H4,
    }
    mt5_tf = tf_map.get(timeframe)
    if mt5_tf is None:
        logger.error("Unknown timeframe: %s", timeframe)
        disconnect()
        return None

    date_to = datetime.now(tz=timezone.utc)
    date_from = date_to.replace(year=date_to.year - years)

    try:
        rates = mt5.copy_rates_range(symbol, mt5_tf, date_from, date_to)
    finally:
        disconnect()

    if rates is None or len(rates) == 0:
        logger.warning("No data returned from MT5 for %s %s", symbol, timeframe)
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time")
    df.index.name = "time"

    logger.info("Downloaded %d bars for %s %s from MT5", len(df), symbol, timeframe)
    return df


def generate_synthetic_data(symbol: str, years: int = 2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate synthetic D1 and H4 data with realistic alternating trend regimes."""
    logger.info("Generating synthetic data for %s", symbol)

    rng = np.random.default_rng(seed=_SEEDS[symbol])
    base_price = _BASE_PRICES[symbol]
    h4_vol = _H4_VOLATILITY[symbol]

    # 6 H4 bars per trading day, ~260 trading days per year
    n_h4 = years * 260 * 6
    idx = pd.date_range("2022-01-03", periods=n_h4, freq="4h", tz="UTC")

    closes: list[float] = [base_price]
    trend_cycle = 200
    for i in range(1, n_h4):
        phase = (i // trend_cycle) % 2
        drift = h4_vol * (0.3 if phase == 0 else -0.3)
        closes.append(max(closes[-1] + drift + rng.normal(0, h4_vol), base_price * 0.5))

    c = np.array(closes)
    spread = h4_vol * 0.5
    highs = c + np.abs(rng.normal(0, spread, n_h4))
    lows = c - np.abs(rng.normal(0, spread, n_h4))
    opens = np.roll(c, 1)
    opens[0] = c[0]
    volumes = rng.integers(500, 2000, n_h4).astype(float)

    df_h4 = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": c,
            "tick_volume": volumes,
            "spread": 2,
            "real_volume": 0,
        },
        index=idx,
    )
    df_h4.index.name = "time"

    df_d1 = (
        df_h4.resample("D")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "tick_volume": "sum",
                "spread": "last",
                "real_volume": "last",
            }
        )
        .dropna()
    )

    return df_d1, df_h4


def save_data(symbol: str, df_d1: pd.DataFrame, df_h4: pd.DataFrame, output_dir: Path) -> None:
    """Save D1 and H4 DataFrames to CSV files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    path_d1 = output_dir / f"{symbol}_D1.csv"
    path_h4 = output_dir / f"{symbol}_H4.csv"

    df_d1.to_csv(path_d1)
    df_h4.to_csv(path_h4)

    logger.info("Saved %s: D1=%d bars, H4=%d bars", symbol, len(df_d1), len(df_h4))


def load_data(symbol: str, data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load D1 and H4 data from CSV files.

    Returns (df_d1, df_h4) with UTC-aware DatetimeIndex named 'time'.
    Used by backtest scripts (Step 19+).
    """
    path_d1 = data_dir / f"{symbol}_D1.csv"
    path_h4 = data_dir / f"{symbol}_H4.csv"

    df_d1 = pd.read_csv(path_d1, index_col="time", parse_dates=True)
    df_h4 = pd.read_csv(path_h4, index_col="time", parse_dates=True)

    if df_d1.index.tz is None:
        df_d1.index = df_d1.index.tz_localize("UTC")
    if df_h4.index.tz is None:
        df_h4.index = df_h4.index.tz_localize("UTC")

    return df_d1, df_h4


def download_all(pairs: list[str], output_dir: Path, years: int = 2) -> None:
    """Download or generate data for all pairs and save to CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)

    for symbol in pairs:
        print(f"Processing {symbol}...")

        df_d1_mt5 = download_mt5_data(symbol, "D1", years)
        df_h4_mt5 = download_mt5_data(symbol, "H4", years)

        if df_d1_mt5 is not None and df_h4_mt5 is not None:
            logger.info("Using MT5 data for %s", symbol)
            df_d1 = df_d1_mt5
            df_h4 = df_h4_mt5
        else:
            logger.info("Falling back to synthetic data for %s", symbol)
            df_d1, df_h4 = generate_synthetic_data(symbol, years)

        save_data(symbol, df_d1, df_h4, output_dir)

    print(f"\nDone. Data saved to {output_dir}")


if __name__ == "__main__":
    pairs = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURGBP"]
    output_dir = Path(__file__).parent / "data"
    download_all(pairs, output_dir)
