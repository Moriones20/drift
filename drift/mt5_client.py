from __future__ import annotations

import logging
import time

import MetaTrader5 as mt5

from drift.config import BrokerConfig

logger = logging.getLogger(__name__)


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
