from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import pandas as pd

from drift.config import BrokerConfig

logger = logging.getLogger(__name__)

# Short waits for the first retries — long enough for MT5 to wake after a
# PC suspend/resume, short enough to recover within seconds.  Falls back to
# the configured ``interval`` for any subsequent attempts.
_RECONNECT_EARLY_WAITS: list[int] = [5, 30]

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


def _require_account_info():
    account = mt5.account_info()
    if account is None:
        raise RuntimeError(f"MT5 not connected — cannot read account: {mt5.last_error()}")
    return account


def get_balance() -> float:
    return float(_require_account_info().balance)


def get_equity() -> float:
    return float(_require_account_info().equity)


def reconnect(
    config: BrokerConfig,
    max_retries: int = 4,
    interval: int = 300,
    shutdown_event: threading.Event | None = None,
) -> bool:
    """Attempt to reconnect to MT5 with progressive backoff.

    The first two retries use short waits (5s, 30s) to recover quickly from a
    PC suspend/resume where MT5 is still waking up and the first connect attempt
    returns "Authorization failed" transiently.  Subsequent retries use
    ``interval`` (the configured steady-state value, default 300s) to handle
    genuine broker outages without hammering the terminal.

    Uses shutdown_event.wait() instead of time.sleep() so the monitoring thread
    can be stopped cleanly without waiting for the full retry interval.
    """
    disconnect()

    for attempt in range(1, max_retries + 1):
        logger.info("Reconnect attempt %d/%d", attempt, max_retries)
        if connect(config):
            return True
        if attempt < max_retries:
            idx = attempt - 1
            wait = _RECONNECT_EARLY_WAITS[idx] if idx < len(_RECONNECT_EARLY_WAITS) else interval
            logger.info("Waiting %ds before next attempt", wait)
            if shutdown_event is not None:
                if shutdown_event.wait(timeout=wait):
                    logger.info("Shutdown requested — aborting reconnect")
                    return False
            else:
                time.sleep(wait)

    logger.error("All %d reconnect attempts failed", max_retries)
    return False


def _us_dst_active(now_utc: datetime) -> bool:
    """Return True if US daylight saving time is in effect for *now_utc*.

    US DST runs from the second Sunday of March to the first Sunday of November.
    ICMarkets' server follows US DST (GMT+3 during DST, GMT+2 otherwise), so this
    is used to pick the right whole-hour fallback when no tick is available
    (see D041).  Day-level precision is enough: the transition happens on a
    weekend when the market is closed.
    """
    year = now_utc.year
    march = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7 + 7)  # 2nd Sunday of March
    november = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = november + timedelta(days=(6 - november.weekday()) % 7)  # 1st Sunday of November
    return dst_start <= now_utc < dst_end


def get_server_utc_offset(symbol: str = "EURUSD") -> timedelta:
    """Return the MT5 server UTC offset as a fixed timedelta.

    Compares the last tick's epoch (which the MT5 server stamps in server time)
    against real UTC.  The result is rounded to the nearest hour because all
    known MT5 server offsets are whole-hour increments.

    ICMarkets' server is NY-anchored and DST-aware: GMT+2 in US winter,
    GMT+3 in US summer (see D041).  This dynamic derivation returns whichever
    is current, so it is correct year-round as long as it runs while the market
    is open.  When no tick is available (e.g. weekend, market closed) it falls
    back to a DST-aware default (GMT+3 in US summer, GMT+2 in US winter) and
    logs a warning.

    Parameters
    ----------
    symbol:
        Any actively-traded symbol whose tick is reliably available.  Defaults
        to EURUSD which is always in Market Watch for ICMarkets accounts.
    """
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        fallback = timedelta(hours=3 if _us_dst_active(datetime.now(timezone.utc)) else 2)
        logger.warning(
            "get_server_utc_offset: no tick for %s — falling back to UTC%+d (US DST %s)",
            symbol,
            int(fallback.total_seconds() // 3600),
            "on" if fallback == timedelta(hours=3) else "off",
        )
        return fallback

    # tick.time is a Unix epoch stamped by the server in its local wall time.
    # Interpreting that integer as UTC gives us the server's "apparent UTC"
    # time.  The difference between that and real UTC is the server offset.
    server_epoch_as_utc = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    real_utc = datetime.now(timezone.utc)

    raw_offset_seconds = (server_epoch_as_utc - real_utc).total_seconds()

    # Round to nearest hour to eliminate sub-second jitter and clock skew.
    rounded_hours = round(raw_offset_seconds / 3600)

    # ICMarkets' server is NY-anchored and only ever runs at GMT+2 (US winter)
    # or GMT+3 (US summer) — see D041.  A derived offset outside that set means
    # the tick is stale and the derivation is garbage:
    #   - A cold MT5 feed right after a terminal restart can return an ~8h-old
    #     tick whose apparent offset (e.g. UTC-5, raw≈-19500s) is WRONG yet sits
    #     within ±14h, so it slipped past the original stale-tick guard (D047)
    #     and poisoned the scheduler's first sleep — skipping a whole session
    #     (the D065 cold-start bug observed live 2026-06-10).
    #   - A weekend/closed-market stale tick (tens of hours off) is also caught.
    # Reject anything that is not a known broker offset and fall back to the
    # DST-aware default rather than trusting a bad derivation.  See D065 (which
    # supersedes the looser ±14h plausibility band of D047).
    if rounded_hours not in (2, 3):
        fallback = timedelta(hours=3 if _us_dst_active(real_utc) else 2)
        logger.warning(
            "get_server_utc_offset: derived UTC%+d (raw=%.1fs) is not a valid "
            "ICMarkets offset (+2/+3, D041) — stale tick? falling back to UTC%+d",
            rounded_hours,
            raw_offset_seconds,
            int(fallback.total_seconds() // 3600),
        )
        return fallback

    offset = timedelta(hours=rounded_hours)

    logger.info(
        "MT5 server offset derived: UTC%+d (raw=%.1fs)",
        rounded_hours,
        raw_offset_seconds,
    )
    return offset


def server_now(server_utc_offset: timedelta) -> datetime:
    """Return the current MT5 server time as a timezone-aware datetime.

    Parameters
    ----------
    server_utc_offset:
        The fixed offset returned by ``get_server_utc_offset``.  Pass the
        value derived once at startup rather than calling the MT5 API on every
        tick.
    """
    return datetime.now(timezone.utc) + server_utc_offset


def get_candles(symbol: str, timeframe: str, count: int = 250) -> pd.DataFrame:
    """Fetch the most recent `count` closed candles for `symbol` at `timeframe`.

    Supported timeframes: M1, M5, M15, M30, H1, H4, D1, W1, MN1.

    Warmup guidance for indicator stability:
    - M15: request at least 100 bars (ATR-14 and RSI-14 need ~14 bars of history
      to stabilize; 200 is recommended for a comfortable buffer).
    - H4: request at least 30 bars (ADX-14 needs ~28 bars). Default 250 covers
      both timeframes.

    Returns a DataFrame with a UTC-aware DatetimeIndex named 'time' and
    lowercase OHLCV columns: open, high, low, close, tick_volume.
    """
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
