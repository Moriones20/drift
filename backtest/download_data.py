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

PAIRS = ["AUDCAD", "NZDCAD", "AUDNZD", "EURCHF", "EURGBP"]

LULL_PAIRS = ["EURCHF", "EURGBP", "AUDNZD", "USDJPY", "GBPJPY", "EURJPY"]

_BASE_PRICES: dict[str, float] = {
    "AUDCAD": 0.90,
    "NZDCAD": 0.82,
    "AUDNZD": 1.08,
    "EURCHF": 0.95,
    "EURGBP": 0.86,
}

_BASE_PRICES_LULL: dict[str, float] = {
    "EURCHF": 0.95,
    "EURGBP": 0.86,
    "AUDNZD": 1.08,
    "USDJPY": 150.0,
    "GBPJPY": 190.0,
    "EURJPY": 162.0,
}

_H4_VOLATILITY: dict[str, float] = {
    "AUDCAD": 0.0007,
    "NZDCAD": 0.0007,
    "AUDNZD": 0.0006,
    "EURCHF": 0.0005,
    "EURGBP": 0.0005,
}

_M15_VOLATILITY: dict[str, float] = {
    "EURCHF": 0.00012,
    "EURGBP": 0.00012,
    "AUDNZD": 0.00015,
    "USDJPY": 0.015,
    "GBPJPY": 0.020,
    "EURJPY": 0.018,
}

