from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

# Broker retcodes that are transient: the request can succeed on a later
# attempt without changing it.  Most relevant here is 10018 (market closed),
# returned during ICMarkets' ~00:00 server-time daily rollover, which falls
# inside the Daily Lull Scalper entry window (23:00-01:59 server).  See D040.
#   10004 requote · 10018 market closed · 10021 price off (no quotes)
#   10024 too many requests · 10031 no connection
_TRANSIENT_RETCODES = frozenset({10004, 10018, 10021, 10024, 10031})


def open_trade(
    pair: str,
    direction: str,
    lot_size: float,
    stop_loss: float,
    take_profit: float,
    magic: int,
    max_retries: int = 0,
    retry_delay_seconds: float = 0.0,
    guard_boundary: float | None = None,
) -> int | None:
    """Send a market order, retrying on transient broker rejections.

    On a transient retcode (see _TRANSIENT_RETCODES) the order is re-sent up to
    max_retries extra times, sleeping retry_delay_seconds between attempts and
    re-reading a fresh price each time (the market may have reopened and moved).
    Fatal retcodes (invalid stops, no money, etc.) fail immediately without
    retrying.  Defaults (0 retries) preserve single-shot behaviour.

    guard_boundary is the range edge the entry fired at (range_low for a buy,
    range_high for a sell).  On retries only, the order is abandoned if the
    market has reverted back inside the range — so a delayed fill never chases
    a degraded setup.  See D040.
    """
    symbol_info = mt5.symbol_info(pair)
    if symbol_info is None:
        logger.error("open_trade: cannot get symbol_info for %s", pair)
        return None

    digits = symbol_info.digits
    stop_loss = round(stop_loss, digits)
    take_profit = round(take_profit, digits)

    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        tick = mt5.symbol_info_tick(pair)
        if tick is None:
            logger.error("open_trade: cannot get tick for %s", pair)
            return None

        is_buy = direction.lower() == "buy"
        if is_buy:
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid

        # Price-validity guard (retries only): abandon if the market has
        # reverted off the signalled extreme.  Candle closes and the entry
        # condition are bid-based, so compare the current bid to the boundary.
        # Skipped on attempt 1 (price just fired) to avoid a spurious abandon
        # on the bid/ask spread.  See D040.
        if attempt > 1 and guard_boundary is not None:
            reverted = (is_buy and tick.bid > guard_boundary) or (
                not is_buy and tick.bid < guard_boundary
            )
            if reverted:
                logger.warning(
                    "open_trade: abandoned %s %s — price reverted off extreme "
                    "(bid=%.5f boundary=%.5f) after rollover wait",
                    direction,
                    pair,
                    tick.bid,
                    guard_boundary,
                )
                return None

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pair,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "deviation": 20,
            "magic": magic,
            "comment": "Drift",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result is None:
            logger.error(
                "open_trade: order_send returned None for %s %s, last_error=%s",
                direction,
                pair,
                mt5.last_error(),
            )
            return None

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                "open_trade: opened %s %s ticket=%d lot=%.2f price=%.5f sl=%.5f tp=%.5f",
                direction,
                pair,
                result.order,
                lot_size,
                price,
                stop_loss,
                take_profit,
            )
            return result.order

        retryable = result.retcode in _TRANSIENT_RETCODES and attempt < total_attempts
        if retryable:
            logger.warning(
                "open_trade: transient reject %s %s retcode=%d comment=%s — retry %d/%d in %.0fs",
                direction,
                pair,
                result.retcode,
                result.comment,
                attempt,
                max_retries,
                retry_delay_seconds,
            )
            if retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)
            continue

        logger.error(
            "open_trade: failed %s %s lot=%.2f sl=%.5f tp=%.5f retcode=%d comment=%s "
            "(attempt %d/%d)",
            direction,
            pair,
            lot_size,
            stop_loss,
            take_profit,
            result.retcode,
            result.comment,
            attempt,
            total_attempts,
        )
        return None

    return None


def close_trade(
    ticket: int, pair: str, lot_size: float, direction: str, magic: int = 234000
) -> bool:
    tick = mt5.symbol_info_tick(pair)
    if tick is None:
        logger.error("close_trade: cannot get tick for %s", pair)
        return False

    if direction.lower() == "buy":
        close_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pair,
        "volume": lot_size,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": magic,
        "comment": "Drift",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result is None:
        logger.error(
            "close_trade: order_send returned None for ticket=%d %s, last_error=%s",
            ticket,
            pair,
            mt5.last_error(),
        )
        return False

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            "close_trade: failed ticket=%d %s retcode=%d comment=%s",
            ticket,
            pair,
            result.retcode,
            result.comment,
        )
        return False

    logger.info(
        "close_trade: closed ticket=%d %s lot=%.2f price=%.5f",
        ticket,
        pair,
        lot_size,
        price,
    )
    return True


def modify_sl(ticket: int, pair: str, new_sl: float) -> bool:
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        logger.error("modify_sl: position not found for ticket=%d", ticket)
        return False

    position = positions[0]

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pair,
        "position": ticket,
        "sl": new_sl,
        "tp": position.tp,
    }

    result = mt5.order_send(request)

    if result is None:
        logger.error(
            "modify_sl: order_send returned None for ticket=%d, last_error=%s",
            ticket,
            mt5.last_error(),
        )
        return False

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            "modify_sl: failed ticket=%d new_sl=%.5f retcode=%d comment=%s",
            ticket,
            new_sl,
            result.retcode,
            result.comment,
        )
        return False

    logger.info("modify_sl: ticket=%d sl updated to %.5f", ticket, new_sl)
    return True


def get_open_positions(magic: int, server_offset: timedelta = timedelta(0)) -> list[dict]:
    all_positions = mt5.positions_get()
    if all_positions is None:
        logger.warning(
            "get_open_positions: positions_get returned None, last_error=%s",
            mt5.last_error(),
        )
        return []

    result = []
    for pos in all_positions:
        if pos.magic != magic:
            continue

        direction = "buy" if pos.type == 0 else "sell"
        # pos.time is a server-time epoch read as UTC; subtract the server offset
        # to get the real-UTC open instant (D039), consistent with trades/signals.
        time_open = datetime.fromtimestamp(pos.time, tz=timezone.utc) - server_offset

        result.append(
            {
                "ticket": pos.ticket,
                "pair": pos.symbol,
                "direction": direction,
                "volume": pos.volume,
                "price_open": pos.price_open,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
                "time_open": time_open,
            }
        )

    return result
