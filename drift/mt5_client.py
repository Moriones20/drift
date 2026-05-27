from __future__ import annotations

import logging
import time

import MetaTrader5 as mt5
import pandas as pd

from drift.config import BrokerConfig

logger = logging.getLogger(__name__)

_TIMEFRAMES: dict[str, int] = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


def connect(config: BrokerConfig) -> bool:
    if not mt5.initialize():
        logger.error("MT5 initialize failed: %s", mt5.last_error())
        return False

    if not mt5.login(login=config.login, password=config.password, server=config.server):
        logger.error("MT5 login failed: %s", mt5.last_error())
        mt5.shutdown()
        return False

    logger.info("MT5 connected — server=%s login=%d", config.server, config.login)
    return True


def disconnect() -> None:
    mt5.shutdown()
    logger.info("MT5 disconnected")


def health_check() -> bool:
    info = mt5.terminal_info()
    if info is None:
        return False
    return bool(info.connected)


def get_balance() -> float:
    account = mt5.account_info()
    if account is None:
        raise RuntimeError(f"MT5 not connected — cannot get balance: {mt5.last_error()}")
    return float(account.balance)


def reconnect(config: BrokerConfig, max_retries: int = 3, interval: int = 300) -> bool:
    disconnect()

    for attempt in range(1, max_retries + 1):
        logger.info("Reconnect attempt %d/%d", attempt, max_retries)
        if connect(config):
            return True
        if attempt < max_retries:
            logger.info("Waiting %ds before next attempt", interval)
            time.sleep(interval)

    logger.error("All %d reconnect attempts failed", max_retries)
    return False


def get_candles(symbol: str, timeframe: str, count: int = 250) -> pd.DataFrame:
    if timeframe not in _TIMEFRAMES:
        raise ValueError(f"Unknown timeframe '{timeframe}'. Valid options: {list(_TIMEFRAMES)}")

    timeframe_mt5 = _TIMEFRAMES[timeframe]
    rates = mt5.copy_rates_from_pos(symbol, timeframe_mt5, 0, count)

    if rates is None:
        raise RuntimeError(f"MT5 returned no data for {symbol} {timeframe}: {mt5.last_error()}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)

    logger.info("Fetched %d candles — symbol=%s timeframe=%s", len(df), symbol, timeframe)
    return df