_SEEDS: dict[str, int] = {
    "AUDCAD": 11,
    "NZDCAD": 22,
    "AUDNZD": 33,
    "EURCHF": 44,
    "EURGBP": 55,
    "USDJPY": 66,
    "GBPJPY": 77,
    "EURJPY": 88,
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
        "M15": mt5.TIMEFRAME_M15,
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


def generate_synthetic_m15(symbol: str, years: int = 2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate synthetic H4 and M15 data for Daily Lull pairs.

    Creates M15 bars by subdividing each H4 bar into 16 M15 bars using linear
    interpolation of the price path with proportional noise.  The H4 frame is
    also returned so callers have both timeframes from a single call.
    """
    logger.info("Generating synthetic M15 data for %s", symbol)

    rng = np.random.default_rng(seed=_SEEDS[symbol])
    base_price = _BASE_PRICES_LULL[symbol]
    m15_vol = _M15_VOLATILITY[symbol]
    h4_vol = m15_vol * 4  # approximate H4 volatility from M15 vol

    n_h4 = years * 260 * 6
    idx_h4 = pd.date_range("2022-01-03", periods=n_h4, freq="4h", tz="UTC")

    closes_h4: list[float] = [base_price]
    trend_cycle = 200
    for i in range(1, n_h4):
        phase = (i // trend_cycle) % 2
        drift = h4_vol * (0.3 if phase == 0 else -0.3)
        closes_h4.append(max(closes_h4[-1] + drift + rng.normal(0, h4_vol), base_price * 0.5))

    c_h4 = np.array(closes_h4)
    spread_h4 = h4_vol * 0.5
    highs_h4 = c_h4 + np.abs(rng.normal(0, spread_h4, n_h4))
    lows_h4 = c_h4 - np.abs(rng.normal(0, spread_h4, n_h4))
    opens_h4 = np.roll(c_h4, 1)
    opens_h4[0] = c_h4[0]
    vols_h4 = rng.integers(500, 2000, n_h4).astype(float)

    df_h4 = pd.DataFrame(
        {
            "open": opens_h4,
            "high": highs_h4,
            "low": lows_h4,
            "close": c_h4,
            "tick_volume": vols_h4,
            "spread": 2,
            "real_volume": 0,
        },
        index=idx_h4,
    )
    df_h4.index.name = "time"

    # Build M15 bars: 16 per H4 bar via linear interpolation + noise
    bars_per_h4 = 16
    m15_rows: list[dict] = []

    for i in range(n_h4):
        h4_open = opens_h4[i]
        h4_close = c_h4[i]
        h4_high = highs_h4[i]
        h4_low = lows_h4[i]
        h4_vol_bar = vols_h4[i]
        ts_start = idx_h4[i]

        # Linear path from h4_open to h4_close with noise
        path = np.linspace(h4_open, h4_close, bars_per_h4 + 1)
        noise = rng.normal(0, m15_vol, bars_per_h4)
        m15_closes = path[1:] + noise

        # Clamp individual bar closes so they respect the H4 high/low envelope
        m15_closes = np.clip(m15_closes, h4_low, h4_high)

        m15_opens = np.empty(bars_per_h4)
        m15_opens[0] = h4_open
        m15_opens[1:] = m15_closes[:-1]

        bar_range = np.abs(m15_closes - m15_opens)
        wick = bar_range * 0.4
        wick_noise = rng.normal(0, wick + m15_vol * 0.1, bars_per_h4)
        m15_highs = np.maximum(m15_opens, m15_closes) + np.abs(wick_noise)
        m15_lows = np.minimum(m15_opens, m15_closes) - np.abs(wick_noise)

        # Clamp to H4 envelope
        m15_highs = np.clip(m15_highs, h4_low, h4_high * 1.001)
        m15_lows = np.clip(m15_lows, h4_low * 0.999, h4_high)

        m15_vols = rng.integers(20, 150, bars_per_h4).astype(float)
        m15_vols = m15_vols / m15_vols.sum() * h4_vol_bar

        for j in range(bars_per_h4):
            m15_rows.append(
                {
                    "time": ts_start + pd.Timedelta(minutes=15 * j),
                    "open": float(m15_opens[j]),
                    "high": float(m15_highs[j]),
                    "low": float(m15_lows[j]),
                    "close": float(m15_closes[j]),
                    "tick_volume": float(m15_vols[j]),
                    "spread": 2,
                    "real_volume": 0,
                }
            )

    df_m15 = pd.DataFrame(m15_rows).set_index("time")
    df_m15.index = pd.DatetimeIndex(df_m15.index, tz="UTC")
    df_m15.index.name = "time"

    logger.info(
        "Generated synthetic M15 data for %s: H4=%d bars, M15=%d bars",
        symbol,
        len(df_h4),
        len(df_m15),
    )
    return df_h4, df_m15


def save_data(symbol: str, df_d1: pd.DataFrame, df_h4: pd.DataFrame, output_dir: Path) -> None:
    """Save D1 and H4 DataFrames to CSV files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    path_d1 = output_dir / f"{symbol}_D1.csv"
    path_h4 = output_dir / f"{symbol}_H4.csv"

    df_d1.to_csv(path_d1)
    df_h4.to_csv(path_h4)

    logger.info("Saved %s: D1=%d bars, H4=%d bars", symbol, len(df_d1), len(df_h4))


def save_lull_data(
    symbol: str, df_h4: pd.DataFrame, df_m15: pd.DataFrame, output_dir: Path
) -> None:
    """Save H4 and M15 DataFrames for an Daily Lull pair to CSV files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    path_h4 = output_dir / f"{symbol}_H4.csv"
    path_m15 = output_dir / f"{symbol}_M15.csv"

    df_h4.to_csv(path_h4)
    df_m15.to_csv(path_m15)

    logger.info("Saved lull %s: H4=%d bars, M15=%d bars", symbol, len(df_h4), len(df_m15))


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


def load_lull_data(symbol: str, data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load H4 and M15 data for an Daily Lull pair from CSV files.

    Returns (df_h4, df_m15) with UTC-aware DatetimeIndex named 'time'.
    """
    path_h4 = data_dir / f"{symbol}_H4.csv"
    path_m15 = data_dir / f"{symbol}_M15.csv"

    df_h4 = pd.read_csv(path_h4, index_col="time", parse_dates=True)
    df_m15 = pd.read_csv(path_m15, index_col="time", parse_dates=True)

    if df_h4.index.tz is None:
        df_h4.index = df_h4.index.tz_localize("UTC")
    if df_m15.index.tz is None:
        df_m15.index = df_m15.index.tz_localize("UTC")

    return df_h4, df_m15


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


def download_lull_all(pairs: list[str], output_dir: Path, years: int = 2) -> None:
    """Download or generate H4 and M15 data for all Daily Lull pairs and save to CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)

    for symbol in pairs:
        print(f"Processing lull pair {symbol}...")

        df_h4_mt5 = download_mt5_data(symbol, "H4", years)
        df_m15_mt5 = download_mt5_data(symbol, "M15", years)

        if df_h4_mt5 is not None and df_m15_mt5 is not None:
            logger.info("Using MT5 data for %s", symbol)
            df_h4 = df_h4_mt5
            df_m15 = df_m15_mt5
        else:
            logger.info("Falling back to synthetic M15 data for %s", symbol)
            df_h4, df_m15 = generate_synthetic_m15(symbol, years)

        save_lull_data(symbol, df_h4, df_m15, output_dir)

    print(f"\nDone. lull data saved to {output_dir}")


if __name__ == "__main__":
    pairs = ["AUDCAD", "NZDCAD", "AUDNZD", "EURCHF", "EURGBP"]
    output_dir = Path(__file__).parent / "data"
    download_all(pairs, output_dir)
